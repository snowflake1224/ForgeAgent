# Evaluation

Evaluation runs the same tasks against one or more `ArchitectureKind` values
and records structured `TaskMetrics`. A benchmark YAML captures fixture,
issue, validation commands, time and token budgets, model identity, and skill
and tool versions.

`BenchmarkRunner` copies each fixture into a clean per-architecture workspace,
initializes a baseline git repository, invokes `Orchestrator`, aggregates the
returned reports, and writes:

- `result.json`: machine-readable config metadata, task rows, reports, and aggregates
- `report.md`: a human-readable rendering of that actual run

Metrics include completion, patch acceptance, regression detection, test pass
rate, iterations, tool calls, invalid tool-call rate, wall-clock duration,
token use, estimated cost, and failure category.

## Reproducibility

Record the benchmark and tool versions, model/provider, git commit, environment,
and sandbox image. Compare architectures only under matched task and runtime
conditions. A smoke fixture validates wiring; it does not establish external
validity or architecture superiority.

Run:

```bash
forge benchmark run benchmarks/configs/smoke_calc.yaml
forge benchmark compare <experiment-a> <experiment-b>
```

The comparison reports A-minus-B deltas without declaring a winner.
