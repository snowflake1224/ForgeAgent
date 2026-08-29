"""Orchestration engine with deterministic termination and architecture variants."""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from forgeagent.config import Settings, get_settings
from forgeagent.domain import (
    AgentRole,
    AgentStepRecord,
    ArchitectureKind,
    ExecutionBudget,
    ExecutionMetrics,
    ExecutionReport,
    FailureCategory,
    PatchArtifact,
    ReviewVerdict,
    TaskSpec,
    TaskStatus,
    TestResultSummary,
    new_id,
    utcnow,
)
from forgeagent.execution import create_executor
from forgeagent.mcp import InProcessMCPAdapter, MCPRegistry
from forgeagent.mcp.builtin import register_builtin_tools
from forgeagent.models import LLMProvider, create_llm_provider
from forgeagent.observability import AGENT_FAILURES, TASKS_TOTAL, span
from forgeagent.security import ToolPolicy
from forgeagent.skills.runtime import SkillRuntime


@dataclass
class RunState:
    run_id: str
    task: TaskSpec
    workspace_root: str
    budget: ExecutionBudget
    status: TaskStatus = TaskStatus.PENDING
    problem_statement: str = ""
    plan_summary: str = ""
    selected_files: list[dict[str, str]] = field(default_factory=list)
    test_results: list[dict[str, Any]] = field(default_factory=list)
    review: ReviewVerdict | None = None
    steps: list[AgentStepRecord] = field(default_factory=list)
    iterations: int = 0
    failure_category: FailureCategory = FailureCategory.NONE
    error: str | None = None
    started_at: float = 0.0
    commit_message: str | None = None
    patch_diff: str = ""
    files_changed: list[str] = field(default_factory=list)


class BudgetExceededError(Exception):
    def __init__(self, category: FailureCategory, message: str) -> None:
        super().__init__(message)
        self.category = category


