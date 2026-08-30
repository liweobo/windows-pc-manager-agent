"""Durable two-tier confirmations for item cleanup and independent Bin emptying."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol, TypeAlias
from uuid import UUID, uuid4

from pydantic import Field

from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.software_uninstall_analysis import canonical_digest
from pc_manager_agent.domain.system_cleanup_execution import (
    CleanupExecutionPlan,
    CleanupExecutionPreview,
    RecycleBinEmptyPlan,
    RecycleBinEmptyPreview,
)
from pc_manager_agent.safety.system_cleanup_preview import CleanupExecutionPlanBuilder

CleanupPlan: TypeAlias = CleanupExecutionPlan | RecycleBinEmptyPlan
CleanupPreview: TypeAlias = CleanupExecutionPreview | RecycleBinEmptyPreview


class SystemCleanupConfirmationScope(StrEnum):
    """Keep ordinary cleanup and Recycle Bin empty authorities non-interchangeable."""

    ITEM_CLEANUP = "ITEM_CLEANUP"
    RECYCLE_BIN_EMPTY = "RECYCLE_BIN_EMPTY"


class SystemCleanupConfirmationTier(StrEnum):
    """Plan and short-lived immediate approval tiers."""

    PLAN = "PLAN"
    RUNTIME = "RUNTIME"


class SystemCleanupConfirmationState(StrEnum):
    """Single-use durable confirmation lifecycle."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CONSUMED = "CONSUMED"


class SystemCleanupConfirmation(FrozenModel):
    """Approval bound to exact objects, policies, adapter, recovery, and risk."""

    confirmation_id: UUID = Field(default_factory=uuid4)
    parent_confirmation_id: UUID | None = None
    scope: SystemCleanupConfirmationScope
    tier: SystemCleanupConfirmationTier
    transaction_id: UUID
    plan_id: UUID
    preview_id: UUID
    plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    item_set_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    identity_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    material_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    classification_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    protection_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    eligibility_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    adapter_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    recovery_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    item_count: int = Field(ge=1)
    contained_object_count: int = Field(ge=1)
    total_bytes: int = Field(ge=0)
    risk_level: RiskLevel
    object_summary: str = Field(min_length=1, max_length=4_000)
    requested_at: datetime
    confirmed_at: datetime | None = None
    expires_at: datetime
    state: SystemCleanupConfirmationState = SystemCleanupConfirmationState.PENDING


class SystemCleanupConfirmationStore(Protocol):
    """Persistence operations required for atomic two-confirmation consumption."""

    def save_confirmation(self, confirmation: SystemCleanupConfirmation) -> None:
        """Persist one pending confirmation."""
        ...

    def get_confirmation(self, confirmation_id: UUID) -> SystemCleanupConfirmation:
        """Load one exact confirmation."""
        ...

    def update_confirmation(self, confirmation: SystemCleanupConfirmation) -> None:
        """Persist a decision or expiry."""
        ...

    def consume_confirmation_pair(
        self,
        plan_confirmation: SystemCleanupConfirmation,
        runtime_confirmation: SystemCleanupConfirmation,
    ) -> None:
        """Atomically consume both approvals and reserve dispatch."""
        ...


class SystemCleanupConfirmationError(PermissionError):
    """Raised for stale, expired, mismatched, or replayed cleanup authority."""


