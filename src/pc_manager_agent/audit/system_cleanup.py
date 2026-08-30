"""Privacy-minimized audit events for controlled Stage 4E2 cleanup."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from pc_manager_agent.audit.models import AuditEvent
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.confirmation.system_cleanup import SystemCleanupConfirmation
from pc_manager_agent.domain.system_cleanup_execution import (
    CleanupExecutionPlan,
    CleanupExecutionPreview,
    CleanupItemResult,
    CleanupResult,
    PlannedCleanupItem,
    RecycleBinEmptyPlan,
    RecycleBinEmptyPreview,
    RecycleBinEmptyResult,
    SystemCleanupAssessment,
)
from pc_manager_agent.recovery.models import TrashRecoveryRecord


class SystemCleanupAuditLogger:
    """Record IDs, aggregates, digests and recovery truth without file contents."""

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

    def assessed(self, assessment: SystemCleanupAssessment) -> None:
        """Record Fresh R0 discovery that still grants no mutation authority."""
        categories: dict[str, int] = {}
        protections: dict[str, int] = {}
        decisions: dict[str, int] = {}
        for item in assessment.items:
            categories[item.category.value] = categories.get(item.category.value, 0) + 1
            protections[item.protection_level.value] = (
                protections.get(item.protection_level.value, 0) + 1
            )
            decisions[item.eligibility.value] = decisions.get(item.eligibility.value, 0) + 1
        self._record(
            AuditEvent(
                event_type="optimization.cleanup.assessed",
                app_version=self._app_version,
                git_commit=self._git_commit,
                parameters={
                    "request_id": str(assessment.request.request_id),
                    "source_report_id": str(assessment.request.source_report_id),
                    "selected_candidate_ids": [
                        str(value) for value in assessment.request.selected_candidate_ids
                    ],
                    "assessment_id": str(assessment.assessment_id),
                    "assessment_digest": assessment.invariant_digest(),
                },
                confirmation_required=False,
                result={
                    "discovered_count": len(assessment.items),
                    "eligible_count": assessment.eligible_count,
                    "blocked_count": assessment.blocked_count,
                    "manual_review_count": assessment.manual_review_count,
                    "deferred_count": assessment.deferred_count,
                    "categories": categories,
                    "protection_levels": protections,
                    "eligibility_decisions": decisions,
                    "mutation_executed": False,
                    "file_contents_read": False,
                    "permanent_delete_calls": 0,
                },
                rollback={"level": "NONE", "reason": "Fresh discovery is read-only"},
            )
        )

    def previewed(
        self,
        plan: CleanupExecutionPlan,
        preview: CleanupExecutionPreview,
    ) -> None:
        """Record an exact all-eligible batch before either approval."""
        self._record(
            AuditEvent(
                event_type="optimization.cleanup.previewed",
                app_version=self._app_version,
                git_commit=self._git_commit,
                plan_id=str(plan.plan_id),
                plan_version=1,
                agent_decision="Fresh all-eligible Preview generated; no mutation executed",
                parameters={
                    "transaction_id": str(plan.transaction_id),
                    "source_report_id": str(plan.source_report_id),
                    "assessment_id": str(plan.assessment_id),
                    "preview_id": str(preview.preview_id),
                    "plan_digest": plan.canonical_digest(),
                    "preview_digest": preview.canonical_digest(),
                    "item_set_digest": preview.item_set_digest,
                },
                risk_level=plan.risk_level,
                confirmation_required=True,
                result={
                    "item_count": plan.total_items,
                    "contained_object_count": plan.contained_object_count,
                    "total_observed_bytes": plan.total_observed_bytes,
                    "recovery_level": plan.recovery_summary.level.value,
                    "permanent_delete_available": False,
                    "mutation_executed": False,
                },
                rollback={"level": "MANUAL", "automatic_restore": False},
            )
        )

    def empty_previewed(
        self,
        plan: RecycleBinEmptyPlan,
        preview: RecycleBinEmptyPreview,
    ) -> None:
        """Record separate exact-volume irreversibility before approval."""
        self._record(
            AuditEvent(
                event_type="optimization.recycle_bin.empty.previewed",
                app_version=self._app_version,
                git_commit=self._git_commit,
                plan_id=str(plan.plan_id),
                plan_version=1,
                parameters={
                    "transaction_id": str(plan.transaction_id),
                    "preview_id": str(preview.preview_id),
                    "plan_digest": plan.canonical_digest(),
                    "preview_digest": preview.canonical_digest(),
                    "snapshot_digest": plan.snapshot_digest,
                    "volume_digest": _path_digest(plan.snapshot.volume_root),
                },
                risk_level=plan.risk_level,
                confirmation_required=True,
                result={
                    "item_count": plan.snapshot.item_count,
                    "observed_size_bytes": plan.snapshot.observed_size_bytes,
                    "enumeration_complete": plan.snapshot.enumeration_complete,
                    "recovery_level": "NONE",
                    "mutation_executed": False,
                },
                rollback={"level": "NONE", "automatic_restore": False},
            )
        )

    def confirmation_resolved(self, confirmation: SystemCleanupConfirmation) -> None:
        """Record either tier with all non-secret evidence digests."""
        self._record(
            AuditEvent(
                event_type=(
                    "optimization.cleanup."
                    f"{confirmation.scope.value.casefold()}."
                    f"{confirmation.tier.value.casefold()}_confirmation.resolved"
                ),
                app_version=self._app_version,
                git_commit=self._git_commit,
                plan_id=str(confirmation.plan_id),
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
                    "protection_digest": confirmation.protection_digest,
                    "eligibility_digest": confirmation.eligibility_digest,
                    "adapter_digest": confirmation.adapter_digest,
                    "recovery_digest": confirmation.recovery_digest,
                },
                risk_level=confirmation.risk_level,
                confirmation_required=True,
                confirmation_result=confirmation.state.value,
                result={
                    "item_count": confirmation.item_count,
                    "contained_object_count": confirmation.contained_object_count,
                    "total_bytes": confirmation.total_bytes,
                },
            )
        )

    def item_started(
        self,
        plan: CleanupExecutionPlan,
        item: PlannedCleanupItem,
        recovery: TrashRecoveryRecord,
        runtime_confirmation_id: str,
    ) -> None:
        """Write mandatory pre-dispatch evidence; audit failure blocks the Shell call."""
        candidate = item.candidate
        if candidate.path is None:
            raise ValueError("Cleanup audit cannot start without an exact path")
        self._record(
            AuditEvent(
                event_type="optimization.cleanup.item.started",
                app_version=self._app_version,
                git_commit=self._git_commit,
                plan_id=str(plan.plan_id),
                plan_version=1,
                step_id=str(item.operation_id),
                tool_name=item.tool_name,
                parameters={
                    "transaction_id": str(plan.transaction_id),
                    "item_ref": str(candidate.item_ref),
                    "source_candidate_id": str(candidate.source_candidate_id),
                    "path_digest": _path_digest(candidate.path),
                    "category": candidate.category.value,
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
                    "level": "MANUAL",
                    "recovery_id": str(recovery.recovery_id),
                    "automatic_restore": False,
                },
            )
        )

    def item_completed(
        self,
        plan: CleanupExecutionPlan,
        result: CleanupItemResult,
        recovery: TrashRecoveryRecord | None,
    ) -> None:
        """Record one verified, failed, changed, or skipped terminal item."""
        payload = _item_result_payload(result)
        self._record(
            AuditEvent(
                event_type=f"optimization.cleanup.item.{result.state.value.casefold()}",
                app_version=self._app_version,
                git_commit=self._git_commit,
                plan_id=str(plan.plan_id),
                plan_version=1,
                step_id=str(result.operation_id),
                tool_name="optimization.cleanup.trash",
                parameters={
                    "transaction_id": str(plan.transaction_id),
                    "item_ref": str(result.item_ref),
                    "source_candidate_id": str(result.source_candidate_id),
                    "path_digest": _path_digest(result.path),
                },
                risk_level=plan.risk_level,
                confirmation_required=True,
                confirmation_result="CONSUMED",
                result=payload,
                rollback=(
                    {
                        "level": "MANUAL",
                        "recovery_id": str(recovery.recovery_id),
                        "status": recovery.status.value,
                        "automatic_restore": False,
                    }
                    if recovery is not None
                    else {"level": "NONE", "automatic_restore": False}
                ),
                verification={"status": result.verification_status.value},
            )
        )

    def transaction_completed(self, result: CleanupResult) -> None:
        """Record aggregate terminal state and distinct space semantics."""
        self._record(
            AuditEvent(
                event_type="optimization.cleanup.transaction.completed",
                app_version=self._app_version,
                git_commit=self._git_commit,
                plan_id=str(result.plan_id),
                parameters={"transaction_id": str(result.transaction_id)},
                result={
                    "final_state": result.final_state.value,
                    "verified_items": result.verified_items,
                    "failed_items": result.failed_items,
                    "blocked_items": result.blocked_items,
                    "skipped_items": result.skipped_items,
                    "bytes_removed_from_original_locations": (
                        result.bytes_removed_from_original_locations
                    ),
                    "verified_disk_space_reclaimed_bytes": (
                        result.verified_disk_space_reclaimed_bytes
                    ),
                    "observed_free_space_change_bytes": (result.observed_free_space_change_bytes),
                    "permanent_delete_calls": 0,
                    "per_item_results": [_item_result_payload(item) for item in result.results],
                },
                rollback={"level": "MANUAL", "automatic_restore": False},
            )
        )

    def empty_completed(self, result: RecycleBinEmptyResult) -> None:
        """Record irreversible exact-volume outcome without claiming recovery."""
        self._record(
            AuditEvent(
                event_type="optimization.recycle_bin.empty.completed",
                app_version=self._app_version,
                git_commit=self._git_commit,
                parameters={
                    "transaction_id": str(result.transaction_id),
                    "volume_digest": _path_digest(result.volume_root),
                },
                risk_level=None,
                confirmation_required=True,
                confirmation_result="CONSUMED",
                result={
                    "hresult": result.hresult,
                    "verification_status": result.verification_status.value,
                    "before_item_count": result.before.item_count,
                    "before_size_bytes": result.before.observed_size_bytes,
                    "after_item_count": result.after.item_count if result.after else None,
                    "after_size_bytes": (
                        result.after.observed_size_bytes if result.after else None
                    ),
                    "recovery_level": "NONE",
                    "permanent_delete_calls": 0,
                },
                rollback={"level": "NONE", "automatic_restore": False},
                verification={"status": result.verification_status.value},
            )
        )

    def empty_started(
        self,
        plan: RecycleBinEmptyPlan,
        runtime_confirmation_id: str,
    ) -> None:
        """Write mandatory pre-dispatch evidence for the irreversible Shell call."""
        self._record(
            AuditEvent(
                event_type="optimization.recycle_bin.empty.started",
                app_version=self._app_version,
                git_commit=self._git_commit,
                plan_id=str(plan.plan_id),
                plan_version=1,
                step_id=str(plan.operation_id),
                tool_name="optimization.recycle_bin.empty",
                parameters={
                    "transaction_id": str(plan.transaction_id),
                    "runtime_confirmation_id": runtime_confirmation_id,
                    "snapshot_digest": plan.snapshot_digest,
                    "volume_digest": _path_digest(plan.snapshot.volume_root),
                },
                risk_level=plan.risk_level,
                confirmation_required=True,
                confirmation_result="CONSUMED",
                before_state={
                    "item_count": plan.snapshot.item_count,
                    "observed_size_bytes": plan.snapshot.observed_size_bytes,
                    "enumeration_complete": plan.snapshot.enumeration_complete,
                },
                rollback={"level": "NONE", "automatic_restore": False},
            )
        )

    def blocked(self, *, transaction_id: str, plan_id: str, phase: str, error: str) -> None:
        """Record a fail-closed boundary without local paths or exception text."""
        self._record(
            AuditEvent(
                event_type="optimization.cleanup.blocked",
                app_version=self._app_version,
                git_commit=self._git_commit,
                plan_id=plan_id,
                parameters={"transaction_id": transaction_id, "phase": phase},
                confirmation_required=True,
                result={
                    "state": "BLOCKED",
                    "error_type": error,
                    "mutation_executed": False,
                    "permanent_delete_calls": 0,
                },
                rollback={"level": "NONE", "mutation_executed": False},
            )
        )

    def _record(self, event: AuditEvent) -> None:
        self._repository.record(event)


def _path_digest(path: Path) -> str:
    normalized = os.path.normcase(os.path.abspath(os.fspath(path)))
    return hashlib.sha256(normalized.encode("utf-8", errors="surrogatepass")).hexdigest()


def _item_result_payload(result: CleanupItemResult) -> dict[str, object]:
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
