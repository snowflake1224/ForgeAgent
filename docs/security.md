# Security

ForgeAgent treats repository contents, issue text, model output, and generated
commands as untrusted.

## Trust boundaries

Docker is the intended execution boundary. It applies CPU, memory, PID, timeout,
network, read-only-root, no-new-privileges, dropped-capability, and temporary
filesystem restrictions and removes containers after execution. Docker
unavailability fails closed by default.
Review the sandbox image, Docker daemon permissions, mount mode, and host kernel
before processing hostile repositories.

`LocalProcessExecutor` runs on the host. It is **not** a sandbox and is only
appropriate for trusted fixtures and development. It is selected only through
`FORGE_SANDBOX_BACKEND=local` or the explicit
`FORGE_SANDBOX_ALLOW_LOCAL_FALLBACK=true` escape hatch.

## Controls

- Tool policy defaults to deny with explicit allowed prefixes.
- Tool inputs are validated against JSON Schema.
- Workspace paths reject absolute paths, traversal, and null bytes.
- Resolved paths must remain beneath the workspace root.
- Structured argument vectors avoid shell interpretation.
- Tool and execution timeouts bound operations.
- Prompt content is labeled as untrusted.
- Common credentials are redacted from tool errors and logs.
- Task iterations, tokens, tool calls, and wall time are bounded.
- Model-generated edits are restricted to repository-analysis-selected paths,
  size-limited, and written through MCP path authorization.
- Claude Code backend time is bounded and no-change executions are failures.

## Operational requirements

Authenticate and authorize API access before exposing it beyond localhost.
Use per-run worktrees, read-only source inputs where possible, isolated Docker
workers, egress deny-by-default, short-lived credentials, encrypted storage,
and centralized audit logs. Never mount the Docker socket into a sandbox.

Redaction patterns are defense in depth, not a complete secret detector.
Human review remains mandatory before applying or publishing generated patches.
