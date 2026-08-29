# Security

ForgeAgent treats repository contents, issue text, model output, and generated
commands as untrusted.

## Trust boundaries

Docker is the intended execution boundary. It applies CPU, memory, PID, timeout,
and optional network restrictions and removes containers after execution.
Review the sandbox image, Docker daemon permissions, mount mode, and host kernel
before processing hostile repositories.

`LocalProcessExecutor` runs on the host. It is **not** a sandbox and is only
appropriate for trusted fixtures and development.

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

## Operational requirements

Authenticate and authorize API access before exposing it beyond localhost.
Use per-run worktrees, read-only source inputs where possible, isolated Docker
workers, egress deny-by-default, short-lived credentials, encrypted storage,
and centralized audit logs. Never mount the Docker socket into a sandbox.

Redaction patterns are defense in depth, not a complete secret detector.
Human review remains mandatory before applying or publishing generated patches.
