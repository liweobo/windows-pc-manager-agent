"""Two-tier one-time confirmations for exact backed-up startup actions."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import Field

from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.startup_actions import (
    StartupActionPlan,
    StartupActionPreview,
    StartupActionType,
)


class StartupConfirmationTier(StrEnum):
    """Plan and immediate runtime gates for one startup configuration change."""

    PLAN = "PLAN"
    RUNTIME = "RUNTIME"


class StartupConfirmationState(StrEnum):
    """One-time confirmation lifecycle."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CONSUMED = "CONSUMED"


class StartupActionConfirmation(FrozenModel):
    """Approval bound to exact plan, Preview, identity, backup, action, and expiry."""

    confirmation_id: UUID = Field(default_factory=uuid4)
    parent_confirmation_id: UUID | None = None
    tier: StartupConfirmationTier
    action: StartupActionType
    transaction_id: UUID
    operation_id: UUID
    plan_id: UUID
    preview_id: UUID
    plan_digest: str = Field(min_length=64, max_length=64)
    preview_digest: str = Field(min_length=64, max_length=64)
    identity_digest: str = Field(min_length=64, max_length=64)
    current_state_digest: str = Field(min_length=64, max_length=64)
    backup_id: UUID
    backup_digest: str = Field(min_length=64, max_length=64)
    requested_at: datetime
    confirmed_at: datetime | None = None
    expires_at: datetime
    state: StartupConfirmationState = StartupConfirmationState.PENDING


class StartupConfirmationError(RuntimeError):
    """Raised for missing, stale, expired, wrong-action, or replayed approval."""


