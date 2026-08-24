"""Privacy-minimized audit events for Stage 4D3 report-only analysis."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from pc_manager_agent.audit.models import AuditEvent
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.confirmation.models import ConfirmationRequest
from pc_manager_agent.domain.plans import TaskPlan
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.software_residuals import ResidualReport, UninstallContext
from pc_manager_agent.reporting.residual_exporter import ResidualExportResult


class SoftwareResidualAuditLogger:
    """Record counts and digests without file paths or document contents."""

    def __init__(
        self,
        repository: AuditRepository,
        *,
        app_version: str,
        git_commit: str | None,
    ) -> None:
        self._repository = repository
        self._app_version = app_version
        self._git_commit = git_commit

    def plan_reviewed(
        self,
        plan: TaskPlan,
        context: UninstallContext,
        approved: bool,
        issues: tuple[str, ...],
    ) -> None:
        """Record the R0 scope review using only path and issue counts."""
        self._repository.record(
            AuditEvent(
                event_type="software.residuals.plan.reviewed",
                original_request=plan.user_goal,
                plan={
                    "plan_id": str(plan.plan_id),
                    "plan_digest": plan.canonical_digest(),
                    "tool_names": [step.tool_name for step in plan.steps],
                    "included_path_count": len(plan.scope.included_paths),
                },
                plan_id=str(plan.plan_id),
                plan_version=plan.plan_version,
                parameters={
                    "context_id": str(context.context_id),
                    "transaction_id": str(context.transaction_id),
                    "context_digest": context.canonical_digest(),
                    "mechanism": context.mechanism.value,
                },
                agent_decision="approved" if approved else "rejected",
                risk_level=RiskLevel.R0,
                confirmation_required=True,
                result={
                    "issue_count": len(issues),
                    "deletion_performed": False,
                    "file_contents_read": False,
                },
                rollback={"level": "NONE", "reason": "read-only analysis"},
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )

    def confirmation_resolved(
        self,
        plan: TaskPlan,
        context: UninstallContext,
        confirmation: ConfirmationRequest,
    ) -> None:
        """Record an exact digest-bound R0 plan decision."""
        self._repository.record(
            AuditEvent(
                event_type="software.residuals.plan_confirmation.resolved",
                plan_id=str(plan.plan_id),
                plan_version=plan.plan_version,
                parameters={
                    "confirmation_id": str(confirmation.confirmation_id),
                    "plan_digest": confirmation.plan_digest,
                    "context_id": str(context.context_id),
                    "context_digest": context.canonical_digest(),
                },
                risk_level=RiskLevel.R0,
                confirmation_required=True,
                confirmation_result=confirmation.state.value,
                result={"deletion_performed": False},
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )

    def analysis_completed(
        self,
        plan: TaskPlan,
        context: UninstallContext,
        report: ResidualReport,
    ) -> None:
        """Record aggregate classifications and protection counts, never paths."""
        classifications: dict[str, int] = {}
        protections: dict[str, int] = {}
        ownership: dict[str, int] = {}
        for candidate in report.candidates:
            classifications[candidate.classification.value] = (
                classifications.get(candidate.classification.value, 0) + 1
            )
            protections[candidate.protection_level.value] = (
                protections.get(candidate.protection_level.value, 0) + 1
            )
            ownership[candidate.ownership_confidence.value] = (
                ownership.get(candidate.ownership_confidence.value, 0) + 1
            )
        self._repository.record(
            AuditEvent(
                event_type="software.residuals.analysis.completed",
                plan_id=str(plan.plan_id),
                plan_version=plan.plan_version,
                tool_name="software.residuals.analyze",
                parameters={
                    "report_id": str(report.report_id),
                    "context_id": str(context.context_id),
                    "transaction_id": str(context.transaction_id),
                    "identity_digest": context.software_identity_digest,
                    "context_digest": report.context_digest,
                    "root_count": report.summary.roots_requested,
                    "scan_scope_policy_version": report.scope_policy_version,
                },
                risk_level=RiskLevel.R0,
                confirmation_required=True,
                confirmation_result="APPROVED",
                result={
                    "status": report.status.value,
                    "candidate_count": report.summary.candidates,
                    "file_count": report.summary.files,
                    "directory_count": report.summary.directories,
                    "total_size_bytes": report.summary.total_size_bytes,
                    "issue_count": report.summary.issues,
                    "skipped_path_count": report.summary.skipped_paths,
                    "reparse_points_skipped": report.summary.reparse_points_skipped,
                    "classifications": classifications,
                    "protection_levels": protections,
                    "ownership_confidence": ownership,
                    "deletion_performed": False,
                    "file_contents_read": False,
                    "registry_read": False,
                    "started_at": report.started_at.isoformat(),
                    "completed_at": report.completed_at.isoformat(),
                },
                verification={
                    "report_only": True,
                    "reparse_targets_followed": False,
                    "roots_scanned": report.summary.roots_scanned,
                },
                rollback={"level": "NONE", "reason": "no scanned data was modified"},
                duration_ms=report.summary.duration_ms,
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )

    def report_exported(
        self,
        plan: TaskPlan,
        context: UninstallContext,
        report: ResidualReport,
        result: ResidualExportResult,
    ) -> None:
        """Audit a user-requested local export without retaining its path."""
        self._repository.record(
            AuditEvent(
                event_type="software.residuals.report.exported",
                plan_id=str(plan.plan_id),
                plan_version=plan.plan_version,
                parameters={
                    "report_id": str(report.report_id),
                    "context_id": str(context.context_id),
                    "context_digest": context.canonical_digest(),
                    "target_digest": _path_digest(result.target),
                    "format": result.format.value,
                },
                risk_level=RiskLevel.R1,
                confirmation_required=False,
                confirmation_result="USER_SELECTED_DESTINATION",
                result={
                    "candidate_count": result.candidate_count,
                    "deletion_performed": False,
                    "overwrite_performed": False,
                },
                rollback={
                    "level": "MANUAL",
                    "reason": "The Agent never automatically removes exported reports",
                },
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )


def _path_digest(path: Path) -> str:
    normalized = os.path.normcase(os.path.abspath(os.fspath(path)))
    return hashlib.sha256(normalized.encode("utf-8", errors="surrogatepass")).hexdigest()
