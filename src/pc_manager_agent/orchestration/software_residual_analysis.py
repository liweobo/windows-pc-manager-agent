"""Confirmed, report-only Stage 4D3 residual analysis orchestration."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from pc_manager_agent.audit.software_residuals import SoftwareResidualAuditLogger
from pc_manager_agent.confirmation.models import ConfirmationRequest
from pc_manager_agent.confirmation.state_machine import ConfirmationService
from pc_manager_agent.domain.plans import EstimatedImpact, PlanStep, TaskPlan, TaskScope
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.software_residuals import (
    ResidualAnalysisStatus,
    ResidualAnalyzeRequest,
    ResidualAnalyzeResult,
    ResidualCandidate,
    ResidualClassification,
    ResidualInspectResult,
    ResidualIssue,
    ResidualObjectType,
    ResidualReport,
    ResidualReportResult,
    ResidualReportSummary,
    UninstallContext,
    UserDataProtectionLevel,
)
from pc_manager_agent.orchestration.residual_collectors import (
    ResidualCollectionBudget,
    ResidualCollector,
)
from pc_manager_agent.persistence.software_residuals import SoftwareResidualRepository
from pc_manager_agent.reporting.residual_exporter import (
    ResidualExportResult,
    ResidualReportExporter,
    ResidualReportFormat,
)
from pc_manager_agent.safety.residual_scope_policy import (
    ResidualScanScopePolicy,
    ResidualScopeError,
)
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.tools.registry import ToolRegistry, UnknownToolError

RESIDUAL_TOOL_NAMES = (
    "software.residuals.analyze",
    "software.residuals.report",
    "software.residuals.inspect",
)
_FORBIDDEN_TOOL_MARKERS = ("delete", "cleanup", "trash", "move", "rename", "registry")


class ResidualAnalysisError(RuntimeError):
    """Raised when Stage 4D3 cannot preserve its exact read-only boundary."""


@dataclass(frozen=True, slots=True)
class ResidualSafetyReview:
    """Independent deterministic review of one context-bound R0 plan."""

    approved: bool
    issues: tuple[str, ...] = ()


class ResidualAnalysisPlanCompiler:
    """Compile one eligible transaction into the fixed three-tool R0 plan."""

    def __init__(
        self, scope: ResidualScanScopePolicy, max_objects: int, timeout_seconds: float
    ) -> None:
        self._scope = scope
        self._max_objects = max_objects
        self._timeout_seconds = timeout_seconds

    def compile(self, user_goal: str, context: UninstallContext) -> TaskPlan:
        """Build an immutable plan whose scope equals durable exact path evidence."""
        roots = self._scope.validated_roots(context)
        plan_id = uuid4()
        common_arguments = {
            "context_id": str(context.context_id),
            "context_digest": context.canonical_digest(),
        }
        return TaskPlan(
            plan_id=plan_id,
            summary="Analyze possible post-uninstall residual metadata without deleting anything",
            user_goal=user_goal,
            assumptions=(
                "Only exact paths captured by an Agent-controlled uninstall are scanned.",
                "Ownership confidence never implies deletion safety.",
                "File contents, registry values, links, and reparse targets are not read.",
            ),
            scope=TaskScope(included_paths=tuple(item.path for item in roots)),
            steps=(
                PlanStep(
                    step_id="step-residual-analyze",
                    tool_name=RESIDUAL_TOOL_NAMES[0],
                    description="Read bounded metadata from exact uninstall-context paths",
                    arguments={
                        **common_arguments,
                        "plan_id": str(plan_id),
                        "max_objects": self._max_objects,
                        "timeout_seconds": self._timeout_seconds,
                    },
                    risk_level=RiskLevel.R0,
                    requires_confirmation=False,
                    rollback_level=RollbackLevel.NONE,
                    preconditions=("Exact R0 plan is confirmed", "Uninstall context is eligible"),
                    expected_postconditions=("deletion_performed remains false",),
                ),
                PlanStep(
                    step_id="step-residual-report",
                    tool_name=RESIDUAL_TOOL_NAMES[1],
                    description="Read the latest local residual report",
                    arguments={"context_id": str(context.context_id)},
                    risk_level=RiskLevel.R0,
                    requires_confirmation=False,
                    rollback_level=RollbackLevel.NONE,
                ),
                PlanStep(
                    step_id="step-residual-inspect",
                    tool_name=RESIDUAL_TOOL_NAMES[2],
                    description="Inspect one candidate already inside this report scope",
                    arguments={"context_id": str(context.context_id)},
                    risk_level=RiskLevel.R0,
                    requires_confirmation=False,
                    rollback_level=RollbackLevel.NONE,
                ),
            ),
            estimated_impact=EstimatedImpact(
                files_read=None,
                files_modified=0,
                files_deleted=0,
            ),
            requires_plan_confirmation=True,
            requires_runtime_confirmation=False,
        )


class ResidualSafetyReviewer:
    """Reject scope expansion, stale context, write tools, and hallucinated cleanup."""

    def __init__(self, registry: ToolRegistry, scope: ResidualScanScopePolicy) -> None:
        self._registry = registry
        self._scope = scope

    def review(self, plan: TaskPlan, context: UninstallContext) -> ResidualSafetyReview:
        """Validate every registered tool, argument, risk, and exact root."""
        issues: list[str] = []
        if tuple(step.tool_name for step in plan.steps) != RESIDUAL_TOOL_NAMES:
            issues.append("Stage 4D3 plan must contain exactly the fixed three R0 tools")
        if plan.requires_runtime_confirmation:
            issues.append("Stage 4D3 must not request an R2 runtime confirmation")
        if plan.estimated_impact.files_modified or plan.estimated_impact.files_deleted:
            issues.append("Stage 4D3 plan claims a filesystem mutation")
        try:
            expected_roots = self._scope.validated_roots(context)
        except ResidualScopeError as exc:
            issues.append(str(exc))
            expected_roots = ()
        expected = {_normalized(item.path) for item in expected_roots}
        actual = {_normalized(path) for path in plan.scope.included_paths}
        if actual != expected:
            issues.append("Plan scope differs from exact uninstall-context paths")
        for step in plan.steps:
            if any(marker in step.tool_name for marker in _FORBIDDEN_TOOL_MARKERS):
                issues.append(f"Destructive or unrelated tool name is prohibited: {step.tool_name}")
                continue
            try:
                manifest = self._registry.manifest(step.tool_name)
            except UnknownToolError:
                issues.append(f"Unregistered Stage 4D3 tool: {step.tool_name}")
                continue
            if manifest.risk_level is not RiskLevel.R0 or not manifest.read_only:
                issues.append(f"Tool is not a read-only R0 tool: {step.tool_name}")
            if manifest.rollback_level is not RollbackLevel.NONE:
                issues.append(f"R0 residual tool must declare rollback NONE: {step.tool_name}")
            if manifest.requires_runtime_confirmation:
                issues.append(
                    f"Residual tool unexpectedly requests runtime approval: {step.tool_name}"
                )
            if step.risk_level is not RiskLevel.R0 or step.rollback_level is not RollbackLevel.NONE:
                issues.append(f"Plan step risk contract is invalid: {step.step_id}")
        analyze = plan.steps[0] if plan.steps else None
        if analyze is not None:
            if analyze.arguments.get("context_id") != str(context.context_id):
                issues.append("Plan references another uninstall context")
            if analyze.arguments.get("context_digest") != context.canonical_digest():
                issues.append("Plan context digest is stale")
            if analyze.arguments.get("plan_id") != str(plan.plan_id):
                issues.append("Analyze request is bound to another plan")
        return ResidualSafetyReview(not issues, tuple(issues))


class ResidualAnalyzer:
    """Run finite collectors, persist metadata, and hard-code deletion=false."""

    def __init__(
        self,
        repository: SoftwareResidualRepository,
        scope: ResidualScanScopePolicy,
        collectors: tuple[ResidualCollector, ...],
    ) -> None:
        self._repository = repository
        self._scope = scope
        self._collectors = collectors

    def analyze(
        self,
        request: ResidualAnalyzeRequest,
        cancellation: CancellationToken,
    ) -> ResidualReport:
        """Analyze exact roots under one shared object/time budget."""
        context = self._repository.get_context(request.context_id)
        if context.canonical_digest() != request.context_digest:
            raise ResidualAnalysisError("Uninstall context changed after plan confirmation")
        roots = self._scope.validated_roots(context)
        started_wall = time.time()
        started_monotonic = time.monotonic()
        report_id = uuid4()
        budget = ResidualCollectionBudget(
            max_objects=request.max_objects,
            timeout_seconds=request.timeout_seconds,
            cancellation=cancellation,
        )
        candidates: list[ResidualCandidate] = []
        issues: list[ResidualIssue] = []
        roots_scanned = 0
        for evidence in roots:
            if not budget.can_continue():
                break
            matches = [
                collector for collector in self._collectors if collector.supports(evidence.source)
            ]
            if len(matches) != 1:
                _append_issue(
                    issues,
                    ResidualIssue(
                        code="collector-boundary-error",
                        message="Exact residual source has no single deterministic collector.",
                        path=evidence.path,
                    ),
                )
                continue
            result = matches[0].collect(
                evidence,
                report_id,
                budget,
                uninstall_verified=context.verified_removed,
            )
            candidates.extend(result.candidates)
            for issue in result.issues:
                _append_issue(issues, issue)
            roots_scanned += int(result.root_scanned)
        status = budget.stop_status
        if status is None:
            status = ResidualAnalysisStatus.PARTIAL if issues else ResidualAnalysisStatus.COMPLETED
        files = sum(
            candidate.object_type in {ResidualObjectType.FILE, ResidualObjectType.SHORTCUT}
            for candidate in candidates
        )
        directories = sum(
            candidate.object_type is ResidualObjectType.DIRECTORY for candidate in candidates
        )
        protected_levels = {
            UserDataProtectionLevel.PROTECTED,
            UserDataProtectionLevel.STRONGLY_PROTECTED,
            UserDataProtectionLevel.UNKNOWN,
        }
        completed_at = _utc_from_timestamp(time.time())
        duration_ms = max(0, round((time.monotonic() - started_monotonic) * 1_000))
        warnings = list(context.warnings)
        if not context.verified_removed:
            warnings.append(
                "Uninstall completion was not fully verified; ownership confidence is "
                "reduced by a risk flag."
            )
        if len(issues) >= 5_000:
            warnings.append("Issue details were capped at 5,000 records.")
        report = ResidualReport(
            report_id=report_id,
            context_id=context.context_id,
            uninstall_transaction_id=context.transaction_id,
            plan_id=request.plan_id,
            software_identity_digest=context.software_identity_digest,
            context_digest=context.canonical_digest(),
            started_at=_utc_from_timestamp(started_wall),
            completed_at=completed_at,
            status=status,
            candidates=tuple(candidates),
            issues=tuple(issues),
            warnings=tuple(warnings),
            summary=ResidualReportSummary(
                candidates=len(candidates),
                files=files,
                directories=directories,
                total_size_bytes=sum(candidate.size_bytes for candidate in candidates),
                protected_size_bytes=sum(
                    candidate.size_bytes
                    for candidate in candidates
                    if candidate.protection_level in protected_levels
                ),
                unknown_size_bytes=sum(
                    candidate.size_bytes
                    for candidate in candidates
                    if candidate.classification is ResidualClassification.UNKNOWN
                ),
                issues=len(issues),
                skipped_paths=len(issues),
                reparse_points_skipped=sum(
                    issue.code == "reparse-point-skipped" for issue in issues
                ),
                roots_requested=len(roots),
                roots_scanned=roots_scanned,
                duration_ms=duration_ms,
            ),
            deletion_performed=False,
        )
        self._repository.save_report(report)
        return report


class ResidualAnalysisService:
    """Coordinate R0 plan, review, confirmation, tools, audit, and STOP."""

    def __init__(
        self,
        repository: SoftwareResidualRepository,
        compiler: ResidualAnalysisPlanCompiler,
        reviewer: ResidualSafetyReviewer,
        confirmation: ConfirmationService,
        registry: ToolRegistry,
        audit: SoftwareResidualAuditLogger,
    ) -> None:
        self._repository = repository
        self._compiler = compiler
        self._reviewer = reviewer
        self._confirmation = confirmation
        self._registry = registry
        self._audit = audit

    def prepare(
        self, user_goal: str, transaction_id: UUID
    ) -> tuple[TaskPlan, UninstallContext, ResidualSafetyReview]:
        """Load one eligible Agent transaction and independently review its plan."""
        context = self._repository.get_context_for_transaction(transaction_id)
        plan = self._compiler.compile(user_goal, context)
        review = self._reviewer.review(plan, context)
        self._audit.plan_reviewed(plan, context, review.approved, review.issues)
        return plan, context, review

    def request_plan_confirmation(
        self, plan: TaskPlan, context: UninstallContext
    ) -> ConfirmationRequest:
        """Issue an expiring R0 confirmation after a fresh deterministic review."""
        review = self._reviewer.review(plan, context)
        if not review.approved:
            raise ResidualAnalysisError("Residual analysis plan failed safety review")
        return self._confirmation.request_plan(
            plan,
            (
                f"Read metadata under {len(context.known_paths)} exact path(s) for "
                f"{context.display_name}. Files modified: 0; files deleted: 0."
            ),
        )

    def resolve_plan_confirmation(
        self,
        confirmation_id: UUID,
        approved: bool,
        plan: TaskPlan,
        context: UninstallContext,
    ) -> ConfirmationRequest:
        """Resolve the exact plan confirmation and audit the decision."""
        resolved = self._confirmation.resolve(confirmation_id, approved, plan)
        self._audit.confirmation_resolved(plan, context, resolved)
        return resolved

    def analyze(
        self,
        plan: TaskPlan,
        context: UninstallContext,
        cancellation: CancellationToken | None = None,
    ) -> ResidualReport:
        """Re-review and execute the single context-bound analyze step."""
        review = self._reviewer.review(plan, context)
        if not review.approved:
            raise ResidualAnalysisError("Residual analysis plan changed or is unsafe")
        self._confirmation.require_plan_approved(plan)
        result = self._registry.execute(
            RESIDUAL_TOOL_NAMES[0],
            plan.steps[0].arguments,
            cancellation or CancellationToken(),
        )
        if not isinstance(result, ResidualAnalyzeResult):
            raise ResidualAnalysisError("Residual analyze tool returned an unexpected result")
        self._audit.analysis_completed(plan, context, result.report)
        return result.report

    def latest_report(self, plan: TaskPlan, context: UninstallContext) -> ResidualReport | None:
        """Read the latest report only inside the still-confirmed context scope."""
        self._require_current_plan(plan, context)
        result = self._registry.execute(
            RESIDUAL_TOOL_NAMES[1], {"context_id": str(context.context_id)}
        )
        if not isinstance(result, ResidualReportResult):
            raise ResidualAnalysisError("Residual report tool returned an unexpected result")
        return result.report

    def inspect_candidate(
        self, plan: TaskPlan, context: UninstallContext, candidate_id: UUID
    ) -> ResidualCandidate | None:
        """Read one opaque candidate that belongs to the confirmed context."""
        self._require_current_plan(plan, context)
        result = self._registry.execute(
            RESIDUAL_TOOL_NAMES[2],
            {"context_id": str(context.context_id), "candidate_id": str(candidate_id)},
        )
        if not isinstance(result, ResidualInspectResult):
            raise ResidualAnalysisError("Residual inspect tool returned an unexpected result")
        return result.candidate

    def export_report(
        self,
        plan: TaskPlan,
        context: UninstallContext,
        report: ResidualReport,
        target: Path,
        format: ResidualReportFormat,
        exporter: ResidualReportExporter,
    ) -> ResidualExportResult:
        """Create and audit one user-selected report without granting cleanup authority."""
        self._require_current_plan(plan, context)
        if (
            report.plan_id != plan.plan_id
            or report.context_id != context.context_id
            or report.context_digest != context.canonical_digest()
        ):
            raise ResidualAnalysisError("Residual report is stale or belongs to another plan")
        result = exporter.export(report, target, format)
        self._audit.report_exported(plan, context, report, result)
        return result

    def _require_current_plan(self, plan: TaskPlan, context: UninstallContext) -> None:
        review = self._reviewer.review(plan, context)
        if not review.approved:
            raise ResidualAnalysisError("Residual report scope is stale or unsafe")
        self._confirmation.require_plan_approved(plan)


def _normalized(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _append_issue(issues: list[ResidualIssue], issue: ResidualIssue) -> None:
    if len(issues) < 5_000:
        issues.append(issue)


def _utc_from_timestamp(value: float) -> datetime:
    return datetime.fromtimestamp(value, tz=UTC)
