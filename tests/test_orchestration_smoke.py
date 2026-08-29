from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from forgeagent.config import Settings
from forgeagent.domain import ArchitectureKind, IssueSpec, RepoSpec, TaskSpec, TaskStatus
from forgeagent.orchestration import Orchestrator


@pytest.mark.asyncio
async def test_specialized_architecture_fixes_calc_fixture(tmp_path: Path) -> None:
    fixture = Path(__file__).parents[1] / "benchmarks" / "fixtures" / "calc_bug"
    workspace = tmp_path / "calc_bug"
    shutil.copytree(fixture, workspace)
    settings = Settings(
        env="test",
        llm_provider="mock",
        sandbox_backend="local",
        tool_policy_default="allow",
        otel_enabled=False,
        metrics_enabled=False,
        data_dir=tmp_path / "data",
        artifacts_dir=tmp_path / "artifacts",
        runs_dir=tmp_path / "runs",
    )
    task = TaskSpec(
        issue=IssueSpec(
            title="Fix add",
            body="The add function subtracts instead of adding.",
        ),
        repo=RepoSpec(path=str(workspace)),
        architecture=ArchitectureKind.SPECIALIZED,
        validation_commands=["python -m pytest -q"],
    )

    report = await Orchestrator(settings=settings).run(task, workspace_root=str(workspace))

    assert report.status == TaskStatus.SUCCEEDED
    assert report.test_results
    assert all(result.exit_code == 0 for result in report.test_results)
    assert "return a + b" in (workspace / "src" / "calc.py").read_text(encoding="utf-8")
