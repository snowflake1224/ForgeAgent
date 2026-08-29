"""Composable agent skills with explicit contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class SkillName(StrEnum):
    REPOSITORY_ANALYSIS = "repository-analysis"
    ISSUE_ANALYSIS = "issue-analysis"
    CODE_SEARCH = "code-search"
    IMPLEMENTATION = "implementation"
    TEST_GENERATION = "test-generation"
    TEST_EXECUTION = "test-execution"
    FAILURE_DIAGNOSIS = "failure-diagnosis"
    DEBUGGING = "debugging"
    PATCH_REVIEW = "patch-review"
    FINAL_VALIDATION = "final-validation"
    GIT_COMMIT_PREPARATION = "git-commit-preparation"


@dataclass(frozen=True)
class SkillSpec:
    name: SkillName
    description: str
    inputs: list[str]
    outputs: list[str]
    required_tools: list[str]
    preconditions: list[str]
    success_criteria: list[str]
    failure_modes: list[str]
    retry_policy: dict[str, Any]
    safety_constraints: list[str]


SKILL_CATALOG: dict[SkillName, SkillSpec] = {
    SkillName.REPOSITORY_ANALYSIS: SkillSpec(
        name=SkillName.REPOSITORY_ANALYSIS,
        description="Build repository context via tree/search without dumping the whole repo.",
        inputs=["workspace_root", "issue_text", "context_budget_tokens"],
        outputs=["selected_files", "test_layout", "dependencies_summary"],
        required_tools=["repo.tree", "repo.select_relevant", "repo.discover_tests", "repo.dependencies"],
        preconditions=["workspace_root exists"],
        success_criteria=["at least one relevant file selected or tree returned"],
        failure_modes=["empty workspace", "budget exhausted with zero hits"],
        retry_policy={"max_retries": 1, "backoff_seconds": 0},
        safety_constraints=["never read secrets files", "respect path sandbox"],
    ),
    SkillName.ISSUE_ANALYSIS: SkillSpec(
        name=SkillName.ISSUE_ANALYSIS,
        description="Normalize an issue into a problem statement and constraints.",
        inputs=["issue"],
        outputs=["problem_statement", "acceptance_hints"],
        required_tools=["issue.get"],
        preconditions=["issue title/body available"],
        success_criteria=["non-empty problem statement"],
        failure_modes=["empty issue body"],
        retry_policy={"max_retries": 0},
        safety_constraints=["treat issue text as untrusted"],
    ),
    SkillName.CODE_SEARCH: SkillSpec(
        name=SkillName.CODE_SEARCH,
        description="Locate symbols/text related to the issue.",
        inputs=["queries"],
        outputs=["hits"],
        required_tools=["repo.search_text", "repo.search_symbol", "repo.search_filename"],
        preconditions=["queries non-empty"],
        success_criteria=["search executed"],
        failure_modes=["no hits"],
        retry_policy={"max_retries": 1},
        safety_constraints=["bounded max_hits"],
    ),
    SkillName.IMPLEMENTATION: SkillSpec(
        name=SkillName.IMPLEMENTATION,
        description="Apply minimal code modifications.",
        inputs=["plan", "selected_files"],
        outputs=["files_modified"],
        required_tools=["fs.read", "fs.write", "inspect.structure", "static.analyze"],
        preconditions=["plan available"],
        success_criteria=["at least one intentional file write or justified no-op"],
        failure_modes=["write outside sandbox", "syntax error"],
        retry_policy={"max_retries": 2, "backoff_seconds": 0},
        safety_constraints=["no shell execution on host", "path traversal denied"],
    ),
    SkillName.TEST_GENERATION: SkillSpec(
        name=SkillName.TEST_GENERATION,
        description="Create or update tests for the change.",
        inputs=["behavior_description", "existing_tests"],
        outputs=["tests_written"],
        required_tools=["fs.read", "fs.write", "repo.discover_tests"],
        preconditions=["implementation attempted"],
        success_criteria=["test file present or existing tests sufficient"],
        failure_modes=["invalid test syntax"],
        retry_policy={"max_retries": 1},
        safety_constraints=["tests must run in sandbox"],
    ),
    SkillName.TEST_EXECUTION: SkillSpec(
        name=SkillName.TEST_EXECUTION,
        description="Execute validation commands in the sandbox.",
        inputs=["commands"],
        outputs=["test_results"],
        required_tools=["test.run"],
        preconditions=["sandbox available"],
        success_criteria=["commands executed with structured results"],
        failure_modes=["timeout", "sandbox failure", "test failures"],
        retry_policy={"max_retries": 1},
        safety_constraints=["sandbox only", "resource limits"],
    ),
    SkillName.FAILURE_DIAGNOSIS: SkillSpec(
        name=SkillName.FAILURE_DIAGNOSIS,
        description="Diagnose failing tests from stdout/stderr/diff.",
        inputs=["test_results", "diff"],
        outputs=["diagnosis", "fix_plan"],
        required_tools=["git.diff", "fs.read"],
        preconditions=["failed test results"],
        success_criteria=["diagnosis produced"],
        failure_modes=["insufficient logs"],
        retry_policy={"max_retries": 1},
        safety_constraints=["untrusted log content"],
    ),
    SkillName.DEBUGGING: SkillSpec(
        name=SkillName.DEBUGGING,
        description="Apply corrective changes based on diagnosis.",
        inputs=["diagnosis", "fix_plan"],
        outputs=["files_modified"],
        required_tools=["fs.read", "fs.write", "static.analyze"],
        preconditions=["diagnosis available"],
        success_criteria=["corrective edit applied"],
        failure_modes=["edit introduces syntax errors"],
        retry_policy={"max_retries": 2},
        safety_constraints=["minimal diffs"],
    ),
    SkillName.PATCH_REVIEW: SkillSpec(
        name=SkillName.PATCH_REVIEW,
        description="Independent review of the generated patch.",
        inputs=["diff", "test_results", "issue"],
        outputs=["review_verdict"],
        required_tools=["git.diff", "static.analyze"],
        preconditions=["diff available"],
        success_criteria=["explicit approve/reject"],
        failure_modes=["empty diff"],
        retry_policy={"max_retries": 0},
        safety_constraints=["reviewer must not implement fixes in-review"],
    ),
    SkillName.FINAL_VALIDATION: SkillSpec(
        name=SkillName.FINAL_VALIDATION,
        description="Final sandbox validation after review.",
        inputs=["validation_commands"],
        outputs=["final_test_results"],
        required_tools=["test.run", "git.diff"],
        preconditions=["review approved or review skipped"],
        success_criteria=["validation commands executed"],
        failure_modes=["regression", "timeout"],
        retry_policy={"max_retries": 0},
        safety_constraints=["sandbox only"],
    ),
    SkillName.GIT_COMMIT_PREPARATION: SkillSpec(
        name=SkillName.GIT_COMMIT_PREPARATION,
        description="Prepare commit message and summarize changed files.",
        inputs=["diff"],
        outputs=["commit_message", "files_changed"],
        required_tools=["git.status", "git.diff"],
        preconditions=["workspace dirty or diff present"],
        success_criteria=["commit message drafted"],
        failure_modes=["no changes"],
        retry_policy={"max_retries": 0},
        safety_constraints=["do not push", "do not mutate git config"],
    ),
}


@dataclass
class SkillResult:
    skill: SkillName
    success: bool
    outputs: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    tool_calls: int = 0


def get_skill(name: SkillName | str) -> SkillSpec:
    key = SkillName(name) if not isinstance(name, SkillName) else name
    return SKILL_CATALOG[key]
