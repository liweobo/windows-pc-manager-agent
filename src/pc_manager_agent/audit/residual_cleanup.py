"""Privacy-minimized audit events for controlled Stage 4D4 residual cleanup."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from pc_manager_agent.audit.models import AuditEvent
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.confirmation.residual_cleanup import ResidualCleanupConfirmation
from pc_manager_agent.domain.residual_cleanup import (
    PlannedResidualCleanupItem,
    ResidualCleanupAssessment,
    ResidualCleanupExecutionReport,
    ResidualCleanupItemResult,
    ResidualCleanupPlan,
    ResidualCleanupPreview,
)
from pc_manager_agent.recovery.models import TrashRecoveryRecord


class ResidualCleanupAuditLogger:
    """Record IDs, counts, digests, and recovery truth without file contents."""

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

    def assessed(self, assessment: ResidualCleanupAssessment) -> None:
        """Record a fresh R0 assessment that still grants no mutation authority."""
        classifications: dict[str, int] = {}
        ownership: dict[str, int] = {}
        protections: dict[str, int] = {}
        for item in assessment.items:
            classifications[item.classification.value] = (
                classifications.get(item.classification.value, 0) + 1
            )
            ownership[item.ownership_confidence.value] = (
                ownership.get(item.ownership_confidence.value, 0) + 1
            )
            protections[item.protection_level.value] = (
                protections.get(item.protection_level.value, 0) + 1
            )
        self._repository.record(
            AuditEvent(
                event_type="software.residuals.cleanup.assessed",
                parameters={
                    "request_id": str(assessment.request.request_id),
                    "source_report_id": str(assessment.request.source_report_id),
                    "selected_candidate_ids": [
                        str(value) for value in assessment.request.selected_residual_ids
                    ],
                    "assessment_id": str(assessment.assessment_id),
                    "assessment_digest": assessment.invariant_digest(),
                },
                risk_level=None,
                confirmation_required=False,
                result={
                    "selected_count": assessment.selected_count,
                    "eligible_count": assessment.eligible_count,
                    "blocked_count": assessment.blocked_count,
                    "manual_review_count": assessment.manual_review_count,
                    "classifications": classifications,
                    "ownership_confidence": ownership,
                    "protection_levels": protections,
                    "mutation_executed": False,
                    "file_contents_read": False,
                },
                rollback={"level": "NONE", "reason": "fresh assessment is read-only"},
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )

    def previewed(
        self,
        plan: ResidualCleanupPlan,
        preview: ResidualCleanupPreview,
    ) -> None:
        """Record the all-eligible R2 Preview before either approval."""
        self._repository.record(
            AuditEvent(
                event_type="software.residuals.cleanup.previewed",
                plan_id=str(plan.plan_id),
                plan_version=1,
                agent_decision="Fresh all-eligible Preview generated; no mutation executed",
                parameters={
                    "transaction_id": str(plan.transaction_id),
                    "source_report_id": str(plan.source_report_id),
                    "request_id": str(plan.request_id),
                    "assessment_id": str(plan.assessment_id),
                    "preview_id": str(preview.preview_id),
                    "plan_digest": plan.canonical_digest(),
                    "preview_digest": preview.canonical_digest(),
                    "item_set_digest": preview.item_set_digest,
                },
                risk_level=plan.risk_level,
                confirmation_required=True,
                result={
                    "eligible_item_count": plan.total_items,
                    "contained_object_count": plan.contained_object_count,
                    "total_bytes": plan.total_bytes,
                    "recovery_level": plan.rollback_level.value,
                    "mutation_executed": False,
                },
                rollback={"level": "MANUAL", "automatic_restore": False},
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )

    def confirmation_resolved(
        self,
        plan: ResidualCleanupPlan,
        confirmation: ResidualCleanupConfirmation,
    ) -> None:
        """Record either tier with all non-secret binding digests."""
        self._repository.record(
            AuditEvent(
                event_type=(
                    "software.residuals.cleanup."
                    f"{confirmation.tier.value.casefold()}_confirmation.resolved"
                ),
                plan_id=str(plan.plan_id),
                plan_version=1,
                parameters={
                    "transaction_id": str(confirmation.transaction_id),
                    "confirmation_id": str(confirmation.confirmation_id),
                    "parent_confirmation_id": (
                        str(confirmation.parent_confirmation_id)
                        if confirmation.parent_confirmation_id is not None
                        else None
                    ),
                    "preview_id": str(confirmation.preview_id),
                    "plan_digest": confirmation.plan_digest,
                    "preview_digest": confirmation.preview_digest,
                    "item_set_digest": confirmation.item_set_digest,
                    "identity_digest": confirmation.identity_digest,
                    "material_digest": confirmation.material_digest,
                    "classification_digest": confirmation.classification_digest,
                    "eligibility_digest": confirmation.eligibility_digest,
                    "recovery_capability_digest": (confirmation.recovery_capability_digest),
                },
                risk_level=confirmation.risk_level,
                confirmation_required=True,
                confirmation_result=confirmation.state.value,
                result={
                    "item_count": confirmation.item_count,
                    "contained_object_count": confirmation.contained_object_count,
                    "total_bytes": confirmation.total_bytes,
                },
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )

    def workflow_blocked(
        self,
        plan: ResidualCleanupPlan,
        *,
        phase: str,
        error_type: str,
    ) -> None:
        """Record a fail-closed revalidation boundary without local error text."""
        self._repository.record(
            AuditEvent(
                event_type="software.residuals.cleanup.blocked",
                plan_id=str(plan.plan_id),
                plan_version=1,
                parameters={
                    "transaction_id": str(plan.transaction_id),
                    "phase": phase,
                    "plan_digest": plan.canonical_digest(),
                },
                risk_level=plan.risk_level,
                confirmation_required=True,
                result={
                    "state": "BLOCKED",
                    "error_type": error_type,
                    "mutation_executed": False,
                },
                rollback={"level": "NONE", "mutation_executed": False},
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )

    def item_started(
        self,
        plan: ResidualCleanupPlan,
        item: PlannedResidualCleanupItem,
        recovery: TrashRecoveryRecord,
        runtime_confirmation_id: str,
    ) -> None:
        """Write the mandatory pre-dispatch event; failure prevents the Shell call."""
        self._repository.record(
            AuditEvent(
                event_type="software.residuals.cleanup.item.started",
                plan_id=str(plan.plan_id),
                plan_version=1,
                step_id=str(item.operation_id),
                tool_name=item.tool_name,
                parameters={
                    "transaction_id": str(plan.transaction_id),
                    "item_ref": str(item.item_ref),
                    "source_candidate_id": str(item.candidate.source_candidate_id),
                    "path_digest": _path_digest(item.candidate.path),
                    "classification": item.candidate.classification.value,
                    "runtime_confirmation_id": runtime_confirmation_id,
                },
                risk_level=plan.risk_level,
                confirmation_required=True,
                confirmation_result="CONSUMED",
                before_state={
                    "path_digest": _path_digest(recovery.before_state.path),
                    "kind": recovery.before_state.kind.value,
                    "volume_serial": recovery.before_state.volume_serial,
                    "file_id": recovery.before_state.file_id,
                    "size_bytes": recovery.before_state.size_bytes,
                    "created_ns": recovery.before_state.created_ns,
                    "modified_ns": recovery.before_state.modified_ns,
                    "attributes": recovery.before_state.attributes,
                },
                rollback={
                    "level": recovery.recovery_level.value,
                    "recovery_id": str(recovery.recovery_id),
                    "status": recovery.status.value,
                    "automatic_restore": False,
                },
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )

    def item_completed(
        self,
        plan: ResidualCleanupPlan,
        result: ResidualCleanupItemResult,
        recovery: TrashRecoveryRecord | None,
    ) -> None:
        """Record one verified, failed, changed, or skipped terminal item."""
        self._repository.record(
            AuditEvent(
                event_type=f"software.residuals.cleanup.item.{result.state.value.casefold()}",
                plan_id=str(plan.plan_id),
                plan_version=1,
                step_id=str(result.operation_id),
                tool_name="software.residuals.trash",
                parameters={
                    "transaction_id": str(plan.transaction_id),
                    "item_ref": str(result.item_ref),
                    "source_candidate_id": str(result.source_candidate_id),
                    "path_digest": _path_digest(result.path),
                },
                risk_level=plan.risk_level,
                confirmation_required=True,
                confirmation_result="CONSUMED",
                result=_item_result_payload(result),
                rollback=(
                    {
                        "level": recovery.recovery_level.value,
                        "recovery_id": str(recovery.recovery_id),
                        "status": recovery.status.value,
                        "automatic_restore": False,
                    }
                    if recovery is not None
                    else {"level": "NONE", "automatic_restore": False}
                ),
                verification={"status": result.verification_status.value},
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )

    def transaction_completed(self, report: ResidualCleanupExecutionReport) -> None:
        """Record aggregate terminal state without hiding partial or skipped work."""
        self._repository.record(
            AuditEvent(
                event_type="software.residuals.cleanup.transaction.completed",
                plan_id=str(report.plan_id),
                parameters={"transaction_id": str(report.transaction_id)},
                result={
                    "final_state": report.final_state.value,
                    "completed_count": report.completed_count,
                    "failed_count": report.failed_count,
                    "skipped_count": report.skipped_count,
                    "total_size_bytes": report.total_size_bytes,
                    "per_item_results": [_item_result_payload(item) for item in report.results],
                },
                rollback={"level": "MANUAL", "automatic_restore": False},
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )


def _path_digest(path: Path) -> str:
    """Hash a local path for audit without exposing it as report content."""
    normalized = os.path.normcase(os.path.abspath(os.fspath(path)))
    return hashlib.sha256(normalized.encode("utf-8", errors="surrogatepass")).hexdigest()


def _item_result_payload(result: ResidualCleanupItemResult) -> dict[str, object]:
    """Return an audit-safe result without storing source paths or Shell text."""
    recycle = result.recycle_result
    return {
        "item_ref": str(result.item_ref),
        "operation_id": str(result.operation_id),
        "sequence": result.sequence,
        "source_candidate_id": str(result.source_candidate_id),
        "path_digest": _path_digest(result.path),
        "state": result.state.value,
        "verification_status": result.verification_status.value,
        "recovery_id": str(result.recovery_id) if result.recovery_id is not None else None,
        "message": result.message,
        "recycle_bin": (
            {
                "hresult": recycle.hresult,
                "aborted": recycle.aborted,
                "recycled": recycle.recycled,
                "verification_status": recycle.verification_status.value,
                "has_recycle_item_identifier": recycle.recycle_item_identifier is not None,
            }
            if recycle is not None
            else None
        ),
    }
