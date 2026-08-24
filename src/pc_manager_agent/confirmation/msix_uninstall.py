"""Two-tier digest-bound confirmations for one exact MSIX package removal."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import Field

from pc_manager_agent.domain.msix_uninstall import MsixUninstallPlan, MsixUninstallPreview
from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.risk import RiskLevel


class MsixConfirmationTier(StrEnum):
    """Plan and object-specific immediate approval tiers."""

    PLAN = "plan"
    RUNTIME = "runtime"


class MsixConfirmationState(StrEnum):
    """Durable single-use confirmation lifecycle."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CONSUMED = "consumed"


class MsixUninstallConfirmation(FrozenModel):
    """Approval bound to exact identity, dependency, policy, preflight, and data impact."""

    confirmation_id: UUID = Field(default_factory=uuid4)
    parent_confirmation_id: UUID | None = None
    tier: MsixConfirmationTier
    transaction_id: UUID
    operation_id: UUID
    plan_id: UUID
    preview_id: UUID
    plan_digest: str = Field(min_length=64, max_length=64)
    preview_digest: str = Field(min_length=64, max_length=64)
    invariant_digest: str = Field(min_length=64, max_length=64)
    package_identity_digest: str = Field(min_length=64, max_length=64)
    dependency_digest: str = Field(min_length=64, max_length=64)
    assessment_digest: str = Field(min_length=64, max_length=64)
    preflight_digest: str = Field(min_length=64, max_length=64)
    data_impact_digest: str = Field(min_length=64, max_length=64)
    risk_level: RiskLevel
    object_summary: str = Field(min_length=1, max_length=1_500)
    requested_at: datetime
    confirmed_at: datetime | None = None
    expires_at: datetime
    state: MsixConfirmationState = MsixConfirmationState.PENDING


class MsixConfirmationStore(Protocol):
    """Persistence operations required for atomic two-tier approval."""

    def save_confirmation(self, confirmation: MsixUninstallConfirmation) -> None:
        """Persist one pending gate."""
        ...

    def get_confirmation(self, confirmation_id: UUID) -> MsixUninstallConfirmation:
        """Load one gate."""
        ...

    def update_confirmation(self, confirmation: MsixUninstallConfirmation) -> None:
        """Persist a state transition."""
        ...

    def consume_pair(
        self,
        plan_confirmation: MsixUninstallConfirmation,
        runtime_confirmation: MsixUninstallConfirmation,
    ) -> None:
        """Consume both approvals and reserve dispatch atomically."""
        ...


class MsixConfirmationError(RuntimeError):
    """Raised for absent, stale, expired, mismatched, or replayed approval."""


