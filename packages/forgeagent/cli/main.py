"""Typer command-line interface for local ForgeAgent operations."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.syntax import Syntax

from forgeagent.config import Settings
from forgeagent.domain import ArchitectureKind, IssueSpec, RepoSpec, TaskSpec, TaskStatus
from forgeagent.evaluation import (
    BenchmarkRunner,
    ExperimentResult,
    compare_experiments,
    load_benchmark_config,
    load_experiment_result,
)
from forgeagent.orchestration import Orchestrator
from forgeagent.storage import Store

app = typer.Typer(help="Run ForgeAgent tasks, benchmarks, and report inspection.")
task_app = typer.Typer(help="Create and run software-engineering tasks.")
benchmark_app = typer.Typer(help="Run and compare reproducible benchmarks.")
app.add_typer(task_app, name="task")
app.add_typer(benchmark_app, name="benchmark")
console = Console()


def _settings() -> Settings:
    """Load settings; Docker remains the fail-closed execution default."""
    values: dict[str, Any] = {}
    if "FORGE_LLM_PROVIDER" not in os.environ:
        values["llm_provider"] = "mock"
    return Settings(**values)


def _store(settings: Settings) -> Store:
    settings.ensure_dirs()
    return Store(settings)


def _architecture(value: str) -> ArchitectureKind:
    try:
        return ArchitectureKind(value)
    except ValueError as exc:
        choices = ", ".join(item.value for item in ArchitectureKind)
        raise typer.BadParameter(f"Choose one of: {choices}") from exc


def _print_json(payload: Any) -> None:
    console.print_json(json.dumps(payload, default=str))


def _load_experiment(reference: str, settings: Settings) -> ExperimentResult:
    path = Path(reference)
    if path.is_dir():
        path = path / "result.json"
    if not path.exists():
        path = settings.artifacts_dir / "experiments" / reference / "result.json"
    if not path.exists():
        raise typer.BadParameter(f"Experiment not found: {reference}")
    return load_experiment_result(path)


@task_app.command("run")
def run_task(
    workspace: Annotated[
        Path, typer.Argument(exists=True, file_okay=False, resolve_path=True)
    ],
    title: Annotated[str, typer.Option(help="Issue title.")] = "Resolve repository issue",
    body: Annotated[str, typer.Option(help="Issue details.")] = "",
    architecture: Annotated[
        str,
        typer.Option("--architecture", "-a", help="ArchitectureKind value."),
    ] = ArchitectureKind.SPECIALIZED.value,
    validation: Annotated[
        list[str] | None,
        typer.Option("--validation", "-v", help="Validation command; may be repeated."),
    ] = None,
) -> None:
    """Run one task against an existing workspace and persist its report."""
    settings = _settings()
    store = _store(settings)
    task = TaskSpec(
        issue=IssueSpec(title=title, body=body),
        repo=RepoSpec(path=str(workspace)),
        architecture=_architecture(architecture),
        validation_commands=validation or ["python -m pytest -q"],
    )
    store.create_task(task)
    report = asyncio.run(Orchestrator(settings=settings).run(task, workspace_root=str(workspace)))
    store.save_report(report)
    _print_json(
        {
            "task_id": task.task_id,
            "run_id": report.run_id,
            "status": report.status.value,
            "report": str(settings.runs_dir / report.run_id / "report.json"),
        }
    )
    if report.status != TaskStatus.SUCCEEDED:
        raise typer.Exit(code=1)


@benchmark_app.command("run")
def run_benchmark(
    config: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
) -> None:
    """Run a benchmark config and persist actual experiment artifacts."""
    settings = _settings()
    benchmark = load_benchmark_config(config)
    runner = BenchmarkRunner(settings=settings)
    result = asyncio.run(runner.run(benchmark))
    _store(settings).save_benchmark_result(
        result.experiment_id,
        result.model_dump(mode="json"),
    )
    report = runner.output_root / result.experiment_id / "report.md"
    _print_json(
        {
            "experiment_id": result.experiment_id,
            "report": str(report),
            "aggregates": result.aggregates,
        }
    )


@benchmark_app.command("compare")
def compare_benchmark_runs(run_a: str, run_b: str) -> None:
    """Compare two result files, experiment directories, or experiment IDs."""
    settings = _settings()
    _print_json(
        compare_experiments(
            _load_experiment(run_a, settings),
            _load_experiment(run_b, settings),
        )
    )


@app.command()
def trace(run_id: str) -> None:
    """Print the recorded agent-step timeline for a run."""
    report = _store(_settings()).get_report(run_id)
    if report is None:
        raise typer.BadParameter(f"Run not found: {run_id}")
    _print_json([step.model_dump(mode="json") for step in report.steps])


@app.command()
def diff(run_id: str) -> None:
    """Print the patch captured for a run."""
    report = _store(_settings()).get_report(run_id)
    if report is None:
        raise typer.BadParameter(f"Run not found: {run_id}")
    console.print(Syntax(report.patch.diff or "(no diff)", "diff", theme="ansi_dark"))


@app.command()
def evaluate(run_id: str) -> None:
    """Summarize the measured outcome of one persisted run."""
    report = _store(_settings()).get_report(run_id)
    if report is None:
        raise typer.BadParameter(f"Run not found: {run_id}")
    tests_passed = bool(report.test_results) and all(
        result.exit_code == 0 and not result.timed_out for result in report.test_results
    )
    accepted = (
        report.status == TaskStatus.SUCCEEDED
        and tests_passed
        and (report.review is None or report.review.approved)
    )
    _print_json(
        {
            "run_id": report.run_id,
            "status": report.status.value,
            "patch_accepted": accepted,
            "metrics": report.metrics.model_dump(mode="json"),
            "tests": [result.model_dump(mode="json") for result in report.test_results],
        }
    )


if __name__ == "__main__":
    app()