class SystemCleanupConfirmationService:
    """Issue non-interchangeable item-cleanup and Recycle-Bin-empty confirmations."""

    def __init__(
        self,
        store: SystemCleanupConfirmationStore,
        item_previews: CleanupExecutionPlanBuilder,
        *,
        plan_ttl_seconds: int,
        runtime_ttl_seconds: int,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if min(plan_ttl_seconds, runtime_ttl_seconds) <= 0:
            raise ValueError("System cleanup confirmation TTLs must be positive")
        self._store = store
        self._item_previews = item_previews
        self._plan_ttl = plan_ttl_seconds
        self._runtime_ttl = runtime_ttl_seconds
        self._now = now or (lambda: datetime.now(UTC))

    def request_plan(
        self,
        plan: CleanupPlan,
        preview: CleanupPreview,
    ) -> SystemCleanupConfirmation:
        """Create the first durable gate for one exact fresh Preview."""
        self._require_current(plan, preview)
        confirmation = self._create(
            SystemCleanupConfirmationTier.PLAN,
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
        plan: CleanupPlan,
        preview: CleanupPreview,
    ) -> SystemCleanupConfirmation:
        """Resolve one pending gate after recomputing every binding."""
        confirmation = self._store.get_confirmation(confirmation_id)
        if confirmation.state is not SystemCleanupConfirmationState.PENDING:
            raise SystemCleanupConfirmationError("Cleanup confirmation was already resolved")
        self._require_not_expired(confirmation)
        self._require_binding(confirmation, plan, preview)
        resolved = confirmation.model_copy(
            update={
                "state": (
                    SystemCleanupConfirmationState.APPROVED
                    if approved
                    else SystemCleanupConfirmationState.REJECTED
                ),
                "confirmed_at": self._now() if approved else None,
            }
        )
        self._store.update_confirmation(resolved)
        return resolved

    def request_runtime(
        self,
        plan_confirmation_id: UUID,
        plan: CleanupPlan,
        preview: CleanupPreview,
    ) -> SystemCleanupConfirmation:
        """Create immediate approval only after a separate Fresh Preview."""
        parent = self._store.get_confirmation(plan_confirmation_id)
        if (
            parent.tier is not SystemCleanupConfirmationTier.PLAN
            or parent.state is not SystemCleanupConfirmationState.APPROVED
        ):
            raise SystemCleanupConfirmationError("An approved cleanup plan is required")
        self._require_not_expired(parent)
        self._require_binding(parent, plan, preview, allow_new_preview=True)
        confirmation = self._create(
            SystemCleanupConfirmationTier.RUNTIME,
            plan,
            preview,
            self._runtime_ttl,
            parent_confirmation_id=parent.confirmation_id,
        )
        self._store.save_confirmation(confirmation)
        return confirmation

    def consume_runtime(
        self,
        runtime_confirmation_id: UUID,
        plan: CleanupPlan,
        preview: CleanupPreview,
    ) -> SystemCleanupConfirmation:
        """Consume both approvals once at the durable dispatch boundary."""
        runtime = self._store.get_confirmation(runtime_confirmation_id)
        if (
            runtime.tier is not SystemCleanupConfirmationTier.RUNTIME
            or runtime.state is not SystemCleanupConfirmationState.APPROVED
            or runtime.parent_confirmation_id is None
        ):
            raise SystemCleanupConfirmationError(
                "Cleanup immediate confirmation is absent or already used"
            )
        parent = self._store.get_confirmation(runtime.parent_confirmation_id)
        self._require_not_expired(parent)
        self._require_not_expired(runtime)
        self._require_binding(parent, plan, preview, allow_new_preview=True)
        self._require_binding(runtime, plan, preview)
        if (
            parent.tier is not SystemCleanupConfirmationTier.PLAN
            or parent.state is not SystemCleanupConfirmationState.APPROVED
        ):
            raise SystemCleanupConfirmationError("Cleanup plan approval is stale")
        self._store.consume_confirmation_pair(parent, runtime)
        return runtime.model_copy(update={"state": SystemCleanupConfirmationState.CONSUMED})

    def _create(
        self,
        tier: SystemCleanupConfirmationTier,
        plan: CleanupPlan,
        preview: CleanupPreview,
        ttl_seconds: int,
        *,
        parent_confirmation_id: UUID | None = None,
    ) -> SystemCleanupConfirmation:
        now = self._now()
        binding = self._binding(plan, preview)
        return SystemCleanupConfirmation(
            parent_confirmation_id=parent_confirmation_id,
            tier=tier,
            requested_at=now,
            expires_at=min(preview.expires_at, now + timedelta(seconds=ttl_seconds)),
            object_summary=self._summary(plan),
            **binding,
        )

    def _require_binding(
        self,
        confirmation: SystemCleanupConfirmation,
        plan: CleanupPlan,
        preview: CleanupPreview,
        *,
        allow_new_preview: bool = False,
    ) -> None:
        self._require_current(plan, preview)
        expected = self._binding(plan, preview)
        actual = confirmation.model_dump(
            include=set(expected),
            mode="python",
        )
        if allow_new_preview:
            actual.pop("preview_id", None)
            actual.pop("preview_digest", None)
            expected.pop("preview_id", None)
            expected.pop("preview_digest", None)
        if actual != expected:
            raise SystemCleanupConfirmationError("Cleanup confirmation evidence changed")

    def _require_current(self, plan: CleanupPlan, preview: CleanupPreview) -> None:
        if isinstance(plan, CleanupExecutionPlan) and isinstance(preview, CleanupExecutionPreview):
            self._item_previews.require_current(plan, preview)
            return
        if isinstance(plan, RecycleBinEmptyPlan) and isinstance(preview, RecycleBinEmptyPreview):
            if (
                self._now() >= plan.expires_at
                or self._now() >= preview.expires_at
                or preview.transaction_id != plan.transaction_id
                or preview.plan_id != plan.plan_id
                or preview.plan_digest != plan.canonical_digest()
                or preview.snapshot.canonical_digest() != plan.snapshot_digest
            ):
                raise SystemCleanupConfirmationError("Recycle Bin empty Preview is stale")
            return
        raise SystemCleanupConfirmationError("Cleanup plan and Preview kinds do not match")

    def _require_not_expired(self, confirmation: SystemCleanupConfirmation) -> None:
        if self._now() >= confirmation.expires_at:
            expired = confirmation.model_copy(
                update={"state": SystemCleanupConfirmationState.EXPIRED}
            )
            self._store.update_confirmation(expired)
            raise SystemCleanupConfirmationError("Cleanup confirmation expired")

    @staticmethod
    def _binding(plan: CleanupPlan, preview: CleanupPreview) -> dict[str, object]:
        if isinstance(plan, CleanupExecutionPlan) and isinstance(preview, CleanupExecutionPreview):
            candidates = tuple(item.candidate for item in plan.items)
            return {
                "scope": SystemCleanupConfirmationScope.ITEM_CLEANUP,
                "transaction_id": plan.transaction_id,
                "plan_id": plan.plan_id,
                "preview_id": preview.preview_id,
                "plan_digest": plan.canonical_digest(),
                "preview_digest": preview.canonical_digest(),
                "item_set_digest": preview.item_set_digest,
                "identity_digest": canonical_digest(
                    [
                        item.fresh_identity.canonical_digest()
                        if item.fresh_identity is not None
                        else None
                        for item in candidates
                    ]
                ),
                "material_digest": canonical_digest(
                    [
                        item.material.canonical_digest() if item.material is not None else None
                        for item in candidates
                    ]
                ),
                "classification_digest": canonical_digest(
                    [(item.category.value, item.source) for item in candidates]
                ),
                "protection_digest": canonical_digest(
                    [item.protection_level.value for item in candidates]
                ),
                "eligibility_digest": canonical_digest(
                    [
                        (
                            item.eligibility.value,
                            item.reason_codes,
                            item.path_safety.canonical_digest()
                            if item.path_safety is not None
                            else None,
                            item.activity.canonical_digest() if item.activity is not None else None,
                        )
                        for item in candidates
                    ]
                ),
                "adapter_digest": canonical_digest(
                    [item.cleanup_adapter_type.value for item in candidates]
                ),
                "recovery_digest": canonical_digest(
                    [
                        item.recoverability_evidence.canonical_digest()
                        if item.recoverability_evidence is not None
                        else None
                        for item in candidates
                    ]
                ),
                "item_count": plan.total_items,
                "contained_object_count": plan.contained_object_count,
                "total_bytes": plan.total_observed_bytes,
                "risk_level": plan.risk_level,
            }
        if isinstance(plan, RecycleBinEmptyPlan) and isinstance(preview, RecycleBinEmptyPreview):
            snapshot_digest = plan.snapshot_digest
            return {
                "scope": SystemCleanupConfirmationScope.RECYCLE_BIN_EMPTY,
                "transaction_id": plan.transaction_id,
                "plan_id": plan.plan_id,
                "preview_id": preview.preview_id,
                "plan_digest": plan.canonical_digest(),
                "preview_digest": preview.canonical_digest(),
                "item_set_digest": snapshot_digest,
                "identity_digest": canonical_digest(str(plan.snapshot.volume_root)),
                "material_digest": snapshot_digest,
                "classification_digest": canonical_digest("RECYCLE_BIN_CONTENT"),
                "protection_digest": canonical_digest("RECOVERY_BOUNDARY"),
                "eligibility_digest": canonical_digest(
                    (plan.snapshot.enumeration_complete, snapshot_digest)
                ),
                "adapter_digest": canonical_digest("RECYCLE_BIN_EMPTY"),
                "recovery_digest": canonical_digest("NONE"),
                "item_count": plan.snapshot.item_count,
                "contained_object_count": plan.snapshot.item_count,
                "total_bytes": plan.snapshot.observed_size_bytes,
                "risk_level": RiskLevel.R2_HIGH_IMPACT,
            }
        raise SystemCleanupConfirmationError("Cleanup plan and Preview kinds do not match")

    @staticmethod
    def _summary(plan: CleanupPlan) -> str:
        if isinstance(plan, CleanupExecutionPlan):
            return (
                f"Move {plan.total_items} exact cleanup item(s), containing "
                f"{plan.contained_object_count} object(s) and "
                f"{plan.total_observed_bytes} byte(s), to Windows Recycle Bin. "
                "Recovery is MANUAL; no permanent-delete fallback exists."
            )
        return (
            f"Permanently remove the current user's {plan.snapshot.item_count} Recycle Bin "
            f"item(s), observed as {plan.snapshot.observed_size_bytes} byte(s), from exact "
            f"volume {plan.snapshot.volume_root}. Agent recovery is NONE."
        )
