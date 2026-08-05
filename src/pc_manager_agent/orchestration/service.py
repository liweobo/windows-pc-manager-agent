"""Plan-review-confirm-execute-verify-audit orchestration."""

from __future__ import annotations

import os
import time
from pathlib import Path
from uuid import UUID

from pc_manager_agent import __version__
from pc_manager_agent.audit.models import AuditEvent
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.confirmation.models import ConfirmationRequest
from pc_manager_agent.confirmation.state_machine import ConfirmationService
from pc_manager_agent.domain.plans import EstimatedImpact, PlanStep, TaskPlan, TaskScope
from pc_manager_agent.domain.reports import ScanReport
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.safety.path_policy import PathPolicy
from pc_manager_agent.safety.plan_reviewer import SafetyReview, SafetyReviewer
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.tools.registry import ToolRegistry


class OrchestrationError(RuntimeError):
    """Raised when an execution gate or postcondition fails."""


class ScanOrchestrator:
    """Coordinate the complete deterministic lifecycle for one approved root."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        reviewer: SafetyReviewer,
        path_policy: PathPolicy,
        confirmation: ConfirmationService,
        audit: AuditRepository,
        max_files: int,
        timeout_seconds: float,
    ) -> None:
        self._registry = registry
        self._reviewer = reviewer
        self._path_policy = path_policy
        self._confirmation = confirmation
        self._audit = audit
        self._max_files = max_files
        self._timeout_seconds = timeout_seconds
        self._git_commit = os.getenv("PC_MANAGER_GIT_COMMIT")

    def prepare_plan(self, root: Path) -> tuple[TaskPlan, SafetyReview]:
        """Build and independently review a metadata-only scan plan."""
        canonical_root = self._path_policy.validate_scan_root(root)
        exclusions = tuple(
            path for path in self._path_policy.forbidden_roots if self._within(path, canonical_root)
        )
        step = PlanStep(
            step_id="step-scan",
            tool_name="file.scan",
            description="只读取已授权目录中的文件元数据",
            arguments={
                "root": str(canonical_root),
                "excluded_paths": [str(path) for path in exclusions],
                "max_files": self._max_files,
                "timeout_seconds": self._timeout_seconds,
            },
            risk_level=RiskLevel.R0,
            requires_confirmation=False,
            rollback_level=RollbackLevel.NONE,
            preconditions=("目录存在", "目录在用户批准范围内", "审计数据库可用"),
            expected_postconditions=("未修改任何文件或目录",),
        )
        plan = TaskPlan(
            summary="对一个已授权目录执行只读元数据扫描",
            user_goal=f"只读扫描目录：{canonical_root}",
            assumptions=("访问时间仅作为元数据展示，不据此断言文件未使用",),
            scope=TaskScope(included_paths=(canonical_root,), excluded_paths=exclusions),
            steps=(step,),
            estimated_impact=EstimatedImpact(files_read=None, files_modified=0, files_deleted=0),
            requires_plan_confirmation=True,
            requires_runtime_confirmation=False,
        )
        review = self._reviewer.review(plan)
        self._audit.record(
            AuditEvent(
                event_type="plan.reviewed",
                original_request=plan.user_goal,
                plan=plan.model_dump(mode="json"),
                plan_id=str(plan.plan_id),
                plan_version=plan.plan_version,
                agent_decision="approved" if review.approved else "denied",
                risk_level=RiskLevel.R0,
                confirmation_required=True,
                verification=review.model_dump(mode="json"),
                app_version=__version__,
                git_commit=self._git_commit,
            )
        )
        return plan, review

    def request_plan_confirmation(self, plan: TaskPlan) -> ConfirmationRequest:
        """Issue a concrete plan confirmation after successful review."""
        review = self._reviewer.review(plan)
        if not review.approved:
            raise OrchestrationError("Safety review denied the plan")
        root = plan.scope.included_paths[0]
        return self._confirmation.request_plan(
            plan,
            f"只读扫描 {root}，最多 {self._max_files} 个文件；不会修改文件。",
        )

    def resolve_plan_confirmation(
        self,
        confirmation_id: UUID,
        approved: bool,
        plan: TaskPlan,
    ) -> ConfirmationRequest:
        """Record a plan decision and its audit event."""
        resolved = self._confirmation.resolve(confirmation_id, approved, plan)
        self._audit.record(
            AuditEvent(
                event_type="confirmation.resolved",
                plan_id=str(plan.plan_id),
                plan_version=plan.plan_version,
                risk_level=RiskLevel.R0,
                confirmation_required=True,
                confirmation_result=resolved.state.value,
                app_version=__version__,
                git_commit=self._git_commit,
            )
        )
        return resolved

    def execute(self, plan: TaskPlan, cancellation: CancellationToken) -> ScanReport:
        """Re-review, require exact confirmation, execute, verify, and audit."""
        review = self._reviewer.review(plan)
        if not review.approved:
            raise OrchestrationError("Safety review denied execution")
        self._confirmation.require_plan_approved(plan)
        step = plan.steps[0]
        started = time.monotonic()
        self._audit.record(
            AuditEvent(
                event_type="tool.started",
                plan_id=str(plan.plan_id),
                plan_version=plan.plan_version,
                step_id=step.step_id,
                tool_name=step.tool_name,
                parameters=step.arguments,
                risk_level=step.risk_level,
                confirmation_required=True,
                confirmation_result="APPROVED",
                app_version=__version__,
                git_commit=self._git_commit,
            )
        )
        try:
            result = self._registry.execute(step.tool_name, step.arguments, cancellation)
            if not isinstance(result, ScanReport):
                raise OrchestrationError("Scanner returned an unexpected report type")
            self._verify(plan, result)
        except Exception as exc:
            duration = max(0, round((time.monotonic() - started) * 1_000))
            self._audit.record(
                AuditEvent(
                    event_type="tool.failed",
                    plan_id=str(plan.plan_id),
                    plan_version=plan.plan_version,
                    step_id=step.step_id,
                    tool_name=step.tool_name,
                    parameters=step.arguments,
                    risk_level=step.risk_level,
                    confirmation_required=True,
                    confirmation_result="APPROVED",
                    error={"type": type(exc).__name__, "message": str(exc)},
                    app_version=__version__,
                    git_commit=self._git_commit,
                    duration_ms=duration,
                )
            )
            raise
        duration = max(0, round((time.monotonic() - started) * 1_000))
        self._audit.record(
            AuditEvent(
                event_type="tool.completed",
                plan_id=str(plan.plan_id),
                plan_version=plan.plan_version,
                step_id=step.step_id,
                tool_name=step.tool_name,
                parameters=step.arguments,
                risk_level=step.risk_level,
                confirmation_required=True,
                confirmation_result="APPROVED",
                result={"summary": result.summary.model_dump(mode="json")},
                verification={"passed": True, "root": str(result.root)},
                app_version=__version__,
                git_commit=self._git_commit,
                duration_ms=duration,
            )
        )
        return result

    @staticmethod
    def _verify(plan: TaskPlan, report: ScanReport) -> None:
        expected_root = plan.scope.included_paths[0]
        if report.root != expected_root:
            raise OrchestrationError("Scanner report root does not match the confirmed plan")
        if report.summary.files_seen != len(report.files):
            raise OrchestrationError("Scanner report count verification failed")

    @staticmethod
    def _within(path: Path, root: Path) -> bool:
        candidate = os.path.normcase(os.path.abspath(path))
        boundary = os.path.normcase(os.path.abspath(root))
        try:
            return os.path.commonpath((candidate, boundary)) == boundary
        except ValueError:
            return False
