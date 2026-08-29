"""Sandboxed code execution.

Security boundary
-----------------
Repository code and agent-generated commands are treated as untrusted.
The Docker backend is the intended production isolation boundary:

* resource limits (CPU, memory, pids)
* timeout enforcement
* optional network disablement
* bind-mounted workspace
* automatic container cleanup

The LocalProcessExecutor is an explicit fallback for developer machines /
CI without Docker. It is **not** a security boundary and must only be used
when FORGE_SANDBOX_ALLOW_LOCAL_FALLBACK=true and operators accept host risk.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from forgeagent.config import Settings, get_settings
from forgeagent.observability import EXECUTION_LATENCY, SANDBOX_FAILURES, span, timed_histogram


@dataclass
class ExecutionRequest:
    command: list[str]
    workdir: str
    timeout_seconds: float = 300.0
    env: dict[str, str] = field(default_factory=dict)
    network_enabled: bool = False


@dataclass
class ExecutionResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float
    timed_out: bool = False
    backend: str = "unknown"
    error: str | None = None
    resource: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and self.error is None


class SandboxExecutor(ABC):
    backend_name: str

    @abstractmethod
    async def run(self, request: ExecutionRequest) -> ExecutionResult:
        raise NotImplementedError


class LocalProcessExecutor(SandboxExecutor):
    """Host process executor — NOT a security boundary."""

    backend_name = "local"

    async def run(self, request: ExecutionRequest) -> ExecutionResult:
        with span(  # noqa: SIM117 - nested scopes keep tracing and timing distinct
            "execution.local", attributes={"command": " ".join(request.command)[:200]}
        ):
            with timed_histogram(EXECUTION_LATENCY, self.backend_name):
                start = time.perf_counter()
                env = os.environ.copy()
                env.update(request.env)
                try:
                    proc = await asyncio.create_subprocess_exec(
                        *request.command,
                        cwd=request.workdir,
                        env=env,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    try:
                        stdout_b, stderr_b = await asyncio.wait_for(
                            proc.communicate(),
                            timeout=request.timeout_seconds,
                        )
                        timed_out = False
                    except TimeoutError:
                        proc.kill()
                        await proc.communicate()
                        timed_out = True
                        stdout_b, stderr_b = b"", b"Execution timed out"
                        SANDBOX_FAILURES.labels("timeout").inc()
                    duration_ms = (time.perf_counter() - start) * 1000
                    return ExecutionResult(
                        exit_code=-1 if timed_out else int(proc.returncode or 0),
                        stdout=stdout_b.decode("utf-8", errors="replace"),
                        stderr=stderr_b.decode("utf-8", errors="replace"),
                        duration_ms=duration_ms,
                        timed_out=timed_out,
                        backend=self.backend_name,
                    )
                except FileNotFoundError as exc:
                    SANDBOX_FAILURES.labels("missing_binary").inc()
                    return ExecutionResult(
                        exit_code=127,
                        stdout="",
                        stderr=str(exc),
                        duration_ms=(time.perf_counter() - start) * 1000,
                        backend=self.backend_name,
                        error=str(exc),
                    )


class DockerSandboxExecutor(SandboxExecutor):
    """Docker-isolated execution with resource and network controls."""

    backend_name = "docker"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._client: Any | None = None

    def _get_client(self) -> Any:
        if self._client is None:
            import docker

            self._client = docker.from_env()
        return self._client

    async def run(self, request: ExecutionRequest) -> ExecutionResult:
        with span(  # noqa: SIM117 - nested scopes keep tracing and timing distinct
            "execution.docker", attributes={"command": " ".join(request.command)[:200]}
        ):
            with timed_histogram(EXECUTION_LATENCY, self.backend_name):
                return await asyncio.to_thread(self._run_sync, request)

    def _run_sync(self, request: ExecutionRequest) -> ExecutionResult:
        start = time.perf_counter()
        workdir = Path(request.workdir).resolve()
        network_disabled = self.settings.sandbox_network_disabled and not request.network_enabled
        try:
            client = self._get_client()
            client.ping()
        except Exception as exc:
            SANDBOX_FAILURES.labels("docker_unavailable").inc()
            return ExecutionResult(
                exit_code=125,
                stdout="",
                stderr=str(exc),
                duration_ms=(time.perf_counter() - start) * 1000,
                backend=self.backend_name,
                error=f"Docker unavailable: {exc}",
            )

        mem_limit = f"{self.settings.sandbox_memory_limit_mb}m"
        container = None
        try:
            run_kwargs: dict[str, Any] = {
                "image": self.settings.sandbox_image,
                "command": request.command,
                "working_dir": "/workspace",
                "volumes": {str(workdir): {"bind": "/workspace", "mode": "rw"}},
                "network_disabled": network_disabled,
                "mem_limit": mem_limit,
                "nano_cpus": int(self.settings.sandbox_cpu_limit * 1e9),
                "pids_limit": 256,
                "detach": True,
                "environment": request.env,
            }
            getuid = getattr(os, "getuid", None)
            getgid = getattr(os, "getgid", None)
            if getuid is not None and getgid is not None:
                run_kwargs["user"] = f"{getuid()}:{getgid()}"
            container = client.containers.run(**run_kwargs)
            result = container.wait(timeout=int(request.timeout_seconds))
            logs = container.logs(stdout=True, stderr=True).decode("utf-8", errors="replace")
            exit_code = int(result.get("StatusCode", 1))
            duration_ms = (time.perf_counter() - start) * 1000
            return ExecutionResult(
                exit_code=exit_code,
                stdout=logs,
                stderr="",
                duration_ms=duration_ms,
                timed_out=False,
                backend=self.backend_name,
                resource={
                    "mem_limit": mem_limit,
                    "cpu": self.settings.sandbox_cpu_limit,
                    "network_disabled": network_disabled,
                },
            )
        except Exception as exc:
            msg = str(exc).lower()
            timed_out = "timeout" in msg or "timed out" in msg
            reason = "timeout" if timed_out else "runtime_error"
            SANDBOX_FAILURES.labels(reason).inc()
            return ExecutionResult(
                exit_code=-1 if timed_out else 1,
                stdout="",
                stderr=str(exc),
                duration_ms=(time.perf_counter() - start) * 1000,
                timed_out=timed_out,
                backend=self.backend_name,
                error=str(exc),
            )
        finally:
            if container is not None:
                with contextlib.suppress(Exception):
                    container.remove(force=True)


class FallbackSandboxExecutor(SandboxExecutor):
    """Prefer Docker; optionally fall back to local when allowed."""

    backend_name = "fallback"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.docker = DockerSandboxExecutor(self.settings)
        self.local = LocalProcessExecutor()

    async def run(self, request: ExecutionRequest) -> ExecutionResult:
        if self.settings.sandbox_backend == "local":
            return await self.local.run(request)

        result = await self.docker.run(request)
        if (
            result.error
            and "Docker unavailable" in result.error
            and self.settings.sandbox_allow_local_fallback
        ):
            local_result = await self.local.run(request)
            local_result.resource["fallback_from"] = "docker"
            return local_result
        return result


def create_executor(settings: Settings | None = None) -> SandboxExecutor:
    cfg = settings or get_settings()
    if cfg.sandbox_backend == "local":
        return LocalProcessExecutor()
    if shutil.which("docker") is None and cfg.sandbox_allow_local_fallback:
        return LocalProcessExecutor()
    return FallbackSandboxExecutor(cfg)
