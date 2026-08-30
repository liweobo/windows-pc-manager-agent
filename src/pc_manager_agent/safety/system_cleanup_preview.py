"""Plan compilation and Fresh Preview binding for Stage 4E2 item cleanup."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from pc_manager_agent.domain.software_uninstall_analysis import canonical_digest
from pc_manager_agent.domain.system_cleanup_execution import (
    CleanupEligibilityDecision,
    CleanupExecutionPlan,
    CleanupExecutionPreview,
    CleanupRecoveryLevel,
    CleanupRecoverySummary,
    PlannedCleanupItem,
    SystemCleanupAssessment,
)
from pc_manager_agent.safety.path_policy import path_is_within
from pc_manager_agent.safety.system_cleanup_policy import SystemCleanupRiskPolicy
from pc_manager_agent.safety.system_cleanup_revalidation import (
    FreshCleanupCandidateRevalidator,
    SystemCleanupRevalidationError,
)
from pc_manager_agent.tools.manifest import CancellationToken


class SystemCleanupPreviewError(PermissionError):
    """Raised when selected Fresh items cannot form one exact executable Preview."""


class CleanupExecutionPlanBuilder:
    """Compile only the user's second, exact, all-eligible item selection."""

    def __init__(
        self,
        revalidator: FreshCleanupCandidateRevalidator,
        risk_policy: SystemCleanupRiskPolicy,
        *,
        max_selected_items: int,
        plan_ttl_seconds: int,
        preview_ttl_seconds: int,
    ) -> None:
        if min(max_selected_items, plan_ttl_seconds, preview_ttl_seconds) <= 0:
            raise ValueError("Cleanup Preview limits must be positive")
        self._revalidator = revalidator
        self._risk = risk_policy
        self._max_selected = max_selected_items
        self._plan_ttl = plan_ttl_seconds
        self._preview_ttl = preview_ttl_seconds

    def compile(
        self,
        assessment: SystemCleanupAssessment,
        selected_item_refs: tuple[UUID, ...],
    ) -> tuple[CleanupExecutionPlan, CleanupExecutionPreview]:
        """Reject mixed selections rather than silently dropping blocked rows."""
        if not selected_item_refs or len(selected_item_refs) > self._max_selected:
            raise SystemCleanupPreviewError(
                f"Select between 1 and {self._max_selected} exact cleanup items"
            )
        if len(selected_item_refs) != len(set(selected_item_refs)):
            raise SystemCleanupPreviewError("Cleanup item selection contains duplicates")
        by_ref = {item.item_ref: item for item in assessment.items}
        if set(selected_item_refs) - set(by_ref):
            raise SystemCleanupPreviewError("Cleanup item selection is stale or unknown")
        selected = tuple(by_ref[item_ref] for item_ref in selected_item_refs)
        blocked = tuple(
            item for item in selected if item.eligibility is not CleanupEligibilityDecision.ELIGIBLE
        )
        if blocked:
            raise SystemCleanupPreviewError(
                "The selected batch contains blocked or deferred items; create a new selection"
            )
        self._reject_overlapping(selected)
        items = tuple(
            PlannedCleanupItem(sequence=index, candidate=candidate)
            for index, candidate in enumerate(selected)
        )
        materials = tuple(item.candidate.material for item in items)
        if any(material is None for material in materials):
            raise SystemCleanupPreviewError("Eligible cleanup item lacks material evidence")
        object_count = sum(
            material.tree.object_count for material in materials if material is not None
        )
        total_bytes = sum(
            material.tree.total_size_bytes for material in materials if material is not None
        )
        largest = max(
            (material.tree.largest_item_bytes for material in materials if material is not None),
            default=0,
        )
        risk = self._risk.classify(
            item_count=len(items),
            object_count=object_count,
            total_bytes=total_bytes,
            largest_item_bytes=largest,
        )
        now = datetime.now(UTC)
        plan = CleanupExecutionPlan(
            request_id=assessment.request.request_id,
            source_report_id=assessment.request.source_report_id,
            assessment_id=assessment.assessment_id,
            assessment_digest=assessment.invariant_digest(),
            items=items,
            total_items=len(items),
            contained_object_count=object_count,
            total_observed_bytes=total_bytes,
            largest_item_bytes=largest,
            risk_level=risk,
            recovery_summary=CleanupRecoverySummary(
                level=CleanupRecoveryLevel.MANUAL,
                manual_items=len(items),
                none_items=0,
            ),
            created_at=now,
            expires_at=now + timedelta(seconds=self._plan_ttl),
        )
        return plan, self._preview_for(plan)

    def revalidate(
        self,
        plan: CleanupExecutionPlan,
        cancellation: CancellationToken | None = None,
    ) -> CleanupExecutionPreview:
        """Re-scan only selected exact items before the immediate confirmation."""
        if datetime.now(UTC) >= plan.expires_at:
            raise SystemCleanupPreviewError("System cleanup plan expired")
        try:
            for item in plan.items:
                current = self._revalidator.require_unchanged(item.candidate, cancellation)
                if current.invariant_digest() != item.candidate.invariant_digest():
                    raise SystemCleanupPreviewError("Cleanup item evidence changed")
        except SystemCleanupRevalidationError as exc:
            raise SystemCleanupPreviewError(str(exc)) from exc
        return self._preview_for(plan)

    def require_current(
        self,
        plan: CleanupExecutionPlan,
        preview: CleanupExecutionPreview,
    ) -> None:
        """Reject expired Preview, changed item set, risk, recovery, or plan digest."""
        now = datetime.now(UTC)
        if (
            now >= plan.expires_at
            or now >= preview.expires_at
            or preview.transaction_id != plan.transaction_id
            or preview.plan_id != plan.plan_id
            or preview.plan_digest != plan.canonical_digest()
            or preview.item_set_digest != self._item_set_digest(plan.items)
            or preview.risk_level is not plan.risk_level
            or preview.recovery_summary != plan.recovery_summary
        ):
            raise SystemCleanupPreviewError("System cleanup Preview is stale or mismatched")

    def _preview_for(self, plan: CleanupExecutionPlan) -> CleanupExecutionPreview:
        now = datetime.now(UTC)
        return CleanupExecutionPreview(
            transaction_id=plan.transaction_id,
            plan_id=plan.plan_id,
            plan_digest=plan.canonical_digest(),
            generated_at=now,
            expires_at=min(plan.expires_at, now + timedelta(seconds=self._preview_ttl)),
            items=plan.items,
            item_set_digest=self._item_set_digest(plan.items),
            total_items=plan.total_items,
            contained_object_count=plan.contained_object_count,
            total_observed_bytes=plan.total_observed_bytes,
            largest_item_bytes=plan.largest_item_bytes,
            risk_level=plan.risk_level,
            recovery_summary=plan.recovery_summary,
        )

    @staticmethod
    def _item_set_digest(items: tuple[PlannedCleanupItem, ...]) -> str:
        return canonical_digest(
            [
                {
                    "item_ref": str(item.candidate.item_ref),
                    "operation_id": str(item.operation_id),
                    "sequence": item.sequence,
                    "candidate_invariant": item.candidate.invariant_digest(),
                    "action": item.action.value,
                    "tool_name": item.tool_name,
                    "recovery": item.rollback_level.value,
                }
                for item in items
            ]
        )

    @staticmethod
    def _reject_overlapping(candidates: tuple[object, ...]) -> None:
        from pc_manager_agent.domain.system_cleanup_execution import CleanupExecutionCandidate

        typed = tuple(CleanupExecutionCandidate.model_validate(item) for item in candidates)
        for index, candidate in enumerate(typed):
            if candidate.path is None or candidate.fresh_identity is None:
                raise SystemCleanupPreviewError("Eligible cleanup item lacks identity")
            for other in typed[index + 1 :]:
                if other.path is None or other.fresh_identity is None:
                    raise SystemCleanupPreviewError("Eligible cleanup item lacks identity")
                candidate_directory = candidate.fresh_identity.state.kind.value == "DIRECTORY"
                other_directory = other.fresh_identity.state.kind.value == "DIRECTORY"
                if (candidate_directory and path_is_within(other.path, candidate.path)) or (
                    other_directory and path_is_within(candidate.path, other.path)
                ):
                    raise SystemCleanupPreviewError(
                        "A cleanup batch cannot select both a parent and its child"
                    )
