"""Observability: OpenTelemetry traces + Prometheus metrics."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)
from opentelemetry.trace import Span, Status, StatusCode
from prometheus_client import Counter, Histogram, start_http_server

from forgeagent.config import Settings, get_settings

_tracer_ready = False

TASKS_TOTAL = Counter(
    "forgeagent_tasks_total",
    "Total tasks by status",
    ["status", "architecture"],
)
AGENT_FAILURES = Counter(
    "forgeagent_agent_failures_total",
    "Agent step failures",
    ["role"],
)
TOOL_FAILURES = Counter(
    "forgeagent_tool_failures_total",
    "Tool invocation failures",
    ["tool"],
)
MODEL_LATENCY = Histogram(
    "forgeagent_model_latency_seconds",
    "LLM call latency",
    ["provider", "model"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120),
)
EXECUTION_LATENCY = Histogram(
    "forgeagent_execution_latency_seconds",
    "Sandbox / test execution latency",
    ["backend"],
    buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300),
)
TOKENS_TOTAL = Counter(
    "forgeagent_tokens_total",
    "Tokens consumed",
    ["direction"],
)
RETRIES_TOTAL = Counter(
    "forgeagent_retries_total",
    "Retry attempts",
    ["component"],
)
TEST_FAILURES = Counter(
    "forgeagent_test_failures_total",
    "Test suite failures observed",
)
SANDBOX_FAILURES = Counter(
    "forgeagent_sandbox_failures_total",
    "Sandbox execution failures",
    ["reason"],
)


def setup_observability(settings: Settings | None = None) -> None:
    global _tracer_ready
    cfg = settings or get_settings()
    if cfg.otel_enabled and not _tracer_ready:
        resource = Resource.create({"service.name": "forgeagent"})
        provider = TracerProvider(resource=resource)
        # Console exporter keeps local/dev usable without a collector.
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            exporter = OTLPSpanExporter(endpoint=f"{cfg.otel_exporter_otlp_endpoint}/v1/traces")
            provider.add_span_processor(SimpleSpanProcessor(exporter))
        except Exception:
            # Collector may be unavailable; console export remains.
            pass
        trace.set_tracer_provider(provider)
        _tracer_ready = True

    if cfg.metrics_enabled:
        # A second process may already own the configured metrics port.
        with suppress(OSError):
            start_http_server(cfg.metrics_port)


def get_tracer(name: str = "forgeagent") -> trace.Tracer:
    return trace.get_tracer(name)


@contextmanager
def span(
    name: str,
    *,
    attributes: dict[str, Any] | None = None,
    record_exception: bool = True,
) -> Iterator[Span]:
    tracer = get_tracer()
    with tracer.start_as_current_span(name) as current:
        if attributes:
            for k, v in attributes.items():
                if v is not None:
                    current.set_attribute(k, v)
        try:
            yield current
        except Exception as exc:
            current.set_status(Status(StatusCode.ERROR, str(exc)))
            if record_exception:
                current.record_exception(exc)
            raise


@contextmanager
def timed_histogram(histogram: Histogram, *label_values: str) -> Iterator[None]:
    start = time.perf_counter()
    try:
        yield
    finally:
        histogram.labels(*label_values).observe(time.perf_counter() - start)
