"""LLM provider abstraction — never hard-code a single vendor."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from forgeagent.config import Settings, get_settings


@dataclass
class Message:
    role: str  # system | user | assistant | tool
    content: str
    name: str | None = None
    tool_call_id: str | None = None


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class LLMUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: LLMUsage = field(default_factory=LLMUsage)
    model: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


class LLMProvider(ABC):
    """Provider-agnostic chat completion interface."""

    name: str

    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        raise NotImplementedError


class MockLLMProvider(LLMProvider):
    """Deterministic provider for tests and offline runs.

    Behavior is driven by message content keywords so orchestration can be
    exercised without network access. Never claims to be a real model.
    """

    name = "mock"

    def __init__(self, model: str = "mock-gpt") -> None:
        self.model = model
        self.call_count = 0

    async def complete(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        self.call_count += 1
        blob = "\n".join(m.content for m in messages).lower()
        prompt_tokens = max(1, len(blob) // 4)
        content = self._synthesize(blob, tools)
        completion_tokens = max(1, len(content) // 4)
        return LLMResponse(
            content=content,
            usage=LLMUsage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
            model=self.model,
            raw={"provider": "mock", "call": self.call_count},
        )

    def _synthesize(self, blob: str, tools: list[ToolSpec] | None) -> str:
        if "review" in blob and "patch" in blob:
            return (
                "REVIEW_VERDICT: approved\n"
                "RISK: low\n"
                "SUMMARY: Patch is minimal, tests cover the change, no unsafe operations.\n"
                "ISSUES: none"
            )
        if "diagnose" in blob or "failure" in blob and "stack" in blob:
            return (
                "DIAGNOSIS: Assertion failed due to incorrect return value.\n"
                "ROOT_CAUSE: Implementation does not match expected behavior.\n"
                "FIX_PLAN: Adjust the function to return the correct value and re-run tests."
            )
        if "plan" in blob or "planner" in blob:
            return (
                "PROBLEM: Resolve the described software issue.\n"
                "AREAS: source modules related to the failing behavior; existing tests.\n"
                "PLAN:\n"
                "1. Locate relevant symbols via search.\n"
                "2. Implement the minimal fix.\n"
                "3. Add/adjust tests.\n"
                "4. Run validation commands.\n"
                "FILES: to be determined by repository analysis\n"
                "TESTS: run project test suite\n"
                "RISKS: over-editing unrelated files"
            )
        if "implement" in blob or "coding" in blob or "modify" in blob:
            return (
                "IMPLEMENTATION_NOTES: Apply a minimal, targeted change.\n"
                "Use filesystem tools to edit only files identified by analysis.\n"
                "Do not rewrite unrelated modules."
            )
        if "analyze" in blob or "repository" in blob:
            return (
                "ANALYSIS: Inspect file tree, search for issue keywords, "
                "select a small set of relevant files within the context budget."
            )
        return (
            "Acknowledged. Proceed with the next skill using available tools. "
            "Prefer minimal changes and explicit validation."
        )


class OpenAICompatibleProvider(LLMProvider):
    """HTTP chat completions against OpenAI-compatible APIs."""

    name = "openai_compatible"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        provider_label: str = "openai",
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.provider_label = provider_label

    async def complete(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        import httpx

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": m.role, "content": m.content, **({"name": m.name} if m.name else {})}
                for m in messages
            ],
            "temperature": 0.0 if temperature is None else temperature,
            "max_tokens": max_tokens or 4096,
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        choice = data["choices"][0]["message"]
        tool_calls: list[dict[str, Any]] = []
        for tc in choice.get("tool_calls") or []:
            tool_calls.append(
                {
                    "id": tc.get("id"),
                    "name": tc["function"]["name"],
                    "arguments": tc["function"].get("arguments", "{}"),
                }
            )
        usage_raw = data.get("usage") or {}
        return LLMResponse(
            content=choice.get("content") or "",
            tool_calls=tool_calls,
            usage=LLMUsage(
                prompt_tokens=int(usage_raw.get("prompt_tokens") or 0),
                completion_tokens=int(usage_raw.get("completion_tokens") or 0),
            ),
            model=data.get("model") or self.model,
            raw=data,
        )


class AnthropicProvider(LLMProvider):
    """Anthropic Messages API provider."""

    name = "anthropic"

    def __init__(self, *, api_key: str, model: str, base_url: str | None = None) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = (base_url or "https://api.anthropic.com").rstrip("/")

    async def complete(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        import httpx

        system = "\n".join(m.content for m in messages if m.role == "system")
        user_msgs = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role in {"user", "assistant"}
        ]
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens or 4096,
            "temperature": 0.0 if temperature is None else temperature,
            "messages": user_msgs or [{"role": "user", "content": "Proceed."}],
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.parameters,
                }
                for t in tools
            ]

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{self.base_url}/v1/messages",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in data.get("content") or []:
            if block.get("type") == "text":
                text_parts.append(block.get("text") or "")
            elif block.get("type") == "tool_use":
                tool_calls.append(
                    {
                        "id": block.get("id"),
                        "name": block.get("name"),
                        "arguments": block.get("input") or {},
                    }
                )
        usage_raw = data.get("usage") or {}
        return LLMResponse(
            content="\n".join(text_parts),
            tool_calls=tool_calls,
            usage=LLMUsage(
                prompt_tokens=int(usage_raw.get("input_tokens") or 0),
                completion_tokens=int(usage_raw.get("output_tokens") or 0),
            ),
            model=data.get("model") or self.model,
            raw=data,
        )


def create_llm_provider(settings: Settings | None = None) -> LLMProvider:
    """Factory: select provider from settings without hard-coding a vendor."""
    cfg = settings or get_settings()
    if cfg.llm_provider == "mock":
        return MockLLMProvider(model=cfg.llm_model)

    api_key = cfg.llm_api_key.get_secret_value() if cfg.llm_api_key else ""
    if not api_key:
        raise ValueError(
            f"FORGE_LLM_API_KEY is required for provider {cfg.llm_provider}"
        )

    if cfg.llm_provider == "anthropic":
        return AnthropicProvider(
            api_key=api_key,
            model=cfg.llm_model,
            base_url=cfg.llm_base_url,
        )

    base = cfg.llm_base_url or "https://api.openai.com/v1"
    label = cfg.llm_provider
    return OpenAICompatibleProvider(
        api_key=api_key,
        model=cfg.llm_model,
        base_url=base,
        provider_label=label,
    )


# Rough public pricing estimates for cost accounting (USD per 1M tokens).
# Used only for estimates; not billed invoices.
COST_PER_MILLION: dict[str, tuple[float, float]] = {
    "mock-gpt": (0.0, 0.0),
    "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.6),
    "claude-sonnet-4-20250514": (3.0, 15.0),
    "claude-3-5-sonnet-latest": (3.0, 15.0),
}


def estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    inp, out = COST_PER_MILLION.get(model, (1.0, 3.0))
    return (prompt_tokens * inp + completion_tokens * out) / 1_000_000
