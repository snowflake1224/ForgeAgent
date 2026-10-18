# Architecture

ForgeAgent separates orchestration policy from model providers, executable
skills, tools, execution backends, persistence, and evaluation.

## Runtime flow

1. A `TaskSpec` supplies issue text, repository location, architecture, and budgets.
2. `Orchestrator.run(task, workspace_root=...)` creates bounded run state.
3. `SkillRuntime` performs issue and repository analysis, implementation, tests,
   diagnosis, debugging, review, validation, and commit preparation.
   Implementation is delegated either to a provider-neutral structured-edit
   model backend or the real Claude Code CLI backend.
4. Skills invoke registered MCP tools through `MCPRegistry`; the registry applies
   policy, JSON Schema validation, timeout, tracing, and redaction.
5. Test commands execute through Docker or the explicitly unsafe local backend.
6. The orchestrator returns an `ExecutionReport` with steps, patch, tests, review,
   measured metrics, status, and failure category.

## Comparable architectures

- **A — single agent:** one logical role runs the pipeline with one repair attempt.
- **B — planner/coder:** separates planning and coding with two repair attempts.
- **C — planner/coder/reviewer:** adds independent patch review.
- **D — specialized:** adds repository analysis, code search, test, debugging,
  and review roles, with three bounded repair attempts.

These variants share providers, tools, budgets, and report schemas so benchmark
comparisons can control more variables. No loop is unbounded.

## Components

- `config`: environment-backed settings.
- `domain`: canonical enums and Pydantic contracts.
- `models`: mock, OpenAI-compatible, and Anthropic providers using a common
  structured-edit protocol for coding.
- `integrations`: selectable external coding backends, including Claude Code.
- `mcp`: structured tool registry and built-ins.
- `execution`: Docker, local, and fallback executors.
- `skills`: explicit skill catalog and executable runtime.
- `orchestration`: architecture transitions and budgets.
- `storage`: task, report, and benchmark persistence.
- `evaluation`: fixture preparation, runs, aggregation, and reports.
- `observability`: OpenTelemetry spans and Prometheus metrics.

The current API awaits local mock runs directly. Production deployments should
move execution to authenticated workers with durable queueing and cancellation.
