# Agent skills

Skills are named, testable workflow contracts. The catalog declares inputs,
outputs, required tools, preconditions, success criteria, failure modes, retry
policy, and safety constraints.

The eleven canonical skills are:

1. issue analysis
2. repository analysis
3. code search
4. implementation
5. test generation
6. test execution
7. failure diagnosis
8. debugging
9. patch review
10. final validation
11. git commit preparation

`SkillRuntime` implements each contract with methods named
`run_issue_analysis`, `run_repository_analysis`, `run_code_search`,
`run_implementation`, `run_test_generation`, `run_test_execution`,
`run_failure_diagnosis`, `run_debugging`, `run_patch_review`,
`run_final_validation`, and `run_git_commit_preparation`.

Runtime accounting tracks model tokens, estimated cost, tool calls, and invalid
tool calls. The mock implementation includes one deterministic fixture repair:
`return a - b` becomes `return a + b` in `src/calc.py`. This makes the smoke
benchmark reproducible; it is not presented as general-purpose code generation.
