"""Plan compilation and fresh Preview binding for Stage 4D4 cleanup."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pc_manager_agent.domain.residual_cleanup import (
    PlannedResidualCleanupItem,
    ResidualCleanupAssessment,
    ResidualCleanupPlan,
    ResidualCleanupPreview,
    ResidualCleanupRequest,
)
from pc_manager_agent.domain.software_uninstall_analysis import canonical_digest
from pc_manager_agent.safety.path_policy import path_is_within
from pc_manager_agent.safety.residual_cleanup_policy import CleanupRiskPolicy
from pc_manager_agent.safety.residual_cleanup_revalidation import FreshResidualRevalidator
from pc_manager_agent.tools.manifest import CancellationToken


class ResidualCleanupPreviewError(PermissionError):
    """Raised when a mixed, stale, overlapping, or expired batch cannot proceed."""


class ResidualCleanupPreviewEngine:
    """Compile only all-eligible assessments and repeat the scan before runtime approval."""

    def __init__(
        self,
        revalidator: FreshResidualRevalidator,
        risk_policy: CleanupRiskPolicy,
        *,
        plan_ttl_seconds: int,
        preview_ttl_seconds: int,
    ) -> None:
        if plan_ttl_seconds <= 0 or preview_ttl_seconds <= 0:
            raise ValueError("Residual cleanup Preview TTLs must be positive")
        self._revalidator = revalidator
        self._risk = risk_policy
        self._plan_ttl = plan_ttl_seconds
        self._preview_ttl = preview_ttl_seconds

    def compile(
        self,
        assessment: ResidualCleanupAssessment,
    ) -> tuple[ResidualCleanupPlan, ResidualCleanupPreview]:
        """Create an R2 plan only when every originally selected row is eligible."""
        if not assessment.all_eligible:
            raise ResidualCleanupPreviewError(
                "The original selection contains blocked items; reselect and prepare a new batch"
            )
        self._reject_overlapping_items(assessment)
        items = tuple(
            PlannedResidualCleanupItem(sequence=index, candidate=candidate)
            for index, candidate in enumerate(assessment.items)
        )
        object_count = sum(
            item.candidate.material.tree.object_count
            for item in items
            if item.candidate.material is not None
        )
        total_bytes = sum(
            item.candidate.material.tree.total_size_bytes
            for item in items
            if item.candidate.material is not None
        )
        largest = max(
            (
                item.candidate.material.tree.largest_item_bytes
                for item in items
                if item.candidate.material is not None
            ),
            default=0,
        )
        risk = self._risk.classify(
            item_count=len(items),
            object_count=object_count,
            total_size=total_bytes,
            largest_item=largest,
        )
        current = datetime.now(UTC)
        plan = ResidualCleanupPlan(
            source_report_id=assessment.request.source_report_id,
            request_id=assessment.request.request_id,
            assessment_id=assessment.assessment_id,
            assessment_digest=assessment.invariant_digest(),
            items=items,
            total_items=len(items),
            contained_object_count=object_count,
            total_bytes=total_bytes,
            largest_item_bytes=largest,
            risk_level=risk,
            created_at=current,
            expires_at=current + timedelta(seconds=self._plan_ttl),
        )
        return plan, self._preview_for_plan(plan)

    def revalidate(
        self,
        plan: ResidualCleanupPlan,
        request: ResidualCleanupRequest,
        cancellation: CancellationToken | None = None,
    ) -> ResidualCleanupPreview:
        """Repeat the exact fresh scan and reject every material or policy change."""
        if datetime.now(UTC) >= plan.expires_at:
            raise ResidualCleanupPreviewError("Residual cleanup plan expired")
        assessment = self._revalidator.assess(request, cancellation)
        if not assessment.all_eligible:
            raise ResidualCleanupPreviewError("Residual cleanup eligibility changed")
        expected = tuple(
            (item.candidate.source_candidate_id, item.candidate.invariant_digest())
            for item in plan.items
        )
        actual = tuple(
            (item.source_candidate_id, item.invariant_digest()) for item in assessment.items
        )
        if actual != expected:
            raise ResidualCleanupPreviewError(
                "Residual identity, material snapshot, classification, or safety evidence changed"
            )
        return self._preview_for_plan(plan)

    def require_current(
        self,
        plan: ResidualCleanupPlan,
        preview: ResidualCleanupPreview,
    ) -> None:
        """Reject an expired or digest-mismatched Preview before confirmation."""
        if (
            datetime.now(UTC) >= plan.expires_at
            or datetime.now(UTC) >= preview.expires_at
            or preview.transaction_id != plan.transaction_id
            or preview.plan_id != plan.plan_id
            or preview.plan_digest != plan.canonical_digest()
            or preview.item_set_digest != self._item_set_digest(plan.items)
            or preview.risk_level is not plan.risk_level
        ):
            raise ResidualCleanupPreviewError("Residual cleanup Preview is stale or mismatched")

    def _preview_for_plan(self, plan: ResidualCleanupPlan) -> ResidualCleanupPreview:
        current = datetime.now(UTC)
        return ResidualCleanupPreview(
            transaction_id=plan.transaction_id,
            plan_id=plan.plan_id,
            plan_digest=plan.canonical_digest(),
            generated_at=current,
            expires_at=min(
                plan.expires_at,
                current + timedelta(seconds=self._preview_ttl),
            ),
            items=plan.items,
            item_set_digest=self._item_set_digest(plan.items),
            total_items=plan.total_items,
            contained_object_count=plan.contained_object_count,
            total_bytes=plan.total_bytes,
            largest_item_bytes=plan.largest_item_bytes,
            risk_level=plan.risk_level,
        )

    @staticmethod
    def _item_set_digest(items: tuple[PlannedResidualCleanupItem, ...]) -> str:
        return canonical_digest(
            [
                {
                    "item_ref": str(item.item_ref),
                    "operation_id": str(item.operation_id),
                    "sequence": item.sequence,
                    "candidate_id": str(item.candidate.source_candidate_id),
                    "candidate_invariant": item.candidate.invariant_digest(),
                    "action": item.action.value,
                    "tool_name": item.tool_name,
                }
                for item in items
            ]
        )

    @staticmethod
    def _reject_overlapping_items(assessment: ResidualCleanupAssessment) -> None:
        candidates = assessment.items
        for index, candidate in enumerate(candidates):
            if candidate.material is None:
                raise ResidualCleanupPreviewError("Eligible candidate lacks a material snapshot")
            for other in candidates[index + 1 :]:
                if other.material is None:
                    raise ResidualCleanupPreviewError(
                        "Eligible candidate lacks a material snapshot"
                    )
                candidate_directory = candidate.fresh_identity is not None and (
                    candidate.fresh_identity.kind.value == "DIRECTORY"
                )
                other_directory = other.fresh_identity is not None and (
                    other.fresh_identity.kind.value == "DIRECTORY"
                )
                if (candidate_directory and path_is_within(other.path, candidate.path)) or (
                    other_directory and path_is_within(candidate.path, other.path)
                ):
                    raise ResidualCleanupPreviewError(
                        "A cleanup batch cannot select both a parent and its child"
                    )
