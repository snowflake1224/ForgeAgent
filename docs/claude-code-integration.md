# Claude Code integration

`ClaudeCodeBackend` is an adapter for the real Claude Code CLI. It does not
simulate Claude Code and does not route requests through the mock provider.

## Requirements

1. Install Claude Code using Anthropic's supported installation path.
2. Authenticate the CLI independently.
3. Set `FORGE_CLAUDE_CODE_ENABLED=true`.
4. Set `FORGE_CODING_BACKEND=claude_code`.
5. Set `FORGE_CLAUDE_CODE_COMMAND` if the executable is not named `claude`.
6. Prepare a dedicated worktree or workspace with appropriate permissions.

`available` is true only when integration is enabled and the executable can be
found. `run_coding_task` writes an auditable prompt under `.forge/`, invokes
the CLI with print mode and the task prompt, captures stdout/stderr, and expects
the real CLI to modify the configured workspace in place. ForgeAgent then owns
test, bounded debugging, review, final validation, and report phases. A
non-zero exit, timeout, or successful invocation that makes no file changes
terminates the implementation phase with a structured failure.

## Security

Claude Code has its own permission and sandbox model. Enabling this adapter does
not make host execution safe. Operators must review the exact installed CLI
version and flags, avoid broad host credentials, use isolated worktrees, and
apply normal patch review and validation.

The orchestrator selects this backend through `FORGE_CODING_BACKEND`. The
default `model` backend instead requests structured whole-file edits from the
configured provider and validates every path through the MCP filesystem policy.