class StartupActionConfirmationService:
    """Issue, resolve, and consume plan/runtime startup approvals exactly once."""

    def __init__(
        self,
        plan_ttl_seconds: int = 300,
        runtime_ttl_seconds: int = 60,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if plan_ttl_seconds <= 0 or runtime_ttl_seconds <= 0:
            raise ValueError("Startup confirmation TTLs must be positive")
        self._plan_ttl = plan_ttl_seconds
        self._runtime_ttl = runtime_ttl_seconds
        self._now = now or (lambda: datetime.now(UTC))
        self._requests: dict[UUID, StartupActionConfirmation] = {}

    def request_plan(
        self,
        plan: StartupActionPlan,
        preview: StartupActionPreview,
    ) -> StartupActionConfirmation:
        """Create the first approval only for a matching executable Preview."""
        self._require_executable(plan, preview)
        return self._create(StartupConfirmationTier.PLAN, plan, preview, self._plan_ttl)

    def resolve_plan(
        self,
        confirmation_id: UUID,
        approved: bool,
        plan: StartupActionPlan,
        preview: StartupActionPreview,
    ) -> StartupActionConfirmation:
        """Resolve one pending unexpired plan confirmation."""
        return self._resolve(
            confirmation_id,
            approved,
            StartupConfirmationTier.PLAN,
            plan,
            preview,
        )

    def request_runtime(
        self,
        plan_confirmation_id: UUID,
        plan: StartupActionPlan,
        preview: StartupActionPreview,
    ) -> StartupActionConfirmation:
        """Issue immediate confirmation only after live state and backup revalidation."""
        parent = self._get(plan_confirmation_id)
        if (
            parent.tier is not StartupConfirmationTier.PLAN
            or parent.state is not StartupConfirmationState.APPROVED
            or parent.action is not plan.action
        ):
            raise StartupConfirmationError("Approved same-action plan confirmation is required")
        self._require_not_expired(parent)
        self._require_current(parent, plan, preview, allow_new_preview=True)
        return self._create(
            StartupConfirmationTier.RUNTIME,
            plan,
            preview,
            self._runtime_ttl,
            parent_confirmation_id=parent.confirmation_id,
        )

    def resolve_runtime(
        self,
        confirmation_id: UUID,
        approved: bool,
        plan: StartupActionPlan,
        preview: StartupActionPreview,
    ) -> StartupActionConfirmation:
        """Resolve one immediate object-specific startup approval."""
        return self._resolve(
            confirmation_id,
            approved,
            StartupConfirmationTier.RUNTIME,
            plan,
            preview,
        )

    def consume_runtime(
        self,
        confirmation_id: UUID,
        plan: StartupActionPlan,
        preview: StartupActionPreview,
    ) -> StartupActionConfirmation:
        """Consume the runtime and parent plan approvals at the write boundary."""
        request = self._get(confirmation_id)
        if (
            request.tier is not StartupConfirmationTier.RUNTIME
            or request.state is not StartupConfirmationState.APPROVED
            or request.action is not plan.action
        ):
            raise StartupConfirmationError("Runtime startup confirmation is absent or consumed")
        self._require_current(request, plan, preview)
        self._require_not_expired(request)
        parent = self._get(request.parent_confirmation_id)
        if (
            parent.tier is not StartupConfirmationTier.PLAN
            or parent.state is not StartupConfirmationState.APPROVED
            or parent.action is not request.action
        ):
            raise StartupConfirmationError("Plan startup confirmation is no longer valid")
        self._require_not_expired(parent)
        self._requests[parent.confirmation_id] = parent.model_copy(
            update={"state": StartupConfirmationState.CONSUMED}
        )
        consumed = request.model_copy(update={"state": StartupConfirmationState.CONSUMED})
        self._requests[request.confirmation_id] = consumed
        return consumed

    def _create(
        self,
        tier: StartupConfirmationTier,
        plan: StartupActionPlan,
        preview: StartupActionPreview,
        ttl_seconds: int,
        *,
        parent_confirmation_id: UUID | None = None,
    ) -> StartupActionConfirmation:
        current = self._now()
        request = StartupActionConfirmation(
            parent_confirmation_id=parent_confirmation_id,
            tier=tier,
            action=plan.action,
            transaction_id=plan.transaction_id,
            operation_id=plan.operation_id,
            plan_id=plan.plan_id,
            preview_id=preview.preview_id,
            plan_digest=plan.canonical_digest(),
            preview_digest=preview.canonical_digest(),
            identity_digest=plan.target_identity.canonical_digest(),
            current_state_digest=preview.current_state_digest,
            backup_id=preview.backup_id,
            backup_digest=preview.backup_digest,
            requested_at=current,
            expires_at=current + timedelta(seconds=ttl_seconds),
        )
        self._requests[request.confirmation_id] = request
        return request

    def _resolve(
        self,
        confirmation_id: UUID,
        approved: bool,
        tier: StartupConfirmationTier,
        plan: StartupActionPlan,
        preview: StartupActionPreview,
    ) -> StartupActionConfirmation:
        request = self._get(confirmation_id)
        if request.tier is not tier or request.state is not StartupConfirmationState.PENDING:
            raise StartupConfirmationError("Startup confirmation is wrong or already resolved")
        self._require_current(request, plan, preview)
        self._require_not_expired(request)
        resolved = request.model_copy(
            update={
                "state": (
                    StartupConfirmationState.APPROVED
                    if approved
                    else StartupConfirmationState.REJECTED
                ),
                "confirmed_at": self._now() if approved else None,
            }
        )
        self._requests[confirmation_id] = resolved
        return resolved

    def _get(self, confirmation_id: UUID | None) -> StartupActionConfirmation:
        if confirmation_id is None:
            raise StartupConfirmationError("Missing startup confirmation")
        try:
            return self._requests[confirmation_id]
        except KeyError as exc:
            raise StartupConfirmationError("Unknown startup confirmation") from exc

    def _require_not_expired(self, request: StartupActionConfirmation) -> None:
        if self._now() >= request.expires_at:
            self._requests[request.confirmation_id] = request.model_copy(
                update={"state": StartupConfirmationState.EXPIRED}
            )
            raise StartupConfirmationError("Startup confirmation expired")

    @staticmethod
    def _require_executable(
        plan: StartupActionPlan,
        preview: StartupActionPreview,
    ) -> None:
        if (
            not preview.executable
            or preview.plan_id != plan.plan_id
            or preview.transaction_id != plan.transaction_id
            or preview.plan_digest != plan.canonical_digest()
            or preview.action is not plan.action
            or preview.observation.identity.canonical_digest()
            != plan.target_identity.canonical_digest()
            or preview.backup_id != plan.backup_id
            or preview.backup_digest != plan.backup_digest
        ):
            raise StartupConfirmationError("Startup Preview is blocked, stale, or mismatched")

    @classmethod
    def _require_current(
        cls,
        request: StartupActionConfirmation,
        plan: StartupActionPlan,
        preview: StartupActionPreview,
        *,
        allow_new_preview: bool = False,
    ) -> None:
        cls._require_executable(plan, preview)
        actual = (
            request.action,
            request.transaction_id,
            request.operation_id,
            request.plan_id,
            request.plan_digest,
            request.identity_digest,
            request.backup_id,
            request.backup_digest,
        )
        expected = (
            plan.action,
            plan.transaction_id,
            plan.operation_id,
            plan.plan_id,
            plan.canonical_digest(),
            plan.target_identity.canonical_digest(),
            plan.backup_id,
            plan.backup_digest,
        )
        if actual != expected:
            raise StartupConfirmationError("Startup confirmation bindings changed")
        if not allow_new_preview and (
            request.preview_id != preview.preview_id
            or request.preview_digest != preview.canonical_digest()
            or request.current_state_digest != preview.current_state_digest
        ):
            raise StartupConfirmationError("Startup Preview changed after confirmation")
