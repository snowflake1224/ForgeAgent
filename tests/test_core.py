from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from forgeagent.config import Settings
from forgeagent.execution import ExecutionRequest, LocalProcessExecutor
from forgeagent.mcp import MCPRegistry, MCPTool, ToolContext, ToolResult
from forgeagent.mcp.builtin import register_builtin_tools
from forgeagent.models import MockLLMProvider, create_llm_provider
from forgeagent.security import SecurityError, ToolPolicy, resolve_under_root
from forgeagent.skills.runtime import SkillRuntime


def test_settings_and_provider_factory(tmp_path: Path) -> None:
    settings = Settings(
        env="test",
        llm_provider="mock",
        data_dir=tmp_path / "data",
        artifacts_dir=tmp_path / "artifacts",
        runs_dir=tmp_path / "runs",
        api_cors_origins="http://one.test, http://two.test",
        allowed_tool_prefixes="repo., fs.",
    )
    settings.ensure_dirs()

    assert settings.cors_origin_list == ["http://one.test", "http://two.test"]
    assert settings.allowed_tool_prefix_list == ["repo.", "fs."]
    assert all(path.is_dir() for path in (settings.data_dir, settings.artifacts_dir, settings.runs_dir))
    assert isinstance(create_llm_provider(settings), MockLLMProvider)


def test_path_security_stays_under_workspace(tmp_path: Path) -> None:
    assert resolve_under_root(tmp_path, "src/module.py") == tmp_path / "src" / "module.py"
    with pytest.raises(SecurityError):
        resolve_under_root(tmp_path, "../outside.py")


@pytest.mark.asyncio
async def test_registry_enforces_schema_policy_and_timeout(tmp_path: Path) -> None:
    settings = Settings(
        env="test",
        tool_policy_default="deny",
        allowed_tool_prefixes="test.",
        data_dir=tmp_path / "data",
        artifacts_dir=tmp_path / "artifacts",
        runs_dir=tmp_path / "runs",
    )
    registry = MCPRegistry(policy=ToolPolicy(settings))

    async def slow_handler(context: ToolContext, arguments: dict[str, object]) -> ToolResult:
        await asyncio.sleep(0.05)
        return ToolResult(success=True, data={"value": arguments["value"]})

    registry.register(
        MCPTool(
            name="test.slow",
            description="Test timeout handling.",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            handler=slow_handler,
            timeout_seconds=0.001,
        )
    )
    context = ToolContext(workspace_root=str(tmp_path), run_id="run_test")

    invalid = await registry.invoke("test.slow", {"value": "wrong"}, context)
    timed_out = await registry.invoke("test.slow", {"value": 1}, context)

    assert invalid.error_type == "validation_error"
    assert timed_out.error_type == "timeout"


@pytest.mark.asyncio
async def test_local_executor_runs_structured_argv(tmp_path: Path) -> None:
    result = await LocalProcessExecutor().run(
        ExecutionRequest(
            command=["python", "-c", "print('forge-ok')"],
            workdir=str(tmp_path),
            timeout_seconds=10,
            env={},
            network_enabled=False,
        )
    )
    assert result.ok
    assert result.stdout.strip() == "forge-ok"


@pytest.mark.asyncio
async def test_skill_runtime_applies_canonical_calc_repair(tmp_path: Path) -> None:
    source = tmp_path / "src" / "calc.py"
    source.parent.mkdir(parents=True)
    source.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")

    settings = Settings(
        env="test",
        sandbox_backend="local",
        tool_policy_default="allow",
        data_dir=tmp_path / "data",
        artifacts_dir=tmp_path / "artifacts",
        runs_dir=tmp_path / "runs",
    )
    registry = MCPRegistry(policy=ToolPolicy(settings))
    register_builtin_tools(registry, executor=LocalProcessExecutor())
    runtime = SkillRuntime(
        registry,
        MockLLMProvider(),
        workspace_root=str(tmp_path),
        run_id="run_test",
    )

    result = await runtime.run_implementation(
        plan="Fix addition.",
        selected_files=[{"path": "src/calc.py"}],
        issue_text="add subtracts instead of adding",
    )

    assert result.success
    assert result.outputs["files_modified"] == ["src/calc.py"]
    assert "return a + b" in source.read_text(encoding="utf-8")
