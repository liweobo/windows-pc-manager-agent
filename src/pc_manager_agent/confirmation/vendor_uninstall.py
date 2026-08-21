"""Durable two-tier confirmations for one exact Vendor uninstall transaction."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import Field

from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.vendor_uninstall import VendorUninstallPlan, VendorUninstallPreview


class VendorUninstallConfirmationTier(StrEnum):
    """Plan and immediate runtime gates for irreversible Vendor uninstall."""

    PLAN = "plan"
    RUNTIME = "runtime"


class VendorUninstallConfirmationState(StrEnum):
    """Durable lifecycle that prevents replay."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CONSUMED = "consumed"


class VendorUninstallConfirmation(FrozenModel):
    """Approval bound to software, executable, arguments, policy, risk, and preflight."""

    confirmation_id: UUID = Field(default_factory=uuid4)
    parent_confirmation_id: UUID | None = None
    tier: VendorUninstallConfirmationTier
    transaction_id: UUID
    operation_id: UUID
    plan_id: UUID
    preview_id: UUID
    plan_digest: str = Field(min_length=64, max_length=64)
    preview_digest: str = Field(min_length=64, max_length=64)
    invariant_digest: str = Field(min_length=64, max_length=64)
    identity_digest: str = Field(min_length=64, max_length=64)
    vendor_identity_digest: str = Field(min_length=64, max_length=64)
    argument_digest: str = Field(min_length=64, max_length=64)
    executable_file_digest: str = Field(min_length=64, max_length=64)
    capability_digest: str = Field(min_length=64, max_length=64)
    safety_digest: str = Field(min_length=64, max_length=64)
    preflight_digest: str = Field(min_length=64, max_length=64)
    risk_level: RiskLevel
    object_summary: str = Field(min_length=1, max_length=1_000)
    requested_at: datetime
    confirmed_at: datetime | None = None
    expires_at: datetime
    state: VendorUninstallConfirmationState = VendorUninstallConfirmationState.PENDING


class VendorConfirmationStore(Protocol):
    """Persistence boundary used by the Vendor confirmation service."""

    def save_plan_confirmation(self, confirmation: VendorUninstallConfirmation) -> None:
        """Persist the plan gate and advance its transaction."""
        ...

    def save_runtime_confirmation(
        self,
        confirmation: VendorUninstallConfirmation,
        preview: VendorUninstallPreview,
    ) -> None:
        """Persist fresh evidence and the immediate gate."""
        ...

    def get_confirmation(self, confirmation_id: UUID) -> VendorUninstallConfirmation:
        """Load one durable confirmation."""
        ...

    def resolve_confirmation(self, confirmation: VendorUninstallConfirmation) -> None:
        """Persist approval, rejection, or expiry."""
        ...

    def consume_confirmation_pair(
        self,
        plan_confirmation: VendorUninstallConfirmation,
        runtime_confirmation: VendorUninstallConfirmation,
    ) -> None:
        """Atomically consume both approvals and authorize dispatch once."""
        ...


class VendorUninstallConfirmationError(RuntimeError):
    """Raised for stale, mismatched, expired, absent, or replayed approvals."""


