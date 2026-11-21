# Low-Level Design

## 1. Package Layout (`packages/forgeagent/`)

| Module | Responsibility | Key Types &amp; Entrypoints |
| --- | --- | --- |
| `config` | Environment-backed settings (`FORGE_*`) | `Settings`, `get_settings`, `reset_settings_cache` |
| `domain` | Canonical enums &amp; Pydantic models | `ArchitectureKind`, `TaskSpec`, `ExecutionBudget`, `ExecutionReport`, `ToolCallRecord`, `AgentStepRecord`, `ReviewVerdict` |
| `orchestration` | Variants A–D, budget guards, report assembly | `Orchestrator`, `RunState`, `BudgetExceededError` |
| `skills` | 11 formal skill contracts &amp; executable runtime | `SkillName`, `SkillSpec`, `SKILL_CATALOG`, `SkillRuntime` |
| `mcp` | Schema-validated, policy-gated tool gateway | `MCPRegistry`, `MCPTool`, `InProcessMCPAdapter`, `register_builtin_tools` (18 tools) |
| `repository` | Targeted repository exploration under token budgets | `RepositoryIntelligence`, `FileHit` |
| `models` | Vendor-neutral chat completion abstraction | `LLMProvider`, `MockLLMProvider`, `OpenAICompatibleProvider`, `AnthropicProvider`, `create_llm_provider` |
| `integrations` | External coding CLI backend delegation | `ClaudeCodeBackend` |
| `execution` | Fail-closed Docker container sandbox &amp; host fallback | `DockerSandboxExecutor`, `LocalProcessExecutor`, `FallbackSandboxExecutor` |
| `security` | Workspace path confinement, redaction, injection wrapping | `ToolPolicy`, `resolve_under_root`, `redact_secrets`, `sanitize_prompt_untrusted` |
| `storage` | Sync SQLAlchemy ORM + run/benchmark artifact persistence | `Store`, `TaskRow`, `RunRow`, `BenchmarkRunRow` |
| `observability` | OpenTelemetry tracer &amp; Prometheus metrics | `setup_observability`, `span`, `timed_histogram`, `TASKS_TOTAL`, `MODEL_LATENCY` |
| `evaluation` | Reproducible benchmark runner &amp; A–B deltas | `BenchmarkRunner`, `BenchmarkConfig`, `ExperimentResult`, `compare_experiments` |
| `api` &amp; `cli` | FastAPI control plane + Typer `forge` CLI + static UI (`apps/web/`) | `create_task`, `run_task`, `lifespan`, `forge task run`, `forge benchmark run` |

## 2. Budget Enforcement &amp; Deterministic Termination

Before every skill transition in `Orchestrator._run_pipeline`, `_check_budgets(state, runtime)` checks four independent bounds:

1. `time.perf_counter() - state.started_at > budget.timeout_seconds` → raises `BudgetExceededError(FailureCategory.TIMEOUT)`
2. `state.iterations > budget.max_iterations` → raises `BudgetExceededError(FailureCategory.BUDGET_EXCEEDED)`
3. `runtime.tool_call_count > budget.max_tool_calls` → raises `BudgetExceededError(FailureCategory.BUDGET_EXCEEDED)`
4. `runtime.tokens_in + runtime.tokens_out > budget.token_budget` → raises `BudgetExceededError(FailureCategory.BUDGET_EXCEEDED)`

Each executed step captures `(tokens_in, tokens_out)` deltas from `SkillRuntime` into `AgentStepRecord`, and every MCP invocation appends a secret-redacted `ToolCallRecord` (`tool_name`, `arguments`, `success`, `duration_ms`, `invalid`, `agent_role`) onto `ExecutionReport.tool_calls`.

## 3. Coding Edit Protocol &amp; Patch Sync

- **Model coding backend** (`_run_model_implementation`): Reads at most 6 files chosen by `RepositoryIntelligence.select_relevant_files`, wraps content in `<untrusted_content source="coding_context">`, prompts for strict JSON (`{"summary", "edits": [{"path", "content"}]}`), rejects any path outside `allowed_paths` (incrementing `invalid_tool_calls`), and writes replacements via `fs.write` (which verifies `resolve_under_root` and disables Windows newline translation via `newline=""`).
- **Patch review gate** (`run_patch_review`): Evaluates `git.diff` + `test_results`. Explicit `REVIEW_VERDICT: rejected` cannot be overridden by passing tests; failing tests always block approval.
- **Patch artifact filter**: Both `_files_from_status` and `Orchestrator._sync_patch` skip `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `.pyc`, and `.pyo` artifacts when enumerating changed files from `git status --porcelain`.

## 4. Infrastructure &amp; Dependency Notes

- **Database driver behavior**: `Store` rewrites `sqlite+aiosqlite:///` to `sqlite:///` and `postgresql+asyncpg://` to `postgresql+psycopg://` for synchronous SQLAlchemy persistence (`runs/<run_id>/report.json` and `diff.patch` are also written to disk).
- **Declared planned dependencies**: `pyproject.toml` lists `redis`, `tenacity`, `asyncpg`, and `structlog` for planned distributed worker queuing and retry layers; the current API runs tasks inline without an active Redis worker loop.
- **Observability startup**: `setup_observability` runs inside FastAPI `lifespan` and Typer CLI `_settings()` initialization when `env != "test"`. Console span export writes to `stderr` so CLI JSON output on `stdout` stays valid machine-parseable JSON.
