# Experiments

ForgeAgent experiments are evidence artifacts, not marketing claims. Every
experiment should start from a versioned benchmark config and produce an
immutable `result.json` plus `report.md`.

## Recommended procedure

1. Pin repository revisions and the sandbox image.
2. Use identical tasks, commands, budgets, provider, and model across variants.
3. Run enough repetitions to characterize variance for nondeterministic models.
4. Preserve failures and timeouts rather than dropping them.
5. Compare completion, acceptance, regressions, cost, latency, and failure categories.
6. Inspect patches and test validity before interpreting aggregate rates.

The included `smoke_calc.yaml` is deterministic with the mock provider. It
checks that all four architecture paths can copy a fixture, repair code, execute
tests, and emit reports. It is intentionally too small to support conclusions
about real-world performance.

Artifacts are stored at `artifacts/experiments/<experiment-id>/`. Use the CLI
comparison command with either experiment IDs, directories, or result files.
Only values in generated artifacts should be quoted as run results.
