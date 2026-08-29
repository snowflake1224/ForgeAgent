"""In-memory + SQLite persistence for tasks/runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from sqlalchemy import String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from forgeagent.config import Settings, get_settings
from forgeagent.domain import ExecutionReport, TaskSpec


class Base(DeclarativeBase):
    pass


class TaskRow(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload_json: Mapped[str] = mapped_column(Text)


class RunRow(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32))
    report_json: Mapped[str] = mapped_column(Text)
    architecture: Mapped[str] = mapped_column(String(64), default="")


class BenchmarkRunRow(Base):
    __tablename__ = "benchmark_runs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    result_json: Mapped[str] = mapped_column(Text)


class Store:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        url = self.settings.database_url
        # sync sqlite for simplicity in local store
        if url.startswith("sqlite+aiosqlite:///"):
            url = url.replace("sqlite+aiosqlite:///", "sqlite:///", 1)
        elif url.startswith("postgresql+asyncpg://"):
            url = url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
        if url.startswith("sqlite:///./"):
            Path(self.settings.data_dir).mkdir(parents=True, exist_ok=True)
        engine_options: dict[str, Any] = {"future": True}
        if url == "sqlite:///:memory:":
            engine_options.update(
                {
                    "connect_args": {"check_same_thread": False},
                    "poolclass": StaticPool,
                }
            )
        self.engine = create_engine(url, **engine_options)
        Base.metadata.create_all(self.engine)
        self._session = sessionmaker(self.engine, expire_on_commit=False)

    def create_task(self, task: TaskSpec) -> TaskSpec:
        with Session(self.engine) as session:
            session.add(TaskRow(id=task.task_id, payload_json=task.model_dump_json()))
            session.commit()
        return task

    def get_task(self, task_id: str) -> TaskSpec | None:
        with Session(self.engine) as session:
            row = session.get(TaskRow, task_id)
            if not row:
                return None
            return TaskSpec.model_validate_json(row.payload_json)

    def list_tasks(self) -> list[TaskSpec]:
        with Session(self.engine) as session:
            rows = session.scalars(select(TaskRow)).all()
            return [TaskSpec.model_validate_json(r.payload_json) for r in rows]

    def save_report(self, report: ExecutionReport) -> ExecutionReport:
        with Session(self.engine) as session:
            session.merge(
                RunRow(
                    id=report.run_id,
                    task_id=report.task_id,
                    status=report.status.value,
                    report_json=report.model_dump_json(),
                    architecture=report.architecture.value,
                )
            )
            session.commit()
        # also write artifact
        run_dir = self.settings.runs_dir / report.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
        (run_dir / "diff.patch").write_text(report.patch.diff or "", encoding="utf-8")
        return report

    def get_report(self, run_id: str) -> ExecutionReport | None:
        with Session(self.engine) as session:
            row = session.get(RunRow, run_id)
            if not row:
                return None
            return ExecutionReport.model_validate_json(row.report_json)

    def list_runs(self) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            rows = session.scalars(select(RunRow)).all()
            return [
                {
                    "run_id": r.id,
                    "task_id": r.task_id,
                    "status": r.status,
                    "architecture": r.architecture,
                }
                for r in rows
            ]

    def save_benchmark_result(self, result_id: str, payload: dict[str, Any]) -> None:
        with Session(self.engine) as session:
            session.merge(
                BenchmarkRunRow(id=result_id, result_json=json.dumps(payload, default=str))
            )
            session.commit()

    def list_benchmark_runs(self) -> list[str]:
        with Session(self.engine) as session:
            rows = session.scalars(select(BenchmarkRunRow)).all()
            return [r.id for r in rows]

    def get_benchmark_result(self, result_id: str) -> dict[str, Any] | None:
        with Session(self.engine) as session:
            row = session.get(BenchmarkRunRow, result_id)
            if not row:
                return None
            payload = json.loads(row.result_json)
            return cast(dict[str, Any], payload) if isinstance(payload, dict) else None


_store: Store | None = None


def get_store() -> Store:
    global _store
    if _store is None:
        _store = Store()
    return _store
