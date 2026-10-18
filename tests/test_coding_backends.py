from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from forgeagent.config import Settings
from forgeagent.execution import LocalProcessExecutor
from forgeagent.integrations.claude_code import ClaudeCodeBackend, ExternalCodingResult
from forgeagent.mcp import MCPRegistry
from forgeagent.mcp.builtin import register_builtin_tools
from forgeagent.models import LLMProvider, LLMResponse, LLMUsage, Message, ToolSpec
from forgeagent.security import ToolPolicy
from forgeagent.skills.runtime import SkillRuntime


class StructuredEditProvider(LLMProvider):
    name = "test-structured"
    model = "test-structured"

    async def complete(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        del tools, temperature, max_tokens
        context = messages[-1].content
        assert "greet.py" in context
        return LLMResponse(
            content=json.dumps(
                {
                    "summary": "Correct the greeting.",
                    "edits": [
                        {
                            "path": "src/greet.py",
                            "content": 'def greet() -> str:\n    return "hello"\n',
                        }
                    ],
                }
            ),
            usage=LLMUsage(prompt_tokens=10, completion_tokens=10),
            model=self.model,
        )


class FakeClaudeCodeBackend(ClaudeCodeBackend):
    async def run_coding_task(
        self,
        *,
        workdir: Path,
        issue_title: str,
        issue_body: str,
        plan: str,
    ) -> ExternalCodingResult:
        del issue_title, issue_body, plan
        (workdir / "src" / "value.py").write_text("VALUE = 2\n", encoding="utf-8")
        return ExternalCodingResult(
            success=True,
            stdout="patched value.py",
            stderr="",
            exit_code=0,
        )


def _runtime(
    workspace: Path,
    settings: Settings,
    provider: LLMProvider,
    *,
    claude_backend: ClaudeCodeBackend | None = None,
) -> SkillRuntime:
    registry = MCPRegistry(policy=ToolPolicy(settings))
    register_builtin_tools(registry, executor=LocalProcessExecutor())
    return SkillRuntime(
        registry,
        provider,
        workspace_root=str(workspace),
        run_id="run_backend_test",
        settings=settings,
        claude_code_backend=claude_backend,
    )


@pytest.mark.asyncio
async def test_model_backend_applies_provider_structured_edits(tmp_path: Path) -> None:
    source = tmp_path / "src" / "greet.py"
    source.parent.mkdir()
    source.write_text('def greet() -> str:\n    return "goodbye"\n', encoding="utf-8")
    settings = Settings(
        env="test",
        coding_backend="model",
        sandbox_backend="local",
        tool_policy_default="allow",
        otel_enabled=False,
        metrics_enabled=False,
        data_dir=tmp_path / "data",
        artifacts_dir=tmp_path / "artifacts",
        runs_dir=tmp_path / "runs",
    )

    result = await _runtime(tmp_path, settings, StructuredEditProvider()).run_implementation(
        plan="Correct the greeting",
        selected_files=[{"path": "src/greet.py"}],
        issue_text="greet returns the wrong value",
    )

    assert result.success
    assert result.outputs["backend"] == "model"
    assert result.outputs["files_modified"] == ["src/greet.py"]
    assert 'return "hello"' in source.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_claude_code_backend_is_used_by_implementation_skill(tmp_path: Path) -> None:
    source = tmp_path / "src" / "value.py"
    source.parent.mkdir()
    source.write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=ForgeAgent Test",
            "-c",
            "user.email=forgeagent@example.invalid",
            "commit",
            "-m",
            "base",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    settings = Settings(
        env="test",
        coding_backend="claude_code",
        claude_code_enabled=True,
        sandbox_backend="local",
        tool_policy_default="allow",
        otel_enabled=False,
        metrics_enabled=False,
        data_dir=tmp_path / "data",
        artifacts_dir=tmp_path / "artifacts",
        runs_dir=tmp_path / "runs",
    )
    fake = FakeClaudeCodeBackend(settings)

    result = await _runtime(
        tmp_path,
        settings,
        StructuredEditProvider(),
        claude_backend=fake,
    ).run_implementation(
        plan="Update value",
        selected_files=[{"path": "src/value.py"}],
        issue_text="Update value\nSet VALUE to 2",
    )

    assert result.success
    assert result.outputs["backend"] == "claude_code"
    assert "src/value.py" in result.outputs["files_modified"]
    assert source.read_text(encoding="utf-8") == "VALUE = 2\n"
