"""FastAPI application for task submission and run inspection."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from forgeagent.config import get_settings
from forgeagent.domain import ArchitectureKind, ExecutionReport, IssueSpec, RepoSpec, TaskSpec
from forgeagent.orchestration import Orchestrator
from forgeagent.storage import Store, get_store


class TaskCreate(BaseModel):
    title: str
    body: str = ""
    workspace_path: str
    architecture: ArchitectureKind = ArchitectureKind.SPECIALIZED
    validation_commands: list[str] = Field(default_factory=lambda: ["python -m pytest -q"])
    tags: list[str] = Field(default_factory=list)


class RunCreate(BaseModel):
    architecture: ArchitectureKind | None = None


settings = get_settings()
app = FastAPI(title="ForgeAgent API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.state.store = get_store()


def _store() -> Store:
    return cast(Store, app.state.store)


def _report(run_id: str) -> ExecutionReport:
    report = _store().get_report(run_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return report


@app.post("/tasks", status_code=201)
async def create_task(payload: TaskCreate) -> dict[str, Any]:
    workspace = Path(payload.workspace_path).resolve()
    if not workspace.is_dir():
        raise HTTPException(status_code=422, detail="workspace_path must be an existing directory")
    task = TaskSpec(
        issue=IssueSpec(title=payload.title, body=payload.body),
        repo=RepoSpec(path=str(workspace)),
        architecture=payload.architecture,
        validation_commands=payload.validation_commands,
        tags=payload.tags,
    )
    try:
        _store().create_task(task)
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Task already exists") from exc
    return task.model_dump(mode="json")


@app.post("/tasks/{task_id}/runs", status_code=201)
async def run_task(task_id: str, payload: RunCreate | None = None) -> dict[str, Any]:
    task = _store().get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if payload and payload.architecture is not None:
        task = task.model_copy(update={"architecture": payload.architecture})
    if not task.repo.path:
        raise HTTPException(status_code=422, detail="Task has no workspace path")
    report = await Orchestrator(settings=settings).run(task, workspace_root=task.repo.path)
    _store().save_report(report)
    return report.model_dump(mode="json")


@app.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict[str, Any]:
    return _report(run_id).model_dump(mode="json")


@app.get("/runs/{run_id}/trace")
async def get_trace(run_id: str) -> list[dict[str, Any]]:
    return [step.model_dump(mode="json") for step in _report(run_id).steps]


@app.get("/runs/{run_id}/diff")
async def get_diff(run_id: str) -> dict[str, Any]:
    patch = _report(run_id).patch
    return patch.model_dump(mode="json")


@app.get("/runs/{run_id}/tests")
async def get_tests(run_id: str) -> list[dict[str, Any]]:
    return [result.model_dump(mode="json") for result in _report(run_id).test_results]


@app.get("/runs/{run_id}/metrics")
async def get_metrics(run_id: str) -> dict[str, Any]:
    return _report(run_id).metrics.model_dump(mode="json")


@app.get("/benchmarks")
async def list_benchmarks() -> list[str]:
    return _store().list_benchmark_runs()


@app.get("/benchmarks/{experiment_id}")
async def get_benchmark(experiment_id: str) -> dict[str, object]:
    result = _store().get_benchmark_result(experiment_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Benchmark not found")
    return result


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "forgeagent"}


web_root = Path(__file__).resolve().parents[3] / "apps" / "web"
if web_root.is_dir():
    app.mount("/ui", StaticFiles(directory=web_root, html=True), name="web")


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse("/ui/")
