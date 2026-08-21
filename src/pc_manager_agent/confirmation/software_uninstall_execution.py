"""Durable two-tier confirmations for one exact MSI uninstall transaction."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import Field

from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.software_uninstall_execution import (
    MsiUninstallPlan,
    MsiUninstallPreview,
)


class MsiUninstallConfirmationTier(StrEnum):
    """Plan and immediate runtime gates for irreversible uninstall."""

    PLAN = "plan"
    RUNTIME = "runtime"


class MsiUninstallConfirmationState(StrEnum):
    """Durable lifecycle that prevents replay."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CONSUMED = "consumed"


class MsiUninstallConfirmation(FrozenModel):
    """Approval bound to identity, ProductCode, capability, policy, risk, and Preview."""

    confirmation_id: UUID = Field(default_factory=uuid4)
    parent_confirmation_id: UUID | None = None
    tier: MsiUninstallConfirmationTier
    transaction_id: UUID
    operation_id: UUID
    plan_id: UUID
    preview_id: UUID
    plan_digest: str = Field(min_length=64, max_length=64)
    preview_digest: str = Field(min_length=64, max_length=64)
    invariant_digest: str = Field(min_length=64, max_length=64)
    identity_digest: str = Field(min_length=64, max_length=64)
    product_code_digest: str = Field(min_length=64, max_length=64)
    capability_digest: str = Field(min_length=64, max_length=64)
    safety_digest: str = Field(min_length=64, max_length=64)
    preflight_digest: str = Field(min_length=64, max_length=64)
    risk_level: RiskLevel
    object_summary: str = Field(min_length=1, max_length=1_000)
    requested_at: datetime
    confirmed_at: datetime | None = None
    expires_at: datetime
    state: MsiUninstallConfirmationState = MsiUninstallConfirmationState.PENDING


class MsiConfirmationStore(Protocol):
    """Persistence boundary used by the confirmation service."""

    def save_plan_confirmation(self, confirmation: MsiUninstallConfirmation) -> None:
        """Persist the first gate and advance its transaction."""
        ...

    def save_runtime_confirmation(
        self,
        confirmation: MsiUninstallConfirmation,
        preview: MsiUninstallPreview,
    ) -> None:
        """Persist fresh Preview evidence and the immediate gate."""
        ...

    def get_confirmation(self, confirmation_id: UUID) -> MsiUninstallConfirmation:
        """Load one durable confirmation."""
        ...

    def resolve_confirmation(self, confirmation: MsiUninstallConfirmation) -> None:
        """Persist approval, rejection, or expiry."""
        ...

    def consume_confirmation_pair(
        self,
        plan_confirmation: MsiUninstallConfirmation,
        runtime_confirmation: MsiUninstallConfirmation,
    ) -> None:
        """Atomically consume both approvals and authorize dispatch once."""
        ...


class MsiUninstallConfirmationError(RuntimeError):
    """Raised for stale, mismatched, expired, absent, or replayed approvals."""