class MsixConfirmationService:
    """Issue and consume plan plus short-lived immediate confirmations."""

    def __init__(
        self,
        store: MsixConfirmationStore,
        *,
        plan_ttl_seconds: int = 300,
        runtime_ttl_seconds: int = 60,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if plan_ttl_seconds <= 0 or runtime_ttl_seconds <= 0:
            raise ValueError("MSIX confirmation TTLs must be positive")
        self._store = store
        self._plan_ttl = plan_ttl_seconds
        self._runtime_ttl = runtime_ttl_seconds
        self._now = now or (lambda: datetime.now(UTC))

    def request_plan(
        self, plan: MsixUninstallPlan, preview: MsixUninstallPreview
    ) -> MsixUninstallConfirmation:
        """Create the first gate for one current executable Preview."""
        self._require_current(plan, preview)
        confirmation = self._create(MsixConfirmationTier.PLAN, plan, preview, self._plan_ttl)
        self._store.save_confirmation(confirmation)
        return confirmation

    def approve(
        self,
        confirmation_id: UUID,
        approved: bool,
        plan: MsixUninstallPlan,
        preview: MsixUninstallPreview,
    ) -> MsixUninstallConfirmation:
        """Resolve one exact pending gate; all bindings are checked again."""
        current = self._store.get_confirmation(confirmation_id)
        if current.state is not MsixConfirmationState.PENDING:
            raise MsixConfirmationError("MSIX confirmation is absent or already resolved")
        self._require_not_expired(current)
        self._require_binding(current, plan, preview)
        resolved = current.model_copy(
            update={
                "state": (
                    MsixConfirmationState.APPROVED if approved else MsixConfirmationState.REJECTED
                ),
                "confirmed_at": self._now() if approved else None,
            }
        )
        self._store.update_confirmation(resolved)
        return resolved

    def request_runtime(
        self,
        plan_confirmation_id: UUID,
        plan: MsixUninstallPlan,
        preview: MsixUninstallPreview,
    ) -> MsixUninstallConfirmation:
        """Create immediate approval only from a current approved plan gate."""
        parent = self._store.get_confirmation(plan_confirmation_id)
        if (
            parent.tier is not MsixConfirmationTier.PLAN
            or parent.state is not MsixConfirmationState.APPROVED
        ):
            raise MsixConfirmationError("approved MSIX plan confirmation is required")
        self._require_not_expired(parent)
        self._require_binding(parent, plan, preview)
        confirmation = self._create(
            MsixConfirmationTier.RUNTIME,
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
        plan: MsixUninstallPlan,
        preview: MsixUninstallPreview,
    ) -> MsixUninstallConfirmation:
        """Consume both gates once at the final execution boundary."""
        runtime = self._store.get_confirmation(runtime_confirmation_id)
        if (
            runtime.tier is not MsixConfirmationTier.RUNTIME
            or runtime.state is not MsixConfirmationState.APPROVED
            or runtime.parent_confirmation_id is None
        ):
            raise MsixConfirmationError("MSIX immediate confirmation is absent or used")
        parent = self._store.get_confirmation(runtime.parent_confirmation_id)
        for confirmation in (parent, runtime):
            self._require_not_expired(confirmation)
            self._require_binding(confirmation, plan, preview)
        if (
            parent.tier is not MsixConfirmationTier.PLAN
            or parent.state is not MsixConfirmationState.APPROVED
        ):
            raise MsixConfirmationError("MSIX plan confirmation is stale")
        self._store.consume_pair(parent, runtime)
        return runtime.model_copy(update={"state": MsixConfirmationState.CONSUMED})

    def _create(
        self,
        tier: MsixConfirmationTier,
        plan: MsixUninstallPlan,
        preview: MsixUninstallPreview,
        ttl_seconds: int,
        *,
        parent_confirmation_id: UUID | None = None,
    ) -> MsixUninstallConfirmation:
        current = self._now()
        identity = preview.package.identity
        impact = preview.data_impact
        return MsixUninstallConfirmation(
            parent_confirmation_id=parent_confirmation_id,
            tier=tier,
            transaction_id=plan.transaction_id,
            operation_id=plan.operation_id,
            plan_id=plan.plan_id,
            preview_id=preview.preview_id,
            plan_digest=plan.canonical_digest(),
            preview_digest=preview.canonical_digest(),
            invariant_digest=preview.invariant_digest(),
            package_identity_digest=identity.canonical_digest(),
            dependency_digest=preview.dependencies.canonical_digest(),
            assessment_digest=preview.assessment.canonical_digest(),
            preflight_digest=preview.preflight.canonical_digest(),
            data_impact_digest=_impact_digest(preview),
            risk_level=plan.risk_level,
            object_summary=(
                f"{preview.package.display_name} {identity.instance.version}; "
                f"PFN={identity.instance.full_name}; scope=current-user; "
                f"Roamable preserved={impact.roamable_data_preserved}; "
                f"LocalState may be removed={impact.local_state_may_be_removed}; "
                "Windows orphan dependency removal possible; Agent performs no extra data deletion"
            ),
            requested_at=current,
            expires_at=current + timedelta(seconds=ttl_seconds),
        )

    def _require_current(self, plan: MsixUninstallPlan, preview: MsixUninstallPreview) -> None:
        if (
            not preview.executable
            or self._now() >= preview.expires_at
            or preview.plan_id != plan.plan_id
            or preview.transaction_id != plan.transaction_id
            or preview.operation_id != plan.operation_id
            or preview.plan_digest != plan.canonical_digest()
        ):
            raise MsixConfirmationError("MSIX Preview is blocked or stale")

    def _require_binding(
        self,
        confirmation: MsixUninstallConfirmation,
        plan: MsixUninstallPlan,
        preview: MsixUninstallPreview,
    ) -> None:
        self._require_current(plan, preview)
        actual = (
            confirmation.transaction_id,
            confirmation.operation_id,
            confirmation.plan_id,
            confirmation.preview_id,
            confirmation.plan_digest,
            confirmation.preview_digest,
            confirmation.invariant_digest,
            confirmation.package_identity_digest,
            confirmation.dependency_digest,
            confirmation.assessment_digest,
            confirmation.preflight_digest,
            confirmation.data_impact_digest,
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
            preview.dependencies.canonical_digest(),
            preview.assessment.canonical_digest(),
            preview.preflight.canonical_digest(),
            _impact_digest(preview),
            plan.risk_level,
        )
        if actual != expected:
            raise MsixConfirmationError("MSIX confirmation bindings changed")

    def _require_not_expired(self, confirmation: MsixUninstallConfirmation) -> None:
        if self._now() >= confirmation.expires_at:
            expired = confirmation.model_copy(update={"state": MsixConfirmationState.EXPIRED})
            self._store.update_confirmation(expired)
            raise MsixConfirmationError("MSIX confirmation expired")


def _impact_digest(preview: MsixUninstallPreview) -> str:
    """Hash the exact Windows data-removal semantics shown to the user."""
    from pc_manager_agent.domain.software_uninstall_analysis import canonical_digest

    return canonical_digest(preview.data_impact.model_dump(mode="json"))
