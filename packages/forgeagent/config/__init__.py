"""Typed configuration for ForgeAgent."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime settings. Loaded from env vars and optional .env file."""

    model_config = SettingsConfigDict(
        env_prefix="FORGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # Persistence
    database_url: str = "sqlite+aiosqlite:///./data/forgeagent.db"
    redis_url: str | None = "redis://localhost:6379/0"

    # LLM
    llm_provider: Literal["mock", "openai", "anthropic", "azure_openai"] = "mock"
    llm_model: str = "mock-gpt"
    llm_api_key: SecretStr | None = None
    llm_base_url: str | None = None
    llm_max_tokens: int = 4096
    llm_temperature: float = 0.0
    coding_backend: Literal["model", "claude_code"] = "model"

    # Budgets
    max_agent_iterations: int = 12
    max_tool_calls: int = 80
    token_budget: int = 200_000
    task_timeout_seconds: int = 1800
    context_budget_tokens: int = 32_000

    # Sandbox
    sandbox_backend: Literal["docker", "local"] = "docker"
    sandbox_image: str = "forgeagent/sandbox:latest"
    sandbox_cpu_limit: float = 2.0
    sandbox_memory_limit_mb: int = 2048
    sandbox_timeout_seconds: int = 300
    sandbox_network_disabled: bool = True
    sandbox_allow_local_fallback: bool = False

    # GitHub
    github_token: SecretStr | None = None
    github_api_url: str = "https://api.github.com"

    # Observability
    otel_enabled: bool = True
    otel_exporter_otlp_endpoint: str = "http://localhost:4318"
    metrics_enabled: bool = True
    metrics_port: int = 9090

    # Claude Code external backend
    claude_code_enabled: bool = False
    claude_code_command: str = "claude"
    claude_code_workdir: str | None = None
    claude_code_timeout_seconds: int = 900

    # Security
    tool_policy_default: Literal["deny", "allow"] = "deny"
    allowed_tool_prefixes: str = (
        "repo.,fs.,git.,issue.,test.,static.,docs.,inspect."
    )
    secrets_redaction_enabled: bool = True

    # Paths
    data_dir: Path = Field(default=Path("./data"))
    artifacts_dir: Path = Field(default=Path("./artifacts"))
    runs_dir: Path = Field(default=Path("./runs"))

    @field_validator("api_cors_origins", mode="before")
    @classmethod
    def _strip_origins(cls, value: object) -> object:
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.api_cors_origins.split(",") if o.strip()]

    @property
    def allowed_tool_prefix_list(self) -> list[str]:
        return [p.strip() for p in self.allowed_tool_prefixes.split(",") if p.strip()]

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings


def reset_settings_cache() -> None:
    get_settings.cache_clear()
