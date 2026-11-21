from pathlib import Path

from fastapi.testclient import TestClient

from forgeagent.api.app import app
from forgeagent.config import Settings
from forgeagent.storage import Store


def test_health_and_create_task(tmp_path: Path) -> None:
    settings = Settings(
        env="test",
        database_url="sqlite:///:memory:",
        data_dir=tmp_path / "data",
        artifacts_dir=tmp_path / "artifacts",
        runs_dir=tmp_path / "runs",
        otel_enabled=False,
        metrics_enabled=False,
    )
    original_store = app.state.store
    original_settings = app.state.settings
    app.state.store = Store(settings)
    app.state.settings = settings
    try:
        with TestClient(app) as client:
            health = client.get("/health")
            created = client.post(
                "/tasks",
                json={
                    "title": "Inspect repository",
                    "body": "Create a persisted task.",
                    "workspace_path": str(tmp_path),
                    "architecture": "A_single_agent",
                },
            )
    finally:
        app.state.store = original_store
        app.state.settings = original_settings

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert created.status_code == 201
    assert created.json()["issue"]["title"] == "Inspect repository"
