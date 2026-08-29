"""Evaluation and experiment comparison framework."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from forgeagent.config import Settings, get_settings
from forgeagent.domain import (
    ArchitectureKind,
    ExecutionReport,
    FailureCategory,
    IssueSpec,
    RepoSpec,
    TaskSpec,
    TaskStatus,
    new_id,
    utcnow,
)
from forgeagent.orchestration import Orchestrator


class BenchmarkTask(BaseModel):
    task_id: str
    repository: str
    commit: str | None = None
    issue_title: str
    issue_body: str
    validation_commands: list[str] = Field(default_factory=lambda: ["python -m pytest -q"])
    timeout_seconds: int = 600
    token_budget: int = 100_000
    tags: list[str] = Field(default_factory=list)


class BenchmarkConfig(BaseModel):
    name: str
    version: str = "0.1.0"
    description: str = ""
    architectures: list[ArchitectureKind] = Field(
        default_factory=lambda: [
            ArchitectureKind.SINGLE,
            ArchitectureKind.PLANNER_CODER,
            ArchitectureKind.PLANNER_CODER_REVIEWER,
            ArchitectureKind.SPECIALIZED,
        ]
    )
    tasks: list[BenchmarkTask]
    model_provider: str = "mock"
    model_name: str = "mock-gpt"
    skill_version: str = "0.1.0"
    tool_version: str = "0.1.0"


class TaskMetrics(BaseModel):
    task_id: str
    architecture: ArchitectureKind
    run_id: str
    status: TaskStatus
    completed: bool
    test_pass_rate: float | None
    patch_accepted: bool
    regression_detected: bool
    iterations: int
    tool_calls: int
    invalid_tool_call_rate: float
    wall_clock_ms: float
    tokens: int
    estimated_cost_usd: float
    failure_category: FailureCategory


class ExperimentResult(BaseModel):
    experiment_id: str
    config_name: str
    config_version: str
    git_commit: str | None
    model_provider: str
    model_name: str
    skill_version: str
    tool_version: str
    benchmark_version: str
    started_at: str
    finished_at: str
    task_metrics: list[TaskMetrics]
    aggregates: dict[str, Any]
    reports: list[ExecutionReport]


def load_benchmark_config(path: Path) -> BenchmarkConfig:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return BenchmarkConfig.model_validate(data)


def _git_head(repo: Path) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def _prepare_workspace(*, fixture_root: Path, task: BenchmarkTask, work_root: Path) -> Path:
    src = Path(task.repository)
    if not src.is_absolute():
        src = fixture_root / task.repository
    if not src.exists():
        raise FileNotFoundError(f"Benchmark repository not found: {src}")
    dest = work_root / task.task_id
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns(".git"))
    if not (dest / ".git").exists():
        subprocess.run(["git", "init"], cwd=dest, check=True, capture_output=True)
        subprocess.run(["git", "add", "-A"], cwd=dest, check=True, capture_output=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.email=forge@local",
                "-c",
                "user.name=ForgeAgent",
                "commit",
                "-m",
                "bench base",
            ],
            cwd=dest,
            check=False,
            capture_output=True,
        )
    if task.commit:
        subprocess.run(
            ["git", "checkout", task.commit],
            cwd=dest,
            check=False,
            capture_output=True,
        )
    return dest


def _patch_accepted(report: ExecutionReport) -> bool:
    if report.status != TaskStatus.SUCCEEDED:
        return False
    if report.review is not None and not report.review.approved:
        return False
    if not report.test_results:
        return False
    return all(t.exit_code == 0 and not t.timed_out for t in report.test_results)


def _count_failures(rows: list[TaskMetrics]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in rows:
        if r.failure_category == FailureCategory.NONE and r.completed:
            continue
        key = r.failure_category.value
        counts[key] = counts.get(key, 0) + 1
    return counts


def aggregate_metrics(rows: list[TaskMetrics]) -> dict[str, Any]:
    if not rows:
        return {}
    n = len(rows)
    completed = sum(1 for r in rows if r.completed)
    accepted = sum(1 for r in rows if r.patch_accepted)
    regressions = sum(1 for r in rows if r.regression_detected)
    pass_rates = [r.test_pass_rate for r in rows if r.test_pass_rate is not None]
    by_arch: dict[str, list[TaskMetrics]] = {}
    for r in rows:
        by_arch.setdefault(r.architecture.value, []).append(r)

    def arch_summary(items: list[TaskMetrics]) -> dict[str, Any]:
        m = len(items)
        return {
            "n": m,
            "completion_rate": sum(1 for i in items if i.completed) / m if m else 0.0,
            "patch_acceptance_rate": sum(1 for i in items if i.patch_accepted) / m if m else 0.0,
            "avg_iterations": sum(i.iterations for i in items) / m if m else 0.0,
            "avg_tool_calls": sum(i.tool_calls for i in items) / m if m else 0.0,
            "avg_invalid_tool_call_rate": (
                sum(i.invalid_tool_call_rate for i in items) / m if m else 0.0
            ),
            "avg_wall_clock_ms": sum(i.wall_clock_ms for i in items) / m if m else 0.0,
            "total_tokens": sum(i.tokens for i in items),
            "total_estimated_cost_usd": sum(i.estimated_cost_usd for i in items),
            "failure_categories": _count_failures(items),
        }

    return {
        "n_tasks": n,
        "completion_rate": completed / n,
        "patch_acceptance_rate": accepted / n,
        "regression_rate": regressions / n,
        "avg_test_pass_rate": (sum(pass_rates) / len(pass_rates)) if pass_rates else None,
        "avg_iterations": sum(r.iterations for r in rows) / n,
        "avg_tool_calls": sum(r.tool_calls for r in rows) / n,
        "avg_invalid_tool_call_rate": sum(r.invalid_tool_call_rate for r in rows) / n,
        "avg_wall_clock_ms": sum(r.wall_clock_ms for r in rows) / n,
        "total_tokens": sum(r.tokens for r in rows),
        "total_estimated_cost_usd": sum(r.estimated_cost_usd for r in rows),
        "by_architecture": {k: arch_summary(v) for k, v in by_arch.items()},
        "failure_categories": _count_failures(rows),
    }


@dataclass
class BenchmarkRunner:
    settings: Settings
    fixtures_root: Path
    output_root: Path

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        fixtures_root: Path | None = None,
        output_root: Path | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.fixtures_root = fixtures_root or Path("benchmarks/fixtures")
        self.output_root = output_root or Path("artifacts/experiments")

    async def run(
        self,
        config: BenchmarkConfig,
        *,
        architectures: list[ArchitectureKind] | None = None,
    ) -> ExperimentResult:
        arches = architectures or config.architectures
        experiment_id = new_id("exp_")
        work_root = self.output_root / experiment_id / "workspaces"
        work_root.mkdir(parents=True, exist_ok=True)
        started = utcnow()
        orchestrator = Orchestrator(settings=self.settings)
        metrics: list[TaskMetrics] = []
        reports: list[ExecutionReport] = []

        for task in config.tasks:
            for arch in arches:
                ws = _prepare_workspace(
                    fixture_root=self.fixtures_root,
                    task=task,
                    work_root=work_root / arch.value,
                )
                spec = TaskSpec(
                    task_id=task.task_id,
                    issue=IssueSpec(title=task.issue_title, body=task.issue_body),
                    repo=RepoSpec(path=str(ws), commit=task.commit),
                    architecture=arch,
                    timeout_seconds=task.timeout_seconds,
                    token_budget=task.token_budget,
                    validation_commands=task.validation_commands,
                    tags=task.tags,
                )
                report = await orchestrator.run(spec, workspace_root=str(ws))
                reports.append(report)
                inv_rate = (
                    report.metrics.invalid_tool_calls / report.metrics.tool_calls
                    if report.metrics.tool_calls
                    else 0.0
                )
                metrics.append(
                    TaskMetrics(
                        task_id=task.task_id,
                        architecture=arch,
                        run_id=report.run_id,
                        status=report.status,
                        completed=report.status == TaskStatus.SUCCEEDED,
                        test_pass_rate=report.metrics.test_pass_rate,
                        patch_accepted=_patch_accepted(report),
                        regression_detected=report.metrics.regression_detected,
                        iterations=report.metrics.iterations,
                        tool_calls=report.metrics.tool_calls,
                        invalid_tool_call_rate=inv_rate,
                        wall_clock_ms=report.metrics.wall_clock_ms,
                        tokens=report.metrics.tokens_in + report.metrics.tokens_out,
                        estimated_cost_usd=report.metrics.estimated_cost_usd,
                        failure_category=report.metrics.failure_category,
                    )
                )

        finished = utcnow()
        result = ExperimentResult(
            experiment_id=experiment_id,
            config_name=config.name,
            config_version=config.version,
            git_commit=_git_head(Path.cwd()),
            model_provider=config.model_provider,
            model_name=config.model_name,
            skill_version=config.skill_version,
            tool_version=config.tool_version,
            benchmark_version=config.version,
            started_at=started.isoformat(),
            finished_at=finished.isoformat(),
            task_metrics=metrics,
            aggregates=aggregate_metrics(metrics),
            reports=reports,
        )
        self._persist(result)
        return result

    def _persist(self, result: ExperimentResult) -> None:
        out = self.output_root / result.experiment_id
        out.mkdir(parents=True, exist_ok=True)
        (out / "result.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
        (out / "report.md").write_text(render_experiment_report(result), encoding="utf-8")


def render_experiment_report(result: ExperimentResult) -> str:
    agg = result.aggregates
    lines = [
        f"# Experiment Report: {result.experiment_id}",
        "",
        f"- Config: `{result.config_name}` v{result.config_version}",
        f"- Model: `{result.model_provider}` / `{result.model_name}`",
        f"- Skill version: `{result.skill_version}`",
        f"- Tool version: `{result.tool_version}`",
        f"- Git commit: `{result.git_commit or 'n/a'}`",
        f"- Window: {result.started_at} → {result.finished_at}",
        "",
        "## Aggregates",
        "",
        f"- Tasks (arch×task rows): **{agg.get('n_tasks', 0)}**",
        f"- Completion rate: **{agg.get('completion_rate', 0):.3f}**",
        f"- Patch acceptance rate: **{agg.get('patch_acceptance_rate', 0):.3f}**",
        f"- Regression rate: **{agg.get('regression_rate', 0):.3f}**",
        f"- Avg iterations: **{agg.get('avg_iterations', 0):.2f}**",
        f"- Avg tool calls: **{agg.get('avg_tool_calls', 0):.2f}**",
        f"- Avg invalid tool-call rate: **{agg.get('avg_invalid_tool_call_rate', 0):.3f}**",
        f"- Avg wall-clock (ms): **{agg.get('avg_wall_clock_ms', 0):.1f}**",
        f"- Total tokens: **{agg.get('total_tokens', 0)}**",
        f"- Estimated cost (USD): **{agg.get('total_estimated_cost_usd', 0):.6f}**",
        "",
        "## By architecture",
        "",
    ]
    by_arch = agg.get("by_architecture") or {}
    for arch, summary in by_arch.items():
        lines.append(f"### {arch}")
        lines.append("")
        for k, v in summary.items():
            if k == "failure_categories":
                lines.append(f"- {k}: `{json.dumps(v)}`")
            elif isinstance(v, float):
                lines.append(f"- {k}: **{v:.3f}**")
            else:
                lines.append(f"- {k}: **{v}**")
        lines.append("")
    lines.extend(["## Task rows", ""])
    for row in result.task_metrics:
        lines.append(
            f"- `{row.task_id}` / `{row.architecture.value}` → "
            f"{row.status.value}, accepted={row.patch_accepted}, "
            f"iters={row.iterations}, tools={row.tool_calls}, "
            f"ms={row.wall_clock_ms:.0f}"
        )
    lines.append("")
    lines.append(
        "_Generated from an actual local run. "
        "Do not claim architecture superiority without matched configs._"
    )
    return "\n".join(lines)


def compare_experiments(a: ExperimentResult, b: ExperimentResult) -> dict[str, Any]:
    return {
        "a": {"id": a.experiment_id, "aggregates": a.aggregates},
        "b": {"id": b.experiment_id, "aggregates": b.aggregates},
        "delta_completion_rate": (a.aggregates.get("completion_rate", 0) or 0)
        - (b.aggregates.get("completion_rate", 0) or 0),
        "delta_patch_acceptance_rate": (a.aggregates.get("patch_acceptance_rate", 0) or 0)
        - (b.aggregates.get("patch_acceptance_rate", 0) or 0),
        "delta_avg_wall_clock_ms": (a.aggregates.get("avg_wall_clock_ms", 0) or 0)
        - (b.aggregates.get("avg_wall_clock_ms", 0) or 0),
        "note": "Deltas are A - B. No architecture is declared superior without controlled runs.",
    }


def load_experiment_result(path: Path) -> ExperimentResult:
    return ExperimentResult.model_validate_json(path.read_text(encoding="utf-8"))
