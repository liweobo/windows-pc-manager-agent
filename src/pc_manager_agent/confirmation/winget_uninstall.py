"""Durable two-tier confirmations for one exact winget uninstall transaction."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import Field

from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.winget_uninstall import WingetUninstallPlan, WingetUninstallPreview


class WingetUninstallConfirmationTier(StrEnum):
    """Plan and immediate gates for irreversible package removal."""

    PLAN = "plan"
    RUNTIME = "runtime"


class WingetUninstallConfirmationState(StrEnum):
    """Durable lifecycle that makes every approval single-use."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CONSUMED = "consumed"


class WingetUninstallConfirmation(FrozenModel):
    """Approval bound to package, software, mapping, flags, policy, and executable identity."""

    confirmation_id: UUID = Field(default_factory=uuid4)
    parent_confirmation_id: UUID | None = None
    tier: WingetUninstallConfirmationTier
    transaction_id: UUID
    operation_id: UUID
    plan_id: UUID
    preview_id: UUID
    plan_digest: str = Field(min_length=64, max_length=64)
    preview_digest: str = Field(min_length=64, max_length=64)
    invariant_digest: str = Field(min_length=64, max_length=64)
    package_identity_digest: str = Field(min_length=64, max_length=64)
    software_identity_digest: str = Field(min_length=64, max_length=64)
    mapping_digest: str = Field(min_length=64, max_length=64)
    executable_identity_digest: str = Field(min_length=64, max_length=64)
    capability_digest: str = Field(min_length=64, max_length=64)
    safety_digest: str = Field(min_length=64, max_length=64)
    preflight_digest: str = Field(min_length=64, max_length=64)
    risk_level: RiskLevel
    object_summary: str = Field(min_length=1, max_length=1_000)
    requested_at: datetime
    confirmed_at: datetime | None = None
    expires_at: datetime
    state: WingetUninstallConfirmationState = WingetUninstallConfirmationState.PENDING


class WingetConfirmationStore(Protocol):
    """Persistence operations required for atomic approval consumption."""

    def save_plan_confirmation(self, confirmation: WingetUninstallConfirmation) -> None:
        """Persist the first gate."""
        ...

    def save_runtime_confirmation(
        self,
        confirmation: WingetUninstallConfirmation,
        preview: WingetUninstallPreview,
    ) -> None:
        """Persist fresh evidence and the immediate gate."""
        ...

    def get_confirmation(self, confirmation_id: UUID) -> WingetUninstallConfirmation:
        """Load one durable gate."""
        ...

    def resolve_confirmation(self, confirmation: WingetUninstallConfirmation) -> None:
        """Persist approval, rejection, or expiry."""
        ...

    def consume_confirmation_pair(
        self,
        plan_confirmation: WingetUninstallConfirmation,
        runtime_confirmation: WingetUninstallConfirmation,
    ) -> None:
        """Consume both approvals and reserve one dispatch atomically."""
        ...


class WingetUninstallConfirmationError(RuntimeError):
    """Raised for stale, mismatched, expired, absent, or replayed approval."""


