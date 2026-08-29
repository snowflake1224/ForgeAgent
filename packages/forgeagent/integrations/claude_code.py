"""Claude Code / external coding-assistant integration path.

This is not a fake local wrapper pretending to be Claude Code.
When enabled, ForgeAgent can delegate the *coding* phase to an installed
Claude Code CLI (or compatible agentic coding assistant) as an external
backend.

Requirements
------------
1. Claude Code (or compatible CLI) installed and authenticated on the host.
2. FORGE_CLAUDE_CODE_ENABLED=true
3. FORGE_CLAUDE_CODE_COMMAND pointing at the binary (default: ``claude``)

Security
--------
The external assistant still operates on a workspace that should be prepared
inside the sandbox/worktree. ForgeAgent does not grant the assistant unrestricted
host access beyond the configured working directory. Operators must review
Claude Code's own sandboxing/permissions model.

Protocol
--------
ForgeAgent writes an auditable prompt file and invokes the installed CLI in
non-interactive print mode::

    {command} --print -p "<prompt>"

Exact flags may differ by Claude Code version; see
docs/claude-code-integration.md.
The adapter captures stdout/stderr and expects the assistant to modify files
in-place in the workdir. ForgeAgent then continues with test/review phases.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from pathlib import Path

from forgeagent.config import Settings, get_settings


@dataclass
class ExternalCodingResult:
    success: bool
    stdout: str
    stderr: str
    exit_code: int
    backend: str = "claude_code"


class ClaudeCodeBackend:
    """Adapter for Anthropic Claude Code CLI as an external coding agent."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @property
    def available(self) -> bool:
        if not self.settings.claude_code_enabled:
            return False
        return shutil.which(self.settings.claude_code_command) is not None

    async def run_coding_task(
        self,
        *,
        workdir: Path,
        issue_title: str,
        issue_body: str,
        plan: str,
    ) -> ExternalCodingResult:
        if not self.settings.claude_code_enabled:
            return ExternalCodingResult(
                success=False,
                stdout="",
                stderr="Claude Code backend disabled (FORGE_CLAUDE_CODE_ENABLED=false)",
                exit_code=2,
            )
        if not self.available:
            return ExternalCodingResult(
                success=False,
                stdout="",
                stderr=(
                    f"Claude Code command not found: {self.settings.claude_code_command}. "
                    "Install Claude Code and authenticate before enabling this backend."
                ),
                exit_code=127,
            )

        prompt = (
            "You are operating as the coding agent for ForgeAgent.\n"
            "Apply a minimal patch to resolve the issue. Do not commit or push.\n\n"
            f"# Issue\n{issue_title}\n\n{issue_body}\n\n"
            f"# Plan\n{plan}\n"
        )
        prompt_path = workdir / ".forge" / "claude_code_prompt.md"
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(prompt, encoding="utf-8")

        cmd = [
            self.settings.claude_code_command,
            "--print",
            "-p",
            prompt,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(workdir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await proc.communicate()
        code = int(proc.returncode or 0)
        return ExternalCodingResult(
            success=code == 0,
            stdout=stdout_b.decode("utf-8", errors="replace"),
            stderr=stderr_b.decode("utf-8", errors="replace"),
            exit_code=code,
        )
