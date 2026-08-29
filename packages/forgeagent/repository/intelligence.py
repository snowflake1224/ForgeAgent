"""Repository intelligence — targeted context, not whole-repo dumps."""

from __future__ import annotations

import ast
import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".tox",
    ".forge",
}

TEXT_EXTENSIONS = {
    ".py",
    ".md",
    ".txt",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".rs",
    ".go",
    ".java",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".cs",
    ".rb",
    ".php",
    ".sh",
    ".ini",
    ".cfg",
}


@dataclass
class FileHit:
    path: str
    score: float
    reason: str
    preview: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "path": self.path,
            "score": f"{self.score:.3f}",
            "reason": self.reason,
            "preview": self.preview[:200],
        }


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class RepositoryIntelligence:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _iter_files(self, glob: str | None = None) -> list[Path]:
        files: list[Path] = []
        for path in self.root.rglob("*"):
            if not path.is_file():
                continue
            rel_parts = path.relative_to(self.root).parts
            if any(part in SKIP_DIRS for part in rel_parts):
                continue
            if path.suffix and path.suffix.lower() not in TEXT_EXTENSIONS and glob is None:
                continue
            if glob and not fnmatch.fnmatch(path.name, glob) and not fnmatch.fnmatch(
                str(path.relative_to(self.root)).replace("\\", "/"), glob
            ):
                continue
            files.append(path)
        return files

    def file_tree(self, *, max_depth: int = 4, max_entries: int = 200) -> list[str]:
        entries: list[str] = []
        for path in sorted(self.root.rglob("*")):
            if len(entries) >= max_entries:
                entries.append("... truncated ...")
                break
            rel = path.relative_to(self.root)
            if any(part in SKIP_DIRS for part in rel.parts):
                continue
            if len(rel.parts) > max_depth:
                continue
            suffix = "/" if path.is_dir() else ""
            entries.append(str(rel).replace("\\", "/") + suffix)
        return entries

    def search_text(
        self,
        query: str,
        *,
        glob: str | None = None,
        max_hits: int = 30,
    ) -> list[dict[str, Any]]:
        hits: list[dict[str, Any]] = []
        q = query.lower()
        for path in self._iter_files(glob):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), start=1):
                if q in line.lower():
                    hits.append(
                        {
                            "path": str(path.relative_to(self.root)).replace("\\", "/"),
                            "line": i,
                            "text": line.strip()[:240],
                        }
                    )
                    if len(hits) >= max_hits:
                        return hits
        return hits

    def search_filename(self, query: str, *, max_hits: int = 30) -> list[dict[str, Any]]:
        q = query.lower()
        hits: list[dict[str, Any]] = []
        for path in self._iter_files():
            if q in path.name.lower():
                hits.append({"path": str(path.relative_to(self.root)).replace("\\", "/")})
                if len(hits) >= max_hits:
                    break
        return hits

    def search_symbol(self, symbol: str, *, max_hits: int = 30) -> list[dict[str, Any]]:
        hits: list[dict[str, Any]] = []
        pattern = re.compile(
            rf"^\s*(?:async\s+)?(?:def|class)\s+{re.escape(symbol)}\b",
            re.MULTILINE,
        )
        for path in self._iter_files("*.py"):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for match in pattern.finditer(text):
                line = text[: match.start()].count("\n") + 1
                hits.append(
                    {
                        "path": str(path.relative_to(self.root)).replace("\\", "/"),
                        "line": line,
                        "symbol": symbol,
                    }
                )
                if len(hits) >= max_hits:
                    return hits
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                ) and node.name == symbol:
                    hits.append(
                        {
                            "path": str(path.relative_to(self.root)).replace("\\", "/"),
                            "line": getattr(node, "lineno", 0),
                            "symbol": symbol,
                        }
                    )
                    if len(hits) >= max_hits:
                        return hits
        seen: set[tuple[str, int]] = set()
        unique: list[dict[str, Any]] = []
        for h in hits:
            key = (h["path"], int(h["line"]))
            if key in seen:
                continue
            seen.add(key)
            unique.append(h)
        return unique[:max_hits]

    def discover_tests(self) -> dict[str, Any]:
        test_files = []
        for path in self._iter_files():
            rel = str(path.relative_to(self.root)).replace("\\", "/")
            name = path.name
            if (
                name.startswith("test_")
                or name.endswith("_test.py")
                or "/tests/" in f"/{rel}/"
                or rel.startswith("tests/")
            ):
                test_files.append(rel)
        commands: list[list[str]] = []
        if (self.root / "pyproject.toml").exists() or any(
            True for _ in self.root.glob("**/test_*.py")
        ):
            commands.append(["python", "-m", "pytest", "-q"])
        if (self.root / "package.json").exists():
            commands.append(["npm", "test", "--silent"])
        return {"test_files": test_files[:100], "suggested_commands": commands}

    def inspect_dependencies(self) -> dict[str, Any]:
        deps: dict[str, Any] = {}
        pyproject = self.root / "pyproject.toml"
        if pyproject.exists():
            deps["pyproject.toml"] = pyproject.read_text(encoding="utf-8", errors="replace")[:4000]
        req = self.root / "requirements.txt"
        if req.exists():
            deps["requirements.txt"] = req.read_text(encoding="utf-8", errors="replace")[:4000]
        pkg = self.root / "package.json"
        if pkg.exists():
            deps["package.json"] = pkg.read_text(encoding="utf-8", errors="replace")[:4000]
        return deps

    def select_relevant_files(
        self,
        issue_text: str,
        *,
        budget_tokens: int = 8000,
    ) -> list[dict[str, str]]:
        tokens = {t.lower() for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", issue_text)}
        scored: list[FileHit] = []
        for path in self._iter_files():
            rel = str(path.relative_to(self.root)).replace("\\", "/")
            name_score = sum(1 for t in tokens if t in path.name.lower())
            path_score = sum(1 for t in tokens if t in rel.lower())
            content_score = 0
            preview = ""
            try:
                text = path.read_text(encoding="utf-8", errors="replace")[:8000]
                preview = text[:200]
                lower = text.lower()
                content_score = sum(1 for t in tokens if t in lower)
            except OSError:
                continue
            total = name_score * 3 + path_score * 2 + content_score
            if total <= 0:
                continue
            reason_parts = []
            if name_score:
                reason_parts.append("filename_keyword")
            if path_score:
                reason_parts.append("path_keyword")
            if content_score:
                reason_parts.append("content_keyword")
            scored.append(
                FileHit(
                    path=rel,
                    score=float(total),
                    reason="+".join(reason_parts) or "heuristic",
                    preview=preview,
                )
            )
        scored.sort(key=lambda h: h.score, reverse=True)
        selected: list[dict[str, str]] = []
        used = 0
        for hit in scored:
            try:
                text = (self.root / hit.path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            cost = estimate_tokens(text)
            if selected and used + cost > budget_tokens:
                continue
            used += cost
            selected.append(hit.as_dict())
            if used >= budget_tokens:
                break
        return selected