class MsiUninstallConfirmationService:
    """Issue, resolve, and atomically consume durable uninstall confirmations."""

    def __init__(
        self,
        store: MsiConfirmationStore,
        *,
        plan_ttl_seconds: int = 300,
        runtime_ttl_seconds: int = 60,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if plan_ttl_seconds <= 0 or runtime_ttl_seconds <= 0:
            raise ValueError("MSI confirmation TTLs must be positive")
        self._store = store
        self._plan_ttl = plan_ttl_seconds
        self._runtime_ttl = runtime_ttl_seconds
        self._now = now or (lambda: datetime.now(UTC))

    def request_plan(
        self,
        plan: MsiUninstallPlan,
        preview: MsiUninstallPreview,
    ) -> MsiUninstallConfirmation:
        """Persist a first confirmation only for a current executable Preview."""
        self._require_executable(plan, preview)
        confirmation = self._create(
            MsiUninstallConfirmationTier.PLAN,
            plan,
            preview,
            self._plan_ttl,
        )
        self._store.save_plan_confirmation(confirmation)
        return confirmation

    def resolve_plan(
        self,
        confirmation_id: UUID,
        approved: bool,
        plan: MsiUninstallPlan,
        preview: MsiUninstallPreview,
    ) -> MsiUninstallConfirmation:
        """Resolve the first gate against its original exact Preview."""
        return self._resolve(
            confirmation_id,
            approved,
            MsiUninstallConfirmationTier.PLAN,
            plan,
            preview,
        )

    def request_runtime(
        self,
        plan_confirmation_id: UUID,
        plan: MsiUninstallPlan,
        preview: MsiUninstallPreview,
    ) -> MsiUninstallConfirmation:
        """Persist a short-lived gate after fresh evidence reproduces all invariants."""
        parent = self._store.get_confirmation(plan_confirmation_id)
        if (
            parent.tier is not MsiUninstallConfirmationTier.PLAN
            or parent.state is not MsiUninstallConfirmationState.APPROVED
        ):
            raise MsiUninstallConfirmationError("Approved MSI plan confirmation is required")
        self._require_not_expired(parent)
        self._require_executable(plan, preview)
        if parent.invariant_digest != preview.invariant_digest():
            raise MsiUninstallConfirmationError("MSI identity, policy, risk, or preflight changed")
        confirmation = self._create(
            MsiUninstallConfirmationTier.RUNTIME,
            plan,
            preview,
            self._runtime_ttl,
            parent_confirmation_id=parent.confirmation_id,
        )
        self._store.save_runtime_confirmation(confirmation, preview)
        return confirmation

    def resolve_runtime(
        self,
        confirmation_id: UUID,
        approved: bool,
        plan: MsiUninstallPlan,
        preview: MsiUninstallPreview,
    ) -> MsiUninstallConfirmation:
        """Resolve the object-specific immediate gate."""
        return self._resolve(
            confirmation_id,
            approved,
            MsiUninstallConfirmationTier.RUNTIME,
            plan,
            preview,
        )

    def consume_runtime(
        self,
        confirmation_id: UUID,
        plan: MsiUninstallPlan,
        preview: MsiUninstallPreview,
    ) -> MsiUninstallConfirmation:
        """Atomically consume both approvals at the final execution boundary."""
        runtime = self._store.get_confirmation(confirmation_id)
        if (
            runtime.tier is not MsiUninstallConfirmationTier.RUNTIME
            or runtime.state is not MsiUninstallConfirmationState.APPROVED
            or runtime.parent_confirmation_id is None
        ):
            raise MsiUninstallConfirmationError("Runtime confirmation is absent or already used")
        self._require_current(runtime, plan, preview)
        self._require_not_expired(runtime)
        parent = self._store.get_confirmation(runtime.parent_confirmation_id)
        if (
            parent.tier is not MsiUninstallConfirmationTier.PLAN
            or parent.state is not MsiUninstallConfirmationState.APPROVED
            or parent.invariant_digest != preview.invariant_digest()
        ):
            raise MsiUninstallConfirmationError("Plan confirmation is stale or unavailable")
        self._require_not_expired(parent)
        self._store.consume_confirmation_pair(parent, runtime)
        return runtime.model_copy(update={"state": MsiUninstallConfirmationState.CONSUMED})

    def _resolve(
        self,
        confirmation_id: UUID,
        approved: bool,
        tier: MsiUninstallConfirmationTier,
        plan: MsiUninstallPlan,
        preview: MsiUninstallPreview,
    ) -> MsiUninstallConfirmation:
        current = self._store.get_confirmation(confirmation_id)
        if current.tier is not tier or current.state is not MsiUninstallConfirmationState.PENDING:
            raise MsiUninstallConfirmationError("MSI confirmation tier or state is invalid")
        self._require_current(current, plan, preview)
        self._require_not_expired(current)
        resolved = current.model_copy(
            update={
                "state": (
                    MsiUninstallConfirmationState.APPROVED
                    if approved
                    else MsiUninstallConfirmationState.REJECTED
                ),
                "confirmed_at": self._now() if approved else None,
            }
        )
        self._store.resolve_confirmation(resolved)
        return resolved

    def _create(
        self,
        tier: MsiUninstallConfirmationTier,
        plan: MsiUninstallPlan,
        preview: MsiUninstallPreview,
        ttl_seconds: int,
        *,
        parent_confirmation_id: UUID | None = None,
    ) -> MsiUninstallConfirmation:
        current = self._now()
        return MsiUninstallConfirmation(
            parent_confirmation_id=parent_confirmation_id,
            tier=tier,
            transaction_id=plan.transaction_id,
            operation_id=plan.operation_id,
            plan_id=plan.plan_id,
            preview_id=preview.preview_id,
            plan_digest=plan.canonical_digest(),
            preview_digest=preview.canonical_digest(),
            invariant_digest=preview.invariant_digest(),
            identity_digest=preview.identity_digest,
            product_code_digest=preview.validated_product.product_code_digest,
            capability_digest=preview.capability.canonical_digest(),
            safety_digest=preview.execution_assessment.canonical_digest(),
            preflight_digest=preview.preflight.canonical_digest(),
            risk_level=preview.execution_assessment.risk_level,
            object_summary=(
                f"{preview.target.display_name} {preview.target.display_version or ''}".strip()
            ),
            requested_at=current,
            expires_at=current + timedelta(seconds=ttl_seconds),
        )

    def _require_not_expired(self, confirmation: MsiUninstallConfirmation) -> None:
        if self._now() >= confirmation.expires_at:
            expired = confirmation.model_copy(
                update={"state": MsiUninstallConfirmationState.EXPIRED}
            )
            self._store.resolve_confirmation(expired)
            raise MsiUninstallConfirmationError("MSI confirmation expired")

    def _require_executable(
        self,
        plan: MsiUninstallPlan,
        preview: MsiUninstallPreview,
    ) -> None:
        if (
            not preview.executable
            or self._now() >= preview.expires_at
            or preview.plan_id != plan.plan_id
            or preview.transaction_id != plan.transaction_id
            or preview.operation_id != plan.operation_id
            or preview.plan_digest != plan.canonical_digest()
            or preview.identity_digest != plan.identity_digest
            or preview.execution_assessment.risk_level is not plan.risk_level
        ):
            raise MsiUninstallConfirmationError("MSI Preview is blocked, stale, or mismatched")

    def _require_current(
        self,
        confirmation: MsiUninstallConfirmation,
        plan: MsiUninstallPlan,
        preview: MsiUninstallPreview,
    ) -> None:
        self._require_executable(plan, preview)
        actual = (
            confirmation.transaction_id,
            confirmation.operation_id,
            confirmation.plan_id,
            confirmation.preview_id,
            confirmation.plan_digest,
            confirmation.preview_digest,
            confirmation.invariant_digest,
            confirmation.identity_digest,
            confirmation.product_code_digest,
            confirmation.capability_digest,
            confirmation.safety_digest,
            confirmation.preflight_digest,
            confirmation.risk_level,
        )
        expected = (
            plan.transaction_id,
            plan.operation_id,
            plan.plan_id,
            preview.preview_id,
            plan.canonical_digest(),
            preview.canonical_digest(),
            preview.invariant_digest(),
            preview.identity_digest,
            preview.validated_product.product_code_digest,
            preview.capability.canonical_digest(),
            preview.execution_assessment.canonical_digest(),
            preview.preflight.canonical_digest(),
            plan.risk_level,
        )
        if actual != expected:
            raise MsiUninstallConfirmationError("MSI confirmation bindings changed")