class Orchestrator:
    """Controls agent transitions, budgets, retries, and termination.

    Architectures:
      A — single agent runs the full skill pipeline
      B — planner then coding (+ test/debug loop)
      C — B + independent reviewer
      D — specialized roles (analyst, planner, coder, tester, debugger, reviewer)
    """

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        llm: LLMProvider | None = None,
        registry: MCPRegistry | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.llm = llm or create_llm_provider(self.settings)
        self.executor = create_executor(self.settings)
        if registry is None:
            registry = MCPRegistry(policy=ToolPolicy(self.settings))
            register_builtin_tools(registry, executor=self.executor)
        self.registry = registry
        self.mcp_adapter = InProcessMCPAdapter(registry)

    def _budget_for(self, task: TaskSpec) -> ExecutionBudget:
        return ExecutionBudget(
            max_iterations=task.max_iterations or self.settings.max_agent_iterations,
            max_tool_calls=task.max_tool_calls or self.settings.max_tool_calls,
            token_budget=task.token_budget or self.settings.token_budget,
            timeout_seconds=task.timeout_seconds or self.settings.task_timeout_seconds,
            context_budget_tokens=self.settings.context_budget_tokens,
        )

    def _check_budgets(self, state: RunState, runtime: SkillRuntime) -> None:
        elapsed = time.perf_counter() - state.started_at
        if elapsed > state.budget.timeout_seconds:
            raise BudgetExceededError(FailureCategory.TIMEOUT, "Task wall-clock timeout exceeded")
        if state.iterations > state.budget.max_iterations:
            raise BudgetExceededError(
                FailureCategory.BUDGET_EXCEEDED, "Max agent iterations exceeded"
            )
        if runtime.tool_call_count > state.budget.max_tool_calls:
            raise BudgetExceededError(FailureCategory.BUDGET_EXCEEDED, "Max tool calls exceeded")
        if runtime.tokens_in + runtime.tokens_out > state.budget.token_budget:
            raise BudgetExceededError(FailureCategory.BUDGET_EXCEEDED, "Token budget exceeded")

    def _record_step(
        self,
        state: RunState,
        *,
        role: AgentRole,
        skill: str,
        success: bool,
        input_summary: str,
        output_summary: str,
        error: str | None = None,
        duration_ms: float = 0.0,
    ) -> None:
        state.steps.append(
            AgentStepRecord(
                role=role,
                skill=skill,
                success=success,
                input_summary=input_summary[:500],
                output_summary=output_summary[:1000],
                error=error,
                duration_ms=duration_ms,
            )
        )
        if not success:
            AGENT_FAILURES.labels(role.value).inc()

    async def run(self, task: TaskSpec, *, workspace_root: str | None = None) -> ExecutionReport:
        run_id = new_id("run_")
        root = workspace_root or task.repo.path
        if not root:
            raise ValueError("Task requires repo.path (workspace root)")
        Path(root).mkdir(parents=True, exist_ok=True)

        state = RunState(
            run_id=run_id,
            task=task,
            workspace_root=str(Path(root).resolve()),
            budget=self._budget_for(task),
            status=TaskStatus.RUNNING,
            started_at=time.perf_counter(),
        )
        runtime = SkillRuntime(
            self.registry,
            self.llm,
            workspace_root=state.workspace_root,
            run_id=run_id,
        )

        with span(
            "task.execution",
            attributes={
                "run.id": run_id,
                "task.id": task.task_id,
                "architecture": task.architecture.value,
            },
        ):
            try:
                await self._execute_architecture(state, runtime)
                if state.status == TaskStatus.RUNNING:
                    state.status = TaskStatus.SUCCEEDED
            except BudgetExceededError as exc:
                state.status = (
                    TaskStatus.TIMED_OUT
                    if exc.category == FailureCategory.TIMEOUT
                    else TaskStatus.FAILED
                )
                state.failure_category = exc.category
                state.error = str(exc)
            except Exception as exc:
                state.status = TaskStatus.FAILED
                state.failure_category = FailureCategory.UNKNOWN
                state.error = str(exc)

        report = self._build_report(state, runtime)
        TASKS_TOTAL.labels(report.status.value, task.architecture.value).inc()
        return report

    async def _execute_architecture(self, state: RunState, runtime: SkillRuntime) -> None:
        arch = state.task.architecture
        if arch == ArchitectureKind.SINGLE:
            await self._run_pipeline(state, runtime, repair_loops=1, review=False, specialized=False)
        elif arch == ArchitectureKind.PLANNER_CODER:
            await self._run_pipeline(state, runtime, repair_loops=2, review=False, specialized=False)
        elif arch == ArchitectureKind.PLANNER_CODER_REVIEWER:
            await self._run_pipeline(state, runtime, repair_loops=2, review=True, specialized=False)
        elif arch == ArchitectureKind.SPECIALIZED:
            await self._run_pipeline(state, runtime, repair_loops=3, review=True, specialized=True)
        else:
            raise ValueError(f"Unknown architecture: {arch}")

    async def _run_pipeline(
        self,
        state: RunState,
        runtime: SkillRuntime,
        *,
        repair_loops: int,
        review: bool,
        specialized: bool,
    ) -> None:
        role_plan = AgentRole.SINGLE if state.task.architecture == ArchitectureKind.SINGLE else AgentRole.PLANNER
        role_code = AgentRole.SINGLE if state.task.architecture == ArchitectureKind.SINGLE else AgentRole.CODING

        # Issue analysis / plan
        state.iterations += 1
        self._check_budgets(state, runtime)
        t0 = time.perf_counter()
        with span("agent.invocation", attributes={"role": role_plan.value}):
            analysis = await runtime.run_issue_analysis(state.task.issue.model_dump())
        state.problem_statement = str(analysis.outputs.get("problem_statement", ""))
        state.plan_summary = str(analysis.outputs.get("acceptance_hints", ""))[:2000]
        self._record_step(
            state,
            role=role_plan,
            skill=analysis.skill.value,
            success=analysis.success,
            input_summary=state.task.issue.title,
            output_summary=state.problem_statement,
            duration_ms=(time.perf_counter() - t0) * 1000,
        )

        # Repository analysis
        state.iterations += 1
        self._check_budgets(state, runtime)
        issue_text = f"{state.task.issue.title}\n{state.task.issue.body}"
        t0 = time.perf_counter()
        with span("agent.invocation", attributes={"role": AgentRole.REPOSITORY_ANALYST.value}):
            repo = await runtime.run_repository_analysis(
                issue_text,
                budget_tokens=state.budget.context_budget_tokens,
            )
        state.selected_files = list(repo.outputs.get("selected_files", []))
        self._record_step(
            state,
            role=AgentRole.REPOSITORY_ANALYST,
            skill=repo.skill.value,
            success=repo.success,
            input_summary="repository analysis",
            output_summary=f"selected={len(state.selected_files)}",
            error=repo.error,
            duration_ms=(time.perf_counter() - t0) * 1000,
        )

        if specialized:
            state.iterations += 1
            self._check_budgets(state, runtime)
            words = re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", issue_text)[:6]
            search = await runtime.run_code_search(words or ["test", "fix"])
            self._record_step(
                state,
                role=AgentRole.REPOSITORY_ANALYST,
                skill=search.skill.value,
                success=search.success,
                input_summary=str(words),
                output_summary=f"hits={len(search.outputs.get('hits', []))}",
            )

        # Implementation
        state.iterations += 1
        self._check_budgets(state, runtime)
        t0 = time.perf_counter()
        with span("agent.invocation", attributes={"role": role_code.value}):
            impl = await runtime.run_implementation(
                plan=state.plan_summary,
                selected_files=state.selected_files,
                issue_text=issue_text,
            )
        self._record_step(
            state,
            role=role_code,
            skill=impl.skill.value,
            success=impl.success,
            input_summary="implement",
            output_summary=str(impl.outputs.get("files_modified")),
            duration_ms=(time.perf_counter() - t0) * 1000,
        )

        # Test generation
        state.iterations += 1
        tg = await runtime.run_test_generation({"test_files": []})
        self._record_step(
            state,
            role=AgentRole.TEST,
            skill=tg.skill.value,
            success=tg.success,
            input_summary="test generation",
            output_summary=str(tg.outputs.get("note")),
        )

        commands = self._commands(state)
        for repair_i in range(repair_loops + 1):
            state.iterations += 1
            self._check_budgets(state, runtime)
            t0 = time.perf_counter()
            with span("agent.invocation", attributes={"role": AgentRole.TEST.value}):
                te = await runtime.run_test_execution(commands)
            state.test_results = list(te.outputs.get("test_results", []))
            self._record_step(
                state,
                role=AgentRole.TEST,
                skill=te.skill.value,
                success=te.success,
                input_summary=f"test loop={repair_i}",
                output_summary=f"success={te.success}",
                error=te.error,
                duration_ms=(time.perf_counter() - t0) * 1000,
            )
            if te.success:
                break
            if repair_i >= repair_loops:
                state.failure_category = FailureCategory.TEST_FAILURE
                state.error = te.error or "tests failed"
                break
            state.iterations += 1
            self._check_budgets(state, runtime)
            diag = await runtime.run_failure_diagnosis(state.test_results)
            self._record_step(
                state,
                role=AgentRole.DEBUGGING,
                skill=diag.skill.value,
                success=diag.success,
                input_summary="diagnose",
                output_summary=str(diag.outputs.get("diagnosis", ""))[:500],
            )
            state.iterations += 1
            deb = await runtime.run_debugging(str(diag.outputs.get("fix_plan", "")))
            self._record_step(
                state,
                role=AgentRole.DEBUGGING,
                skill=deb.skill.value,
                success=deb.success,
                input_summary="debug",
                output_summary=str(deb.outputs.get("files_modified")),
            )

        if review and state.status == TaskStatus.RUNNING and not state.error:
            state.iterations += 1
            self._check_budgets(state, runtime)
            t0 = time.perf_counter()
            with span("agent.invocation", attributes={"role": AgentRole.REVIEWER.value}):
                rev = await runtime.run_patch_review(
                    issue_text=issue_text,
                    test_results=state.test_results,
                )
            verdict_data = rev.outputs.get("review_verdict", {})
            state.review = ReviewVerdict.model_validate(verdict_data)
            state.patch_diff = str(rev.outputs.get("diff") or state.patch_diff)
            self._record_step(
                state,
                role=AgentRole.REVIEWER,
                skill=rev.skill.value,
                success=rev.success,
                input_summary="patch review",
                output_summary=f"approved={state.review.approved}",
                duration_ms=(time.perf_counter() - t0) * 1000,
            )
            if not state.review.approved:
                state.failure_category = FailureCategory.REVIEW_REJECTED
                state.error = "Reviewer rejected the patch"
                state.status = TaskStatus.FAILED
                return

        if state.status == TaskStatus.FAILED:
            return

        state.iterations += 1
        self._check_budgets(state, runtime)
        final = await runtime.run_final_validation(commands)
        state.test_results = list(final.outputs.get("final_test_results", state.test_results))
        state.patch_diff = str(final.outputs.get("diff") or state.patch_diff)
        self._record_step(
            state,
            role=AgentRole.ORCHESTRATOR,
            skill=final.skill.value,
            success=final.success,
            input_summary="final validation",
            output_summary=f"success={final.success}",
            error=final.error,
        )
        commit = await runtime.run_git_commit_preparation()
        state.commit_message = str(commit.outputs.get("commit_message") or "")
        state.files_changed = list(commit.outputs.get("files_changed") or [])
        state.patch_diff = str(commit.outputs.get("diff") or state.patch_diff)
        self._record_step(
            state,
            role=AgentRole.ORCHESTRATOR,
            skill=commit.skill.value,
            success=commit.success,
            input_summary="commit prep",
            output_summary=state.commit_message or "",
        )
        if not final.success:
            state.failure_category = FailureCategory.TEST_FAILURE
            state.error = final.error
            state.status = TaskStatus.FAILED

    def _commands(self, state: RunState) -> list[list[str]]:
        commands: list[list[str]] = []
        for c in state.task.validation_commands:
            commands.append(c.split() if isinstance(c, str) else list(c))
        return commands or [["python", "-m", "pytest", "-q"]]

    def _build_report(self, state: RunState, runtime: SkillRuntime) -> ExecutionReport:
        patch = self._sync_patch(state)
        tests = [TestResultSummary.model_validate(t) for t in state.test_results]
        total = sum(t.passed + t.failed + t.errors for t in tests)
        passed = sum(t.passed for t in tests)
        pass_rate = (passed / total) if total else None
        wall_ms = (time.perf_counter() - state.started_at) * 1000
        status = state.status
        if status == TaskStatus.RUNNING:
            status = TaskStatus.SUCCEEDED
        if state.error and status == TaskStatus.SUCCEEDED:
            status = TaskStatus.FAILED
        return ExecutionReport(
            run_id=state.run_id,
            task_id=state.task.task_id,
            architecture=state.task.architecture,
            status=status,
            problem_statement=state.problem_statement,
            plan_summary=state.plan_summary,
            patch=patch,
            test_results=tests,
            review=state.review,
            metrics=ExecutionMetrics(
                iterations=state.iterations,
                tool_calls=runtime.tool_call_count,
                invalid_tool_calls=runtime.invalid_tool_calls,
                tokens_in=runtime.tokens_in,
                tokens_out=runtime.tokens_out,
                estimated_cost_usd=runtime.estimated_cost,
                wall_clock_ms=wall_ms,
                test_pass_rate=pass_rate,
                regression_detected=state.failure_category == FailureCategory.TEST_FAILURE,
                failure_category=state.failure_category,
            ),
            steps=state.steps,
            selected_files=state.selected_files,
            error=state.error,
            finished_at=utcnow(),
        )

    def _sync_patch(self, state: RunState) -> PatchArtifact:
        diff = state.patch_diff
        files = list(state.files_changed)
        try:
            if not diff:
                diff = subprocess.check_output(
                    ["git", "diff"],
                    cwd=state.workspace_root,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    timeout=30,
                )
            if not files:
                status = subprocess.check_output(
                    ["git", "status", "--porcelain"],
                    cwd=state.workspace_root,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    timeout=30,
                )
                for line in status.splitlines():
                    if line.strip():
                        files.append(line[3:].strip() if len(line) > 3 else line.strip())
        except Exception:
            pass
        return PatchArtifact(
            diff=diff,
            files_changed=files,
            commit_message=state.commit_message,
        )
