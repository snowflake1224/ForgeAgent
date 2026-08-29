# MCP tool layer

ForgeAgent's in-process MCP layer keeps tool contracts independent from agent
roles. `MCPTool` defines a name, description, JSON Schema, async handler,
timeout, and tags. `MCPRegistry.invoke` performs authorization, schema
validation, timeout enforcement, structured error conversion, tracing, and
secret redaction.

## Built-in tools

- Repository: `repo.tree`, `repo.search_text`, `repo.search_filename`,
  `repo.search_symbol`, `repo.select_relevant`, `repo.discover_tests`,
  `repo.dependencies`
- Filesystem: `fs.read`, `fs.write`, `fs.list`
- Git: `git.status`, `git.diff`, `git.checkout`
- Workflow: `issue.get`, `test.run`, `static.analyze`, `docs.search`,
  `inspect.structure`

Filesystem paths are workspace-relative and resolved beneath the workspace
root. Commands use structured argument vectors and are delegated to the
configured executor. There is no API endpoint accepting arbitrary shell input.

## Policy

`FORGE_TOOL_POLICY_DEFAULT=deny` is the recommended default. Prefixes listed in
`FORGE_ALLOWED_TOOL_PREFIXES` opt tools in. `allow` is useful for trusted local
fixtures but broadens capability and should not be used as a substitute for
sandboxing.

`InProcessMCPAdapter` exports a manifest suitable for provider tool schemas.
Out-of-process MCP transport is not implemented yet.
