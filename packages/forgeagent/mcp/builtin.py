"""Built-in MCP tools for repository, filesystem, git, issues, tests, analysis."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

from forgeagent.execution.sandbox import ExecutionRequest, SandboxExecutor
from forgeagent.mcp import MCPRegistry, MCPTool, ToolContext, ToolResult
from forgeagent.repository.intelligence import RepositoryIntelligence
from forgeagent.security import resolve_under_root


def _workspace(ctx: ToolContext) -> Path:
    return Path(ctx.workspace_root).resolve()


def register_builtin_tools(
    registry: MCPRegistry,
    *,
    executor: SandboxExecutor,
) -> None:
    """Register the standard ForgeAgent MCP tool set."""

    async def repo_tree(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        intel = RepositoryIntelligence(_workspace(ctx))
        tree = intel.file_tree(
            max_depth=int(args.get("max_depth", 4)),
            max_entries=int(args.get("max_entries", 200)),
        )
        return ToolResult(success=True, data={"tree": tree})

    async def repo_search_text(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        intel = RepositoryIntelligence(_workspace(ctx))
        hits = intel.search_text(
            query=args["query"],
            glob=args.get("glob"),
            max_hits=int(args.get("max_hits", 30)),
        )
        return ToolResult(success=True, data={"hits": hits})

    async def repo_search_filename(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        intel = RepositoryIntelligence(_workspace(ctx))
        hits = intel.search_filename(args["query"], max_hits=int(args.get("max_hits", 30)))
        return ToolResult(success=True, data={"hits": hits})

    async def repo_search_symbol(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        intel = RepositoryIntelligence(_workspace(ctx))
        hits = intel.search_symbol(args["symbol"], max_hits=int(args.get("max_hits", 30)))
        return ToolResult(success=True, data={"hits": hits})

    async def repo_select_relevant(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        intel = RepositoryIntelligence(_workspace(ctx))
        selected = intel.select_relevant_files(
            issue_text=args["issue_text"],
            budget_tokens=int(args.get("budget_tokens", 8000)),
        )
        return ToolResult(success=True, data={"selected": selected})

    async def repo_discover_tests(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        intel = RepositoryIntelligence(_workspace(ctx))
        return ToolResult(success=True, data={"tests": intel.discover_tests()})

    async def repo_dependencies(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        intel = RepositoryIntelligence(_workspace(ctx))
        return ToolResult(success=True, data={"dependencies": intel.inspect_dependencies()})

    async def fs_read(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        path = resolve_under_root(_workspace(ctx), args["path"])
        if not path.is_file():
            return ToolResult(success=False, error="File not found", error_type="not_found")
        max_bytes = int(args.get("max_bytes", 100_000))
        data = path.read_bytes()[:max_bytes]
        text = data.decode("utf-8", errors="replace")
        return ToolResult(
            success=True,
            data={
                "path": args["path"],
                "content": text,
                "truncated": len(path.read_bytes()) > max_bytes,
            },
        )

    async def fs_write(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        path = resolve_under_root(_workspace(ctx), args["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(args["content"], encoding="utf-8")
        return ToolResult(success=True, data={"path": args["path"], "bytes": len(args["content"])})

    async def fs_list(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        path = resolve_under_root(_workspace(ctx), args.get("path") or ".")
        if not path.exists():
            return ToolResult(success=False, error="Path not found", error_type="not_found")
        entries = []
        for child in sorted(path.iterdir())[:500]:
            entries.append(
                {
                    "name": child.name,
                    "type": "dir" if child.is_dir() else "file",
                    "size": child.stat().st_size if child.is_file() else None,
                }
            )
        return ToolResult(success=True, data={"entries": entries})

    async def git_status(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        result = await executor.run(
            ExecutionRequest(
                command=["git", "status", "--porcelain"],
                workdir=str(_workspace(ctx)),
                timeout_seconds=30,
            )
        )
        return ToolResult(
            success=result.exit_code == 0,
            data={"stdout": result.stdout, "stderr": result.stderr},
            error=None if result.exit_code == 0 else result.stderr or "git status failed",
        )

    async def git_diff(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        cmd = ["git", "diff"]
        if args.get("staged"):
            cmd.append("--staged")
        if args.get("path"):
            cmd.extend(["--", args["path"]])
        result = await executor.run(
            ExecutionRequest(command=cmd, workdir=str(_workspace(ctx)), timeout_seconds=60)
        )
        return ToolResult(
            success=result.exit_code == 0,
            data={"diff": result.stdout, "stderr": result.stderr},
            error=None if result.exit_code == 0 else result.stderr or "git diff failed",
        )

    async def git_checkout(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        commit = args["commit"]
        if not re.fullmatch(r"[0-9a-fA-F]{7,40}", commit):
            return ToolResult(
                success=False,
                error="Invalid commit hash",
                error_type="validation_error",
            )
        result = await executor.run(
            ExecutionRequest(
                command=["git", "checkout", commit],
                workdir=str(_workspace(ctx)),
                timeout_seconds=60,
            )
        )
        return ToolResult(
            success=result.exit_code == 0,
            data={"stdout": result.stdout, "stderr": result.stderr},
            error=None if result.exit_code == 0 else result.stderr or "checkout failed",
        )

    async def issue_get(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        # Local/fixture issues live under .forge/issues/{id}.json; GitHub via metadata.
        issue_id = args["issue_id"]
        local = _workspace(ctx) / ".forge" / "issues" / f"{issue_id}.json"
        if local.is_file():
            return ToolResult(success=True, data=json.loads(local.read_text(encoding="utf-8")))
        meta = ctx.metadata.get("issue")
        if isinstance(meta, dict):
            return ToolResult(success=True, data=meta)
        return ToolResult(
            success=False,
            error=f"Issue not found: {issue_id}",
            error_type="not_found",
        )

    async def test_run(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        command = args.get("command")
        if isinstance(command, list):
            cmd = [str(c) for c in command]
        elif isinstance(command, str) and command.strip():
            # Prefer argv JSON arrays; split only on whitespace for simple commands.
            cmd = command.split()
        else:
            cmd = ["python", "-m", "pytest", "-q"]
        timeout = float(args.get("timeout_seconds", 300))
        result = await executor.run(
            ExecutionRequest(
                command=cmd,
                workdir=str(_workspace(ctx)),
                timeout_seconds=timeout,
            )
        )
        return ToolResult(
            success=result.exit_code == 0 and not result.timed_out,
            data={
                "exit_code": result.exit_code,
                "stdout": result.stdout[-50_000:],
                "stderr": result.stderr[-50_000:],
                "duration_ms": result.duration_ms,
                "timed_out": result.timed_out,
                "command": cmd,
                "backend": result.backend,
            },
            error=None
            if result.exit_code == 0 and not result.timed_out
            else "Tests failed or timed out",
        )

    async def static_analyze(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        path = resolve_under_root(_workspace(ctx), args["path"])
        if not path.is_file():
            return ToolResult(success=False, error="File not found", error_type="not_found")
        text = path.read_text(encoding="utf-8", errors="replace")
        findings: list[dict[str, Any]] = []
        if path.suffix == ".py":
            try:
                ast.parse(text)
            except SyntaxError as exc:
                findings.append(
                    {
                        "severity": "error",
                        "message": f"SyntaxError: {exc.msg}",
                        "line": exc.lineno,
                    }
                )
            if "eval(" in text or "exec(" in text:
                findings.append(
                    {
                        "severity": "warning",
                        "message": "Use of eval/exec detected",
                        "line": None,
                    }
                )
        return ToolResult(success=True, data={"path": args["path"], "findings": findings})

    async def docs_search(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        intel = RepositoryIntelligence(_workspace(ctx))
        hits = intel.search_text(
            query=args["query"],
            glob=args.get("glob") or "*.md",
            max_hits=int(args.get("max_hits", 20)),
        )
        return ToolResult(success=True, data={"hits": hits})

    async def inspect_structure(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        path = resolve_under_root(_workspace(ctx), args["path"])
        if not path.is_file() or path.suffix != ".py":
            return ToolResult(
                success=False,
                error="Only Python files supported for structured inspection",
                error_type="validation_error",
            )
        source = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            return ToolResult(success=False, error=str(exc), error_type="parse_error")
        symbols = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.append({"kind": "function", "name": node.name, "lineno": node.lineno})
            elif isinstance(node, ast.ClassDef):
                methods = [
                    n.name
                    for n in node.body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                ]
                symbols.append(
                    {
                        "kind": "class",
                        "name": node.name,
                        "lineno": node.lineno,
                        "methods": methods,
                    }
                )
        return ToolResult(success=True, data={"path": args["path"], "symbols": symbols})

    tools: list[MCPTool] = [
        MCPTool(
            name="repo.tree",
            description="List repository file tree with depth limits.",
            input_schema={
                "type": "object",
                "properties": {
                    "max_depth": {"type": "integer", "minimum": 1, "maximum": 10},
                    "max_entries": {"type": "integer", "minimum": 1, "maximum": 2000},
                },
                "additionalProperties": False,
            },
            handler=repo_tree,
            tags=["repository"],
        ),
        MCPTool(
            name="repo.search_text",
            description="Exact/substring text search across the repository.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1},
                    "glob": {"type": "string"},
                    "max_hits": {"type": "integer", "minimum": 1, "maximum": 200},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=repo_search_text,
            tags=["repository", "search"],
        ),
        MCPTool(
            name="repo.search_filename",
            description="Search files by filename substring.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1},
                    "max_hits": {"type": "integer", "minimum": 1, "maximum": 200},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=repo_search_filename,
            tags=["repository", "search"],
        ),
        MCPTool(
            name="repo.search_symbol",
            description="Search for function/class symbol definitions (Python-aware).",
            input_schema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "minLength": 1},
                    "max_hits": {"type": "integer", "minimum": 1, "maximum": 200},
                },
                "required": ["symbol"],
                "additionalProperties": False,
            },
            handler=repo_search_symbol,
            tags=["repository", "search"],
        ),
        MCPTool(
            name="repo.select_relevant",
            description="Select relevant files for an issue within a token budget.",
            input_schema={
                "type": "object",
                "properties": {
                    "issue_text": {"type": "string", "minLength": 1},
                    "budget_tokens": {"type": "integer", "minimum": 500, "maximum": 200000},
                },
                "required": ["issue_text"],
                "additionalProperties": False,
            },
            handler=repo_select_relevant,
            tags=["repository"],
        ),
        MCPTool(
            name="repo.discover_tests",
            description="Discover test files and likely test commands.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            handler=repo_discover_tests,
            tags=["repository", "test"],
        ),
        MCPTool(
            name="repo.dependencies",
            description="Inspect dependency manifests (pyproject, requirements, package.json).",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            handler=repo_dependencies,
            tags=["repository"],
        ),
        MCPTool(
            name="fs.read",
            description="Read a file relative to the workspace root.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "minLength": 1},
                    "max_bytes": {"type": "integer", "minimum": 1, "maximum": 2_000_000},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            handler=fs_read,
            tags=["filesystem"],
        ),
        MCPTool(
            name="fs.write",
            description="Write a file relative to the workspace root.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "minLength": 1},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            handler=fs_write,
            tags=["filesystem"],
        ),
        MCPTool(
            name="fs.list",
            description="List directory entries relative to the workspace root.",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "additionalProperties": False,
            },
            handler=fs_list,
            tags=["filesystem"],
        ),
        MCPTool(
            name="git.status",
            description="Show git status (porcelain).",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            handler=git_status,
            tags=["git"],
        ),
        MCPTool(
            name="git.diff",
            description="Show git diff.",
            input_schema={
                "type": "object",
                "properties": {
                    "staged": {"type": "boolean"},
                    "path": {"type": "string"},
                },
                "additionalProperties": False,
            },
            handler=git_diff,
            tags=["git"],
        ),
        MCPTool(
            name="git.checkout",
            description="Checkout a specific commit (hash only).",
            input_schema={
                "type": "object",
                "properties": {"commit": {"type": "string", "minLength": 7}},
                "required": ["commit"],
                "additionalProperties": False,
            },
            handler=git_checkout,
            tags=["git"],
        ),
        MCPTool(
            name="issue.get",
            description="Retrieve issue details by id (local fixture or context).",
            input_schema={
                "type": "object",
                "properties": {"issue_id": {"type": "string", "minLength": 1}},
                "required": ["issue_id"],
                "additionalProperties": False,
            },
            handler=issue_get,
            tags=["issue"],
        ),
        MCPTool(
            name="test.run",
            description="Run tests inside the sandbox. Prefer argv list for command.",
            input_schema={
                "type": "object",
                "properties": {
                    "command": {
                        "oneOf": [
                            {"type": "array", "items": {"type": "string"}, "minItems": 1},
                            {"type": "string"},
                        ]
                    },
                    "timeout_seconds": {"type": "number", "minimum": 1, "maximum": 3600},
                },
                "additionalProperties": False,
            },
            handler=test_run,
            tags=["test"],
            timeout_seconds=360,
        ),
        MCPTool(
            name="static.analyze",
            description="Run lightweight static analysis on a file.",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string", "minLength": 1}},
                "required": ["path"],
                "additionalProperties": False,
            },
            handler=static_analyze,
            tags=["static"],
        ),
        MCPTool(
            name="docs.search",
            description="Search documentation files (markdown by default).",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1},
                    "glob": {"type": "string"},
                    "max_hits": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=docs_search,
            tags=["docs"],
        ),
        MCPTool(
            name="inspect.structure",
            description="Structured inspection of Python module symbols.",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string", "minLength": 1}},
                "required": ["path"],
                "additionalProperties": False,
            },
            handler=inspect_structure,
            tags=["inspect"],
        ),
    ]

    for tool in tools:
        registry.register(tool)
