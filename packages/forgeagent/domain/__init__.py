"""Shared domain models and enums."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str = "") -> str:
    uid = uuid4().hex[:12]
    return f"{prefix}{uid}" if prefix else uid


class ArchitectureKind(StrEnum):
    """Comparable agent architectures."""

    SINGLE = "A_single_agent"
    PLANNER_CODER = "B_planner_coder"
    PLANNER_CODER_REVIEWER = "C_planner_coder_reviewer"
    SPECIALIZED = "D_specialized_multi_agent"


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class AgentRole(StrEnum):
    ORCHESTRATOR = "orchestrator"
    PLANNER = "planner"
    REPOSITORY_ANALYST = "repository_analyst"
    CODING = "coding"
    TEST = "test"
    DEBUGGING = "debugging"
    REVIEWER = "reviewer"
    SINGLE = "single"


class FailureCategory(StrEnum):
    NONE = "none"
    PLANNING = "planning"
    IMPLEMENTATION = "implementation"
    TEST_FAILURE = "test_failure"
    REVIEW_REJECTED = "review_rejected"
    TOOL_ERROR = "tool_error"
    SANDBOX_ERROR = "sandbox_error"
    BUDGET_EXCEEDED = "budget_exceeded"
    TIMEOUT = "timeout"
    INVALID_TOOL_CALL = "invalid_tool_call"
    SECURITY_VIOLATION = "security_violation"
    UNKNOWN = "unknown"


class IssueSpec(BaseModel):
    """Normalized issue / task description."""

    title: str
    body: str
    url: str | None = None
    labels: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RepoSpec(BaseModel):
    """Repository location and revision."""

    path: str | None = None
    url: str | None = None
    commit: str | None = None
    language: str | None = None


class TaskSpec(BaseModel):
    """A unit of work submitted to ForgeAgent."""

    task_id: str = Field(default_factory=lambda: new_id("task_"))
    issue: IssueSpec
    repo: RepoSpec
    architecture: ArchitectureKind = ArchitectureKind.SPECIALIZED
    timeout_seconds: int | None = None
    token_budget: int | None = None
    max_iterations: int | None = None
    max_tool_calls: int | None = None
    validation_commands: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)


class ExecutionBudget(BaseModel):
    max_iterations: int = 12
    max_tool_calls: int = 80
    token_budget: int = 200_000
    timeout_seconds: int = 1800
    context_budget_tokens: int = 32_000


class ToolCallRecord(BaseModel):
    call_id: str = Field(default_factory=lambda: new_id("tc_"))
    tool_name: str
    arguments: dict[str, Any]
    success: bool
    result_summary: str = ""
    error: str | None = None
    duration_ms: float = 0.0
    invalid: bool = False
    agent_role: AgentRole | None = None
    timestamp: datetime = Field(default_factory=utcnow)


class AgentStepRecord(BaseModel):
    step_id: str = Field(default_factory=lambda: new_id("step_"))
    role: AgentRole
    skill: str | None = None
    input_summary: str = ""
    output_summary: str = ""
    success: bool = True
    error: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    duration_ms: float = 0.0
    timestamp: datetime = Field(default_factory=utcnow)


class TestResultSummary(BaseModel):
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: int = 0
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    duration_ms: float = 0.0
    timed_out: bool = False
    command: str = ""


class PatchArtifact(BaseModel):
    diff: str = ""
    files_changed: list[str] = Field(default_factory=list)
    commit_message: str | None = None


class ReviewVerdict(BaseModel):
    approved: bool
    issues: list[str] = Field(default_factory=list)
    summary: str = ""
    risk_level: Literal["low", "medium", "high"] = "low"


class ExecutionMetrics(BaseModel):
    iterations: int = 0
    tool_calls: int = 0
    invalid_tool_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    estimated_cost_usd: float = 0.0
    wall_clock_ms: float = 0.0
    test_pass_rate: float | None = None
    regression_detected: bool = False
    failure_category: FailureCategory = FailureCategory.NONE


class ExecutionReport(BaseModel):
    """Structured final report for a task execution."""

    run_id: str
    task_id: str
    architecture: ArchitectureKind
    status: TaskStatus
    problem_statement: str = ""
    plan_summary: str = ""
    patch: PatchArtifact = Field(default_factory=PatchArtifact)
    test_results: list[TestResultSummary] = Field(default_factory=list)
    review: ReviewVerdict | None = None
    metrics: ExecutionMetrics = Field(default_factory=ExecutionMetrics)
    steps: list[AgentStepRecord] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    selected_files: list[dict[str, str]] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
