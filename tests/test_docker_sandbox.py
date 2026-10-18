from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from forgeagent.config import Settings
from forgeagent.execution import (
    DockerSandboxExecutor,
    ExecutionRequest,
    FallbackSandboxExecutor,
    create_executor,
)


class FakeContainer:
    removed = False

    def wait(self, timeout: int) -> dict[str, int]:
        assert timeout == 15
        return {"StatusCode": 0}

    def logs(self, *, stdout: bool, stderr: bool) -> bytes:
        if stdout and not stderr:
            return b"sandbox-ok\n"
        return b""

    def remove(self, *, force: bool) -> None:
        assert force
        self.removed = True


class FakeContainers:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}
        self.container = FakeContainer()

    def run(self, **kwargs: Any) -> FakeContainer:
        self.kwargs = kwargs
        return self.container


class FakeDockerClient:
    def __init__(self) -> None:
        self.containers = FakeContainers()

    def ping(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_docker_executor_applies_security_and_resource_limits(tmp_path: Path) -> None:
    settings = Settings(
        env="test",
        sandbox_backend="docker",
        sandbox_allow_local_fallback=False,
        sandbox_network_disabled=True,
        sandbox_cpu_limit=1.5,
        sandbox_memory_limit_mb=768,
        otel_enabled=False,
        metrics_enabled=False,
        data_dir=tmp_path / "data",
        artifacts_dir=tmp_path / "artifacts",
        runs_dir=tmp_path / "runs",
    )
    executor = DockerSandboxExecutor(settings)
    client = FakeDockerClient()
    executor._client = client  # noqa: SLF001 - inject deterministic Docker API fixture

    result = await executor.run(
        ExecutionRequest(
            command=["python", "-c", "print('sandbox-ok')"],
            workdir=str(tmp_path),
            timeout_seconds=15,
        )
    )

    assert result.ok
    assert result.backend == "docker"
    assert result.stdout == "sandbox-ok\n"
    kwargs = client.containers.kwargs
    assert kwargs["network_disabled"] is True
    assert kwargs["mem_limit"] == "768m"
    assert kwargs["nano_cpus"] == 1_500_000_000
    assert kwargs["pids_limit"] == 256
    assert kwargs["read_only"] is True
    assert kwargs["cap_drop"] == ["ALL"]
    assert kwargs["security_opt"] == ["no-new-privileges:true"]
    assert kwargs["tmpfs"]["/tmp"].startswith("rw,noexec,nosuid")
    assert client.containers.container.removed


def test_docker_is_fail_closed_by_default(tmp_path: Path) -> None:
    settings = Settings(
        env="test",
        sandbox_backend="docker",
        otel_enabled=False,
        metrics_enabled=False,
        data_dir=tmp_path / "data",
        artifacts_dir=tmp_path / "artifacts",
        runs_dir=tmp_path / "runs",
    )

    assert settings.sandbox_allow_local_fallback is False
    assert isinstance(create_executor(settings), FallbackSandboxExecutor)


@pytest.mark.sandbox
@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("FORGE_RUN_DOCKER_TESTS") != "1",
    reason="set FORGE_RUN_DOCKER_TESTS=1 after building forgeagent/sandbox:latest",
)
async def test_real_docker_sandbox_smoke(tmp_path: Path) -> None:
    settings = Settings(
        env="test",
        sandbox_backend="docker",
        sandbox_allow_local_fallback=False,
        sandbox_image="forgeagent/sandbox:latest",
        otel_enabled=False,
        metrics_enabled=False,
        data_dir=tmp_path / "data",
        artifacts_dir=tmp_path / "artifacts",
        runs_dir=tmp_path / "runs",
    )
    result = await DockerSandboxExecutor(settings).run(
        ExecutionRequest(
            command=["python", "-c", "print('docker-live-ok')"],
            workdir=str(tmp_path),
            timeout_seconds=30,
        )
    )

    assert result.ok, result.stderr or result.error
    assert result.backend == "docker"
    assert result.stdout.strip() == "docker-live-ok"