class WingetUninstallConfirmationService:
    """Issue, resolve, and consume digest-bound winget confirmations."""

    def __init__(
        self,
        store: WingetConfirmationStore,
        *,
        plan_ttl_seconds: int = 300,
        runtime_ttl_seconds: int = 60,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if plan_ttl_seconds <= 0 or runtime_ttl_seconds <= 0:
            raise ValueError("winget confirmation TTLs must be positive")
        self._store = store
        self._plan_ttl = plan_ttl_seconds
        self._runtime_ttl = runtime_ttl_seconds
        self._now = now or (lambda: datetime.now(UTC))

    def request_plan(
        self,
        plan: WingetUninstallPlan,
        preview: WingetUninstallPreview,
    ) -> WingetUninstallConfirmation:
        """Create the first gate only for a current executable Preview."""
        self._require_executable(plan, preview)
        confirmation = self._create(
            WingetUninstallConfirmationTier.PLAN,
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
        plan: WingetUninstallPlan,
        preview: WingetUninstallPreview,
    ) -> WingetUninstallConfirmation:
        """Resolve the first exact gate against its original Preview."""
        return self._resolve(
            confirmation_id,
            approved,
            WingetUninstallConfirmationTier.PLAN,
            plan,
            preview,
        )

    def request_runtime(
        self,
        plan_confirmation_id: UUID,
        plan: WingetUninstallPlan,
        preview: WingetUninstallPreview,
    ) -> WingetUninstallConfirmation:
        """Create a short-lived gate only when fresh invariants reproduce exactly."""
        parent = self._store.get_confirmation(plan_confirmation_id)
        if (
            parent.tier is not WingetUninstallConfirmationTier.PLAN
            or parent.state is not WingetUninstallConfirmationState.APPROVED
        ):
            raise WingetUninstallConfirmationError("approved winget plan confirmation is required")
        self._require_not_expired(parent)
        self._require_executable(plan, preview)
        if parent.invariant_digest != preview.invariant_digest():
            raise WingetUninstallConfirmationError("winget runtime evidence changed")
        confirmation = self._create(
            WingetUninstallConfirmationTier.RUNTIME,
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
        plan: WingetUninstallPlan,
        preview: WingetUninstallPreview,
    ) -> WingetUninstallConfirmation:
        """Resolve the object-specific immediate confirmation."""
        return self._resolve(
            confirmation_id,
            approved,
            WingetUninstallConfirmationTier.RUNTIME,
            plan,
            preview,
        )

    def consume_runtime(
        self,
        confirmation_id: UUID,
        plan: WingetUninstallPlan,
        preview: WingetUninstallPreview,
    ) -> WingetUninstallConfirmation:
        """Atomically consume both approvals at the final execution boundary."""
        runtime = self._store.get_confirmation(confirmation_id)
        if (
            runtime.tier is not WingetUninstallConfirmationTier.RUNTIME
            or runtime.state is not WingetUninstallConfirmationState.APPROVED
            or runtime.parent_confirmation_id is None
        ):
            raise WingetUninstallConfirmationError("winget runtime approval is absent or used")
        self._require_current(runtime, plan, preview)
        self._require_not_expired(runtime)
        parent = self._store.get_confirmation(runtime.parent_confirmation_id)
        if (
            parent.tier is not WingetUninstallConfirmationTier.PLAN
            or parent.state is not WingetUninstallConfirmationState.APPROVED
            or parent.invariant_digest != preview.invariant_digest()
        ):
            raise WingetUninstallConfirmationError("winget plan approval is stale")
        self._require_not_expired(parent)
        self._store.consume_confirmation_pair(parent, runtime)
        return runtime.model_copy(update={"state": WingetUninstallConfirmationState.CONSUMED})

    def _resolve(
        self,
        confirmation_id: UUID,
        approved: bool,
        tier: WingetUninstallConfirmationTier,
        plan: WingetUninstallPlan,
        preview: WingetUninstallPreview,
    ) -> WingetUninstallConfirmation:
        current = self._store.get_confirmation(confirmation_id)
        if (
            current.tier is not tier
            or current.state is not WingetUninstallConfirmationState.PENDING
        ):
            raise WingetUninstallConfirmationError("winget confirmation tier or state is invalid")
        self._require_current(current, plan, preview)
        self._require_not_expired(current)
        resolved = current.model_copy(
            update={
                "state": (
                    WingetUninstallConfirmationState.APPROVED
                    if approved
                    else WingetUninstallConfirmationState.REJECTED
                ),
                "confirmed_at": self._now() if approved else None,
            }
        )
        self._store.resolve_confirmation(resolved)
        return resolved

    def _create(
        self,
        tier: WingetUninstallConfirmationTier,
        plan: WingetUninstallPlan,
        preview: WingetUninstallPreview,
        ttl_seconds: int,
        *,
        parent_confirmation_id: UUID | None = None,
    ) -> WingetUninstallConfirmation:
        executable = preview.availability.executable
        if executable is None:
            raise WingetUninstallConfirmationError("trusted winget executable is absent")
        current = self._now()
        return WingetUninstallConfirmation(
            parent_confirmation_id=parent_confirmation_id,
            tier=tier,
            transaction_id=plan.transaction_id,
            operation_id=plan.operation_id,
            plan_id=plan.plan_id,
            preview_id=preview.preview_id,
            plan_digest=plan.canonical_digest(),
            preview_digest=preview.canonical_digest(),
            invariant_digest=preview.invariant_digest(),
            package_identity_digest=preview.package.identity.canonical_digest(),
            software_identity_digest=preview.software.identity.canonical_digest(),
            mapping_digest=preview.mapping.canonical_digest(),
            executable_identity_digest=executable.invariant_digest(),
            capability_digest=preview.capability.canonical_digest(),
            safety_digest=preview.execution_assessment.canonical_digest(),
            preflight_digest=preview.preflight.canonical_digest(),
            risk_level=plan.risk_level,
            object_summary=(
                f"{preview.software.display_name} {preview.software.display_version or ''} "
                f"({preview.package.package_id}, source=winget, scope=current-user)"
            ).strip(),
            requested_at=current,
            expires_at=current + timedelta(seconds=ttl_seconds),
        )

    def _require_not_expired(self, confirmation: WingetUninstallConfirmation) -> None:
        if self._now() >= confirmation.expires_at:
            expired = confirmation.model_copy(
                update={"state": WingetUninstallConfirmationState.EXPIRED}
            )
            self._store.resolve_confirmation(expired)
            raise WingetUninstallConfirmationError("winget confirmation expired")

    def _require_executable(
        self,
        plan: WingetUninstallPlan,
        preview: WingetUninstallPreview,
    ) -> None:
        if (
            not preview.executable
            or self._now() >= preview.expires_at
            or preview.plan_id != plan.plan_id
            or preview.transaction_id != plan.transaction_id
            or preview.operation_id != plan.operation_id
            or preview.plan_digest != plan.canonical_digest()
            or preview.package.identity.canonical_digest() != plan.package_identity_digest
            or preview.software.identity.canonical_digest() != plan.software_identity_digest
            or preview.execution_assessment.risk_level is not plan.risk_level
        ):
            raise WingetUninstallConfirmationError("winget Preview is blocked or stale")

    def _require_current(
        self,
        confirmation: WingetUninstallConfirmation,
        plan: WingetUninstallPlan,
        preview: WingetUninstallPreview,
    ) -> None:
        self._require_executable(plan, preview)
        executable = preview.availability.executable
        if executable is None:
            raise WingetUninstallConfirmationError("trusted winget executable is absent")
        actual = (
            confirmation.transaction_id,
            confirmation.operation_id,
            confirmation.plan_id,
            confirmation.preview_id,
            confirmation.plan_digest,
            confirmation.preview_digest,
            confirmation.invariant_digest,
            confirmation.package_identity_digest,
            confirmation.software_identity_digest,
            confirmation.mapping_digest,
            confirmation.executable_identity_digest,
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
            preview.package.identity.canonical_digest(),
            preview.software.identity.canonical_digest(),
            preview.mapping.canonical_digest(),
            executable.invariant_digest(),
            preview.capability.canonical_digest(),
            preview.execution_assessment.canonical_digest(),
            preview.preflight.canonical_digest(),
            plan.risk_level,
        )
        if actual != expected:
            raise WingetUninstallConfirmationError("winget confirmation bindings changed")
