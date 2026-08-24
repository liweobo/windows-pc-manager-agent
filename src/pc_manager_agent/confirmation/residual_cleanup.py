"""Durable two-tier confirmations bound to exact Stage 4D4 cleanup evidence."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import Field

from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.residual_cleanup import (
    ResidualCleanupPlan,
    ResidualCleanupPreview,
)
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.software_uninstall_analysis import canonical_digest
from pc_manager_agent.safety.residual_cleanup_preview import ResidualCleanupPreviewEngine


class ResidualCleanupConfirmationTier(StrEnum):
    """Plan and immediate approval tiers for one exact cleanup batch."""

    PLAN = "PLAN"
    RUNTIME = "RUNTIME"


class ResidualCleanupConfirmationState(StrEnum):
    """Durable single-use confirmation lifecycle."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CONSUMED = "CONSUMED"


class ResidualCleanupConfirmation(FrozenModel):
    """Approval bound to identities, policy evidence, impact, recovery, and risk."""

    confirmation_id: UUID = Field(default_factory=uuid4)
    parent_confirmation_id: UUID | None = None
    tier: ResidualCleanupConfirmationTier
    transaction_id: UUID
    plan_id: UUID
    preview_id: UUID
    plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    item_set_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    identity_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    material_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    classification_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    eligibility_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    recovery_capability_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    item_count: int = Field(ge=1)
    contained_object_count: int = Field(ge=1)
    total_bytes: int = Field(ge=0)
    risk_level: RiskLevel
    object_summary: str = Field(min_length=1, max_length=4_000)
    requested_at: datetime
    confirmed_at: datetime | None = None
    expires_at: datetime
    state: ResidualCleanupConfirmationState = ResidualCleanupConfirmationState.PENDING


class ResidualCleanupConfirmationStore(Protocol):
    """Persistence operations needed for atomic batch approval."""

    def save_confirmation(self, confirmation: ResidualCleanupConfirmation) -> None:
        """Persist one pending confirmation."""
        ...

    def get_confirmation(self, confirmation_id: UUID) -> ResidualCleanupConfirmation:
        """Load one exact confirmation."""
        ...

    def update_confirmation(self, confirmation: ResidualCleanupConfirmation) -> None:
        """Persist one decision or expiry."""
        ...

    def consume_confirmation_pair(
        self,
        plan_confirmation: ResidualCleanupConfirmation,
        runtime_confirmation: ResidualCleanupConfirmation,
    ) -> None:
        """Atomically consume both approvals and reserve dispatch."""
        ...


class ResidualCleanupConfirmationError(PermissionError):
    """Raised for absent, stale, expired, mismatched, or replayed confirmation."""


