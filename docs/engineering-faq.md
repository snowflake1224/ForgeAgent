# Engineering FAQ

This guide prepares engineering walkthroughs and technical deep-dives on ForgeAgent's architecture, security model, ablation evaluation design, and engineering trade-offs.

---

## 1. 60-Second System Elevator Pitch

> ForgeAgent is an experimental autonomous software-engineering platform built to answer a controlled empirical question: *under identical tools, budgets, and models, when does specialized multi-agent orchestration actually outperform a single agent?*
>
> Instead of hardcoding one opaque agent loop, ForgeAgent cleanly separates **4 comparable orchestration architectures (A–D)** from **11 typed skills**, an **18-tool schema-validated MCP registry**, a **fail-closed Docker sandbox**, and a **YAML-driven evaluation harness** that writes immutable `result.json` + `report.md` artifacts.

---

## 2. Core Design Questions &amp; Concrete Answers

### Q1: Why separate Architectures A–D instead of only building D?
- Multi-agent frameworks often conflate **more agents** with **better results** while simultaneously changing prompts, tools, or retry counts.
- In `Orchestrator._execute_architecture`, all four variants (`A_single_agent`, `B_planner_coder`, `C_planner_coder_reviewer`, `D_specialized_multi_agent`) invoke the **exact same `SkillRuntime` and `MCPRegistry`** under identical `ExecutionBudget` caps.
- This isolates three independent variables:
  1. Repair loop depth (`1` in A &rarr; `2` in B/C &rarr; `3` in D)
  2. Independent patch review gate (added in C &amp; D)
  3. Dedicated AST/text code-search step (added in D)

### Q2: How does ForgeAgent build context without dumping an entire repository into the prompt?
- `RepositoryIntelligence.select_relevant_files` tokenizes the issue (`[A-Za-z_][A-Za-z0-9_]{2,}`), skips build/VCS noise (`SKIP_DIRS`), and scores files deterministically:
  `score = 3 * filename_hits + 2 * path_hits + 1 * content_hits`
- Files are packed greedily into `context_budget_tokens` using `len(text) // 4` estimation, plus `repo.search_symbol` parses Python `ast.parse` definitions when exact symbol lookups run in Variant D.

### Q3: Walk through the untrusted code / prompt-injection threat model.
1. **Tool allowlist &amp; schema check:** `ToolPolicy` defaults to `deny`, permitting only `allowed_tool_prefixes` (`repo., fs., git., issue., test., static., docs., inspect.`). `MCPRegistry.invoke` runs Draft 2020-12 JSON Schema validation before invoking handlers.
2. **Path confinement:** `resolve_under_root` strips null bytes, denies absolute paths and `..` segments, and requires `target.relative_to(root_resolved)`. In addition, `_run_model_implementation` restricts model writes strictly to the paths already selected during repository analysis (`allowed_paths`).
3. **Structured subprocesses:** `ExecutionRequest.command` is passed as an argv list to `create_subprocess_exec` or container `command`, avoiding shell expansion (`SHELL_META` rejection adds defense in depth).
4. **Fail-closed Docker sandbox:** `DockerSandboxExecutor` enforces `--network=none`, `--read-only`, `cap_drop=["ALL"]`, `no-new-privileges:true`, `--pids-limit=256`, CPU/memory caps, and forced container removal. Missing Docker fails closed (`exit_code=125`) unless `FORGE_SANDBOX_BACKEND=local` or `FORGE_SANDBOX_ALLOW_LOCAL_FALLBACK=true` is explicitly chosen.
5. **Untrusted prompt wrapping &amp; secret redaction:** Prompts wrap issue text, repo files, and failing logs inside `<untrusted_content source="...">`, and `redact_secrets` masks API/token patterns (`sk-`, `ghp_`, `xox*`, password keys) before persisting errors or `ToolCallRecord`s.

### Q4: Why whole-file JSON replacements instead of unified diff patches for the model backend?
- Small or mid-tier LLMs frequently emit malformed line numbers or off-by-one hunk headers in unified diffs. Returning `{"edits": [{"path", "content"}]}` is schema-validated once, guaranteed to apply cleanly, and limited to `<= 500 KB` per file and `<= 10` edits per turn.

### Q5: What real-world bugs did static inspection uncover, and how were they fixed?
1. **`setup_observability()` unwired:** Defined in `observability/__init__.py` but never invoked at startup &mdash; now invoked in FastAPI `lifespan` and Typer CLI `_settings()` when `env != "test"`, with console span output directed to `stderr` so CLI JSON output stays valid.
2. **Reviewer override bug:** `run_patch_review` previously overrode `REVIEW_VERDICT: rejected` whenever `tests_ok` was true &mdash; fixed so explicit rejections always block approval.
3. **Missing run telemetry details:** `ExecutionReport.tool_calls` was previously empty and per-step `tokens_in`/`tokens_out` stayed `0` &mdash; now populated from `SkillRuntime` with redacted arguments and exact per-step token deltas.
4. **Stored-XSS vector in `/ui/` &amp; cache noise:** `apps/web/app.js` replaced raw `innerHTML` interpolation with DOM `textContent` nodes, and `git status --porcelain` filtering strips `__pycache__` / `.pytest_cache` artifacts.

### Q6: What would you change for multi-tenant production deployment?
- Replace inline `await Orchestrator(...).run(...)` inside FastAPI `POST /tasks/{id}/runs` with an authenticated worker queue (using the declared `redis` dependency), isolated ephemeral git worktrees per run, egress-restricted sandbox pools, and real SWE-bench Verified evaluation suites across Anthropic/OpenAI models (since `smoke_calc.yaml` uses the deterministic mock provider).