class VendorUninstallConfirmationService:
    """Issue, resolve, and atomically consume durable Vendor confirmations."""

    def __init__(
        self,
        store: VendorConfirmationStore,
        *,
        plan_ttl_seconds: int = 300,
        runtime_ttl_seconds: int = 60,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if plan_ttl_seconds <= 0 or runtime_ttl_seconds <= 0:
            raise ValueError("Vendor confirmation TTLs must be positive")
        self._store = store
        self._plan_ttl = plan_ttl_seconds
        self._runtime_ttl = runtime_ttl_seconds
        self._now = now or (lambda: datetime.now(UTC))

    def request_plan(
        self,
        plan: VendorUninstallPlan,
        preview: VendorUninstallPreview,
    ) -> VendorUninstallConfirmation:
        """Persist a first confirmation only for a current executable Preview."""
        self._require_executable(plan, preview)
        confirmation = self._create(
            VendorUninstallConfirmationTier.PLAN,
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
        plan: VendorUninstallPlan,
        preview: VendorUninstallPreview,
    ) -> VendorUninstallConfirmation:
        """Resolve the first exact gate against its original Preview."""
        return self._resolve(
            confirmation_id,
            approved,
            VendorUninstallConfirmationTier.PLAN,
            plan,
            preview,
        )

    def request_runtime(
        self,
        plan_confirmation_id: UUID,
        plan: VendorUninstallPlan,
        preview: VendorUninstallPreview,
    ) -> VendorUninstallConfirmation:
        """Issue a short-lived gate only when every invariant reproduces exactly."""
        parent = self._store.get_confirmation(plan_confirmation_id)
        if (
            parent.tier is not VendorUninstallConfirmationTier.PLAN
            or parent.state is not VendorUninstallConfirmationState.APPROVED
        ):
            raise VendorUninstallConfirmationError("Approved Vendor plan confirmation is required")
        self._require_not_expired(parent)
        self._require_executable(plan, preview)
        if parent.invariant_digest != preview.invariant_digest():
            raise VendorUninstallConfirmationError(
                "Vendor executable, arguments, policy, risk, or preflight changed"
            )
        confirmation = self._create(
            VendorUninstallConfirmationTier.RUNTIME,
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
        plan: VendorUninstallPlan,
        preview: VendorUninstallPreview,
    ) -> VendorUninstallConfirmation:
        """Resolve the object-specific immediate confirmation."""
        return self._resolve(
            confirmation_id,
            approved,
            VendorUninstallConfirmationTier.RUNTIME,
            plan,
            preview,
        )

    def consume_runtime(
        self,
        confirmation_id: UUID,
        plan: VendorUninstallPlan,
        preview: VendorUninstallPreview,
    ) -> VendorUninstallConfirmation:
        """Atomically consume both exact approvals at the final execution boundary."""
        runtime = self._store.get_confirmation(confirmation_id)
        if (
            runtime.tier is not VendorUninstallConfirmationTier.RUNTIME
            or runtime.state is not VendorUninstallConfirmationState.APPROVED
            or runtime.parent_confirmation_id is None
        ):
            raise VendorUninstallConfirmationError(
                "Vendor runtime confirmation is absent or already used"
            )
        self._require_current(runtime, plan, preview)
        self._require_not_expired(runtime)
        parent = self._store.get_confirmation(runtime.parent_confirmation_id)
        if (
            parent.tier is not VendorUninstallConfirmationTier.PLAN
            or parent.state is not VendorUninstallConfirmationState.APPROVED
            or parent.invariant_digest != preview.invariant_digest()
        ):
            raise VendorUninstallConfirmationError("Vendor plan confirmation is stale")
        self._require_not_expired(parent)
        self._store.consume_confirmation_pair(parent, runtime)
        return runtime.model_copy(update={"state": VendorUninstallConfirmationState.CONSUMED})

    def _resolve(
        self,
        confirmation_id: UUID,
        approved: bool,
        tier: VendorUninstallConfirmationTier,
        plan: VendorUninstallPlan,
        preview: VendorUninstallPreview,
    ) -> VendorUninstallConfirmation:
        current = self._store.get_confirmation(confirmation_id)
        if (
            current.tier is not tier
            or current.state is not VendorUninstallConfirmationState.PENDING
        ):
            raise VendorUninstallConfirmationError("Vendor confirmation tier or state is invalid")
        self._require_current(current, plan, preview)
        self._require_not_expired(current)
        resolved = current.model_copy(
            update={
                "state": (
                    VendorUninstallConfirmationState.APPROVED
                    if approved
                    else VendorUninstallConfirmationState.REJECTED
                ),
                "confirmed_at": self._now() if approved else None,
            }
        )
        self._store.resolve_confirmation(resolved)
        return resolved

    def _create(
        self,
        tier: VendorUninstallConfirmationTier,
        plan: VendorUninstallPlan,
        preview: VendorUninstallPreview,
        ttl_seconds: int,
        *,
        parent_confirmation_id: UUID | None = None,
    ) -> VendorUninstallConfirmation:
        current = self._now()
        return VendorUninstallConfirmation(
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
            vendor_identity_digest=preview.vendor_identity.invariant_digest(),
            argument_digest=preview.vendor_identity.argument_assessment.argument_fingerprint,
            executable_file_digest=(
                preview.vendor_identity.executable.file_identity.canonical_digest()
            ),
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

    def _require_not_expired(self, confirmation: VendorUninstallConfirmation) -> None:
        if self._now() >= confirmation.expires_at:
            expired = confirmation.model_copy(
                update={"state": VendorUninstallConfirmationState.EXPIRED}
            )
            self._store.resolve_confirmation(expired)
            raise VendorUninstallConfirmationError("Vendor confirmation expired")

    def _require_executable(
        self,
        plan: VendorUninstallPlan,
        preview: VendorUninstallPreview,
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
            raise VendorUninstallConfirmationError(
                "Vendor Preview is blocked, stale, or mismatched"
            )

    def _require_current(
        self,
        confirmation: VendorUninstallConfirmation,
        plan: VendorUninstallPlan,
        preview: VendorUninstallPreview,
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
            confirmation.vendor_identity_digest,
            confirmation.argument_digest,
            confirmation.executable_file_digest,
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
            preview.vendor_identity.invariant_digest(),
            preview.vendor_identity.argument_assessment.argument_fingerprint,
            preview.vendor_identity.executable.file_identity.canonical_digest(),
            preview.capability.canonical_digest(),
            preview.execution_assessment.canonical_digest(),
            preview.preflight.canonical_digest(),
            plan.risk_level,
        )
        if actual != expected:
            raise VendorUninstallConfirmationError("Vendor confirmation bindings changed")
