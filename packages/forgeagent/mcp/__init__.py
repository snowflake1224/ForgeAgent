"""MCP tool layer: schemas, registry, policy, structured invocation."""

from __future__ import annotations

import asyncio
import json
import time
import traceback
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JSONSchemaValidationError

from forgeagent.observability import TOOL_FAILURES, span
from forgeagent.security import SecurityError, ToolPolicy, redact_secrets

ToolHandler = Callable[["ToolContext", dict[str, Any]], Awaitable["ToolResult"]]


@dataclass
class ToolContext:
    """Per-invocation context bound to a workspace / run."""

    workspace_root: str
    run_id: str
    agent_role: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    error_type: str | None = None

    def to_structured(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "error_type": self.error_type,
        }


@dataclass
class MCPTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler
    timeout_seconds: float = 60.0
    tags: list[str] = field(default_factory=list)

    def validate_args(self, arguments: dict[str, Any]) -> None:
        validator = Draft202012Validator(self.input_schema)
        errors = sorted(validator.iter_errors(arguments), key=lambda e: list(e.path))
        if errors:
            msgs = "; ".join(e.message for e in errors[:5])
            raise JSONSchemaValidationError(msgs)


class MCPRegistry:
    """Registry/adapter abstraction — add tools without changing agent logic."""

    def __init__(self, policy: ToolPolicy | None = None) -> None:
        self._tools: dict[str, MCPTool] = {}
        self.policy = policy or ToolPolicy()

    def register(self, tool: MCPTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> MCPTool | None:
        return self._tools.get(name)

    def list_tools(self) -> list[MCPTool]:
        return list(self._tools.values())

    def list_specs(self) -> list[dict[str, Any]]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "inputSchema": t.input_schema,
                "tags": t.tags,
            }
            for t in self._tools.values()
        ]

    async def invoke(
        self,
        name: str,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        start = time.perf_counter()
        with span(
            "mcp.request",
            attributes={"mcp.tool": name, "run.id": context.run_id},
        ):
            tool = self._tools.get(name)
            if tool is None:
                return ToolResult(
                    success=False,
                    error=f"Unknown tool: {name}",
                    error_type="not_found",
                )
            try:
                self.policy.authorize(name, arguments)
                tool.validate_args(arguments)
                result = await asyncio.wait_for(
                    tool.handler(context, arguments),
                    timeout=tool.timeout_seconds,
                )
            except SecurityError as exc:
                result = ToolResult(
                    success=False,
                    error=str(exc),
                    error_type="security_violation",
                )
            except JSONSchemaValidationError as exc:
                result = ToolResult(
                    success=False,
                    error=f"Invalid arguments: {exc.message}",
                    error_type="validation_error",
                )
            except TimeoutError as exc:
                result = ToolResult(
                    success=False,
                    error=str(exc) or "Tool timed out",
                    error_type="timeout",
                )
            except Exception as exc:
                result = ToolResult(
                    success=False,
                    error=redact_secrets(f"{type(exc).__name__}: {exc}"),
                    error_type="internal_error",
                    data={"traceback": redact_secrets(traceback.format_exc())[-2000:]},
                )

            duration_ms = (time.perf_counter() - start) * 1000
            if not result.success:
                TOOL_FAILURES.labels(name).inc()
            result.data.setdefault("_meta", {})
            result.data["_meta"]["duration_ms"] = duration_ms
            result.data["_meta"]["tool"] = name
            if result.error:
                result.error = redact_secrets(result.error)
            return result


class MCPServerAdapter(ABC):
    """Optional adapter for exposing the registry as an MCP server process."""

    @abstractmethod
    def export_manifest(self) -> dict[str, Any]:
        raise NotImplementedError


class InProcessMCPAdapter(MCPServerAdapter):
    """In-process MCP surface used by agents (no extra process required)."""

    def __init__(self, registry: MCPRegistry) -> None:
        self.registry = registry

    def export_manifest(self) -> dict[str, Any]:
        return {
            "name": "forgeagent-mcp",
            "version": "0.1.0",
            "tools": self.registry.list_specs(),
        }

    def tools_as_llm_specs(self) -> list[dict[str, Any]]:
        return [
            {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["inputSchema"],
            }
            for t in self.registry.list_specs()
        ]


def dump_tool_result_for_log(result: ToolResult) -> str:
    return redact_secrets(json.dumps(result.to_structured(), default=str)[:4000])