class ResidualCleanupConfirmationService:
    """Issue plan approval and a separately bound short-lived immediate approval."""

    def __init__(
        self,
        store: ResidualCleanupConfirmationStore,
        preview_engine: ResidualCleanupPreviewEngine,
        *,
        plan_ttl_seconds: int,
        runtime_ttl_seconds: int,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if plan_ttl_seconds <= 0 or runtime_ttl_seconds <= 0:
            raise ValueError("Residual cleanup confirmation TTLs must be positive")
        self._store = store
        self._preview = preview_engine
        self._plan_ttl = plan_ttl_seconds
        self._runtime_ttl = runtime_ttl_seconds
        self._now = now or (lambda: datetime.now(UTC))

    def request_plan(
        self,
        plan: ResidualCleanupPlan,
        preview: ResidualCleanupPreview,
    ) -> ResidualCleanupConfirmation:
        """Create the first durable gate for one fresh executable Preview."""
        self._preview.require_current(plan, preview)
        confirmation = self._create(
            ResidualCleanupConfirmationTier.PLAN,
            plan,
            preview,
            self._plan_ttl,
        )
        self._store.save_confirmation(confirmation)
        return confirmation

    def resolve(
        self,
        confirmation_id: UUID,
        approved: bool,
        plan: ResidualCleanupPlan,
        preview: ResidualCleanupPreview,
    ) -> ResidualCleanupConfirmation:
        """Approve or reject one pending gate after checking every binding again."""
        confirmation = self._store.get_confirmation(confirmation_id)
        if confirmation.state is not ResidualCleanupConfirmationState.PENDING:
            raise ResidualCleanupConfirmationError(
                "Residual cleanup confirmation was already resolved"
            )
        self._require_not_expired(confirmation)
        self._require_binding(confirmation, plan, preview)
        resolved = confirmation.model_copy(
            update={
                "state": (
                    ResidualCleanupConfirmationState.APPROVED
                    if approved
                    else ResidualCleanupConfirmationState.REJECTED
                ),
                "confirmed_at": self._now() if approved else None,
            }
        )
        self._store.update_confirmation(resolved)
        return resolved

    def request_runtime(
        self,
        plan_confirmation_id: UUID,
        plan: ResidualCleanupPlan,
        runtime_preview: ResidualCleanupPreview,
    ) -> ResidualCleanupConfirmation:
        """Create immediate approval after a separate fresh revalidation scan."""
        parent = self._store.get_confirmation(plan_confirmation_id)
        if (
            parent.tier is not ResidualCleanupConfirmationTier.PLAN
            or parent.state is not ResidualCleanupConfirmationState.APPROVED
        ):
            raise ResidualCleanupConfirmationError(
                "An approved residual cleanup plan confirmation is required"
            )
        self._require_not_expired(parent)
        self._require_binding(parent, plan, runtime_preview, allow_new_preview=True)
        confirmation = self._create(
            ResidualCleanupConfirmationTier.RUNTIME,
            plan,
            runtime_preview,
            self._runtime_ttl,
            parent_confirmation_id=parent.confirmation_id,
        )
        self._store.save_confirmation(confirmation)
        return confirmation

    def consume_runtime(
        self,
        runtime_confirmation_id: UUID,
        plan: ResidualCleanupPlan,
        preview: ResidualCleanupPreview,
    ) -> ResidualCleanupConfirmation:
        """Consume both exact approvals once at the durable dispatch boundary."""
        runtime = self._store.get_confirmation(runtime_confirmation_id)
        if (
            runtime.tier is not ResidualCleanupConfirmationTier.RUNTIME
            or runtime.state is not ResidualCleanupConfirmationState.APPROVED
            or runtime.parent_confirmation_id is None
        ):
            raise ResidualCleanupConfirmationError(
                "Residual cleanup immediate confirmation is absent or already used"
            )
        parent = self._store.get_confirmation(runtime.parent_confirmation_id)
        self._require_not_expired(parent)
        self._require_not_expired(runtime)
        self._require_binding(parent, plan, preview, allow_new_preview=True)
        self._require_binding(runtime, plan, preview)
        if (
            parent.tier is not ResidualCleanupConfirmationTier.PLAN
            or parent.state is not ResidualCleanupConfirmationState.APPROVED
        ):
            raise ResidualCleanupConfirmationError("Residual cleanup plan approval is stale")
        self._store.consume_confirmation_pair(parent, runtime)
        return runtime.model_copy(update={"state": ResidualCleanupConfirmationState.CONSUMED})

    def _create(
        self,
        tier: ResidualCleanupConfirmationTier,
        plan: ResidualCleanupPlan,
        preview: ResidualCleanupPreview,
        ttl_seconds: int,
        *,
        parent_confirmation_id: UUID | None = None,
    ) -> ResidualCleanupConfirmation:
        current = self._now()
        return ResidualCleanupConfirmation(
            parent_confirmation_id=parent_confirmation_id,
            tier=tier,
            transaction_id=plan.transaction_id,
            plan_id=plan.plan_id,
            preview_id=preview.preview_id,
            plan_digest=plan.canonical_digest(),
            preview_digest=preview.canonical_digest(),
            item_set_digest=preview.item_set_digest,
            identity_digest=self._identity_digest(plan),
            material_digest=self._material_digest(plan),
            classification_digest=self._classification_digest(plan),
            eligibility_digest=self._eligibility_digest(plan),
            recovery_capability_digest=self._recovery_digest(plan),
            item_count=plan.total_items,
            contained_object_count=plan.contained_object_count,
            total_bytes=plan.total_bytes,
            risk_level=plan.risk_level,
            object_summary=(
                f"Move {plan.total_items} exact residual candidate(s), containing "
                f"{plan.contained_object_count} object(s) and {plan.total_bytes} byte(s), "
                "to Windows Recycle Bin; recovery is MANUAL and no permanent fallback exists"
            ),
            requested_at=current,
            expires_at=min(
                preview.expires_at,
                current + timedelta(seconds=ttl_seconds),
            ),
        )

    def _require_binding(
        self,
        confirmation: ResidualCleanupConfirmation,
        plan: ResidualCleanupPlan,
        preview: ResidualCleanupPreview,
        *,
        allow_new_preview: bool = False,
    ) -> None:
        self._preview.require_current(plan, preview)
        if confirmation.transaction_id != plan.transaction_id:
            raise ResidualCleanupConfirmationError("Residual cleanup transaction changed")
        if not allow_new_preview and (
            confirmation.preview_id != preview.preview_id
            or confirmation.preview_digest != preview.canonical_digest()
        ):
            raise ResidualCleanupConfirmationError("Residual cleanup Preview changed")
        actual = (
            confirmation.plan_id,
            confirmation.plan_digest,
            confirmation.item_set_digest,
            confirmation.identity_digest,
            confirmation.material_digest,
            confirmation.classification_digest,
            confirmation.eligibility_digest,
            confirmation.recovery_capability_digest,
            confirmation.item_count,
            confirmation.contained_object_count,
            confirmation.total_bytes,
            confirmation.risk_level,
        )
        expected = (
            plan.plan_id,
            plan.canonical_digest(),
            preview.item_set_digest,
            self._identity_digest(plan),
            self._material_digest(plan),
            self._classification_digest(plan),
            self._eligibility_digest(plan),
            self._recovery_digest(plan),
            plan.total_items,
            plan.contained_object_count,
            plan.total_bytes,
            plan.risk_level,
        )
        if actual != expected:
            raise ResidualCleanupConfirmationError("Residual cleanup confirmation evidence changed")

    def _require_not_expired(self, confirmation: ResidualCleanupConfirmation) -> None:
        if self._now() >= confirmation.expires_at:
            expired = confirmation.model_copy(
                update={"state": ResidualCleanupConfirmationState.EXPIRED}
            )
            self._store.update_confirmation(expired)
            raise ResidualCleanupConfirmationError("Residual cleanup confirmation expired")

    @staticmethod
    def _identity_digest(plan: ResidualCleanupPlan) -> str:
        return canonical_digest(
            [
                item.candidate.fresh_identity.model_dump(mode="json")
                if item.candidate.fresh_identity is not None
                else None
                for item in plan.items
            ]
        )

    @staticmethod
    def _material_digest(plan: ResidualCleanupPlan) -> str:
        return canonical_digest(
            [
                item.candidate.material.canonical_digest()
                if item.candidate.material is not None
                else None
                for item in plan.items
            ]
        )

    @staticmethod
    def _classification_digest(plan: ResidualCleanupPlan) -> str:
        return canonical_digest(
            [
                {
                    "candidate_id": str(item.candidate.source_candidate_id),
                    "classification": item.candidate.classification.value,
                    "ownership": item.candidate.ownership_confidence.value,
                    "protection": item.candidate.protection_level.value,
                }
                for item in plan.items
            ]
        )

    @staticmethod
    def _eligibility_digest(plan: ResidualCleanupPlan) -> str:
        return canonical_digest(
            [
                {
                    "candidate_id": str(item.candidate.source_candidate_id),
                    "decision": item.candidate.eligibility.value,
                    "reasons": item.candidate.eligibility_reason_codes,
                    "path_safety": (
                        item.candidate.path_safety.canonical_digest()
                        if item.candidate.path_safety is not None
                        else None
                    ),
                    "recent_activity": (
                        item.candidate.recent_activity.canonical_digest()
                        if item.candidate.recent_activity is not None
                        else None
                    ),
                }
                for item in plan.items
            ]
        )

    @staticmethod
    def _recovery_digest(plan: ResidualCleanupPlan) -> str:
        return canonical_digest(
            [
                item.candidate.recoverability.canonical_digest()
                if item.candidate.recoverability is not None
                else None
                for item in plan.items
            ]
        )
