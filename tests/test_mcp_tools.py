from pathlib import Path

import pytest

from forgeagent.config import Settings
from forgeagent.execution import LocalProcessExecutor
from forgeagent.mcp import MCPRegistry, ToolContext
from forgeagent.mcp.builtin import register_builtin_tools
from forgeagent.security import ToolPolicy


@pytest.mark.asyncio
async def test_builtin_tree_and_read_tools(tmp_path: Path) -> None:
    source = tmp_path / "src" / "sample.py"
    source.parent.mkdir()
    source.write_text("VALUE = 42\n", encoding="utf-8")
    settings = Settings(
        env="test",
        tool_policy_default="deny",
        allowed_tool_prefixes="repo.,fs.",
        data_dir=tmp_path / "data",
        artifacts_dir=tmp_path / "artifacts",
        runs_dir=tmp_path / "runs",
    )
    registry = MCPRegistry(policy=ToolPolicy(settings))
    register_builtin_tools(registry, executor=LocalProcessExecutor())
    context = ToolContext(workspace_root=str(tmp_path), run_id="run_tools")

    tree = await registry.invoke("repo.tree", {"max_depth": 3}, context)
    read = await registry.invoke("fs.read", {"path": "src/sample.py"}, context)
    write = await registry.invoke(
        "fs.write",
        {"path": "src/generated.py", "content": "A = 1\r\nB = 2\r\n"},
        context,
    )

    assert tree.success
    assert "src/sample.py" in tree.data["tree"]
    assert read.success
    assert read.data["content"].splitlines() == ["VALUE = 42"]
    assert write.success
    assert (tmp_path / "src" / "generated.py").read_bytes() == b"A = 1\r\nB = 2\r\n"
