"""Two-tier one-time confirmations for exact service actions."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import Field

from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.service_actions import (
    ServiceActionPlan,
    ServiceActionPreview,
    ServiceActionType,
)


class ServiceConfirmationTier(StrEnum):
    """Plan and immediate runtime gates."""

    PLAN = "PLAN"
    RUNTIME = "RUNTIME"


class ServiceConfirmationState(StrEnum):
    """One-time service confirmation lifecycle."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CONSUMED = "CONSUMED"


class ServiceActionConfirmation(FrozenModel):
    """Approval bound to plan, Preview, identity, state, graph, permissions, and expiry."""

    confirmation_id: UUID = Field(default_factory=uuid4)
    parent_confirmation_id: UUID | None = None
    tier: ServiceConfirmationTier
    action: ServiceActionType
    transaction_id: UUID
    operation_id: UUID
    plan_id: UUID
    preview_id: UUID
    plan_digest: str = Field(min_length=64, max_length=64)
    preview_digest: str = Field(min_length=64, max_length=64)
    identity_digest: str = Field(min_length=64, max_length=64)
    state_digest: str = Field(min_length=64, max_length=64)
    dependency_digest: str = Field(min_length=64, max_length=64)
    permission_digest: str = Field(min_length=64, max_length=64)
    requested_at: datetime
    confirmed_at: datetime | None = None
    expires_at: datetime
    state: ServiceConfirmationState = ServiceConfirmationState.PENDING


class ServiceConfirmationError(RuntimeError):
    """Raised for missing, stale, expired, wrong-action, or replayed approval."""


class ServiceActionConfirmationService:
    """Issue, resolve, and consume exact service confirmations in memory."""

    def __init__(
        self,
        plan_ttl_seconds: int = 300,
        runtime_ttl_seconds: int = 60,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if plan_ttl_seconds <= 0 or runtime_ttl_seconds <= 0:
            raise ValueError("Service confirmation TTLs must be positive")
        self._plan_ttl = plan_ttl_seconds
        self._runtime_ttl = runtime_ttl_seconds
        self._now = now or (lambda: datetime.now(UTC))
        self._requests: dict[UUID, ServiceActionConfirmation] = {}

    def request_plan(
        self, plan: ServiceActionPlan, preview: ServiceActionPreview
    ) -> ServiceActionConfirmation:
        """Create a plan approval only for an executable exact Preview."""
        self._require_executable(plan, preview)
        return self._create(ServiceConfirmationTier.PLAN, plan, preview, self._plan_ttl)

    def resolve_plan(
        self,
        confirmation_id: UUID,
        approved: bool,
        plan: ServiceActionPlan,
        preview: ServiceActionPreview,
    ) -> ServiceActionConfirmation:
        """Resolve one pending plan approval."""
        return self._resolve(confirmation_id, approved, ServiceConfirmationTier.PLAN, plan, preview)

    def request_runtime(
        self,
        plan_confirmation_id: UUID,
        plan: ServiceActionPlan,
        preview: ServiceActionPreview,
    ) -> ServiceActionConfirmation:
        """Issue a short-lived gate after a fresh equivalent Preview."""
        parent = self._get(plan_confirmation_id)
        if (
            parent.tier is not ServiceConfirmationTier.PLAN
            or parent.state is not ServiceConfirmationState.APPROVED
            or parent.action is not plan.action
        ):
            raise ServiceConfirmationError("Approved same-action plan confirmation is required")
        self._require_not_expired(parent)
        self._require_current(parent, plan, preview, allow_new_preview=True)
        return self._create(
            ServiceConfirmationTier.RUNTIME,
            plan,
            preview,
            self._runtime_ttl,
            parent_confirmation_id=parent.confirmation_id,
        )

    def resolve_runtime(
        self,
        confirmation_id: UUID,
        approved: bool,
        plan: ServiceActionPlan,
        preview: ServiceActionPreview,
    ) -> ServiceActionConfirmation:
        """Resolve the immediate object-specific approval."""
        return self._resolve(
            confirmation_id, approved, ServiceConfirmationTier.RUNTIME, plan, preview
        )

    def consume_runtime(
        self,
        confirmation_id: UUID,
        plan: ServiceActionPlan,
        preview: ServiceActionPreview,
    ) -> ServiceActionConfirmation:
        """Consume runtime and parent approvals at the write boundary."""
        request = self._get(confirmation_id)
        if (
            request.tier is not ServiceConfirmationTier.RUNTIME
            or request.state is not ServiceConfirmationState.APPROVED
            or request.action is not plan.action
        ):
            raise ServiceConfirmationError("Runtime service confirmation is absent or consumed")
        self._require_current(request, plan, preview)
        self._require_not_expired(request)
        parent = self._get(request.parent_confirmation_id)
        if (
            parent.tier is not ServiceConfirmationTier.PLAN
            or parent.state is not ServiceConfirmationState.APPROVED
            or parent.action is not request.action
        ):
            raise ServiceConfirmationError("Plan service confirmation is no longer valid")
        self._require_not_expired(parent)
        self._requests[parent.confirmation_id] = parent.model_copy(
            update={"state": ServiceConfirmationState.CONSUMED}
        )
        consumed = request.model_copy(update={"state": ServiceConfirmationState.CONSUMED})
        self._requests[request.confirmation_id] = consumed
        return consumed

    def _create(
        self,
        tier: ServiceConfirmationTier,
        plan: ServiceActionPlan,
        preview: ServiceActionPreview,
        ttl_seconds: int,
        *,
        parent_confirmation_id: UUID | None = None,
    ) -> ServiceActionConfirmation:
        now = self._now()
        value = ServiceActionConfirmation(
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
            state_digest=preview.current_state_digest,
            dependency_digest=preview.dependencies.graph_digest,
            permission_digest=preview.permissions.canonical_digest(),
            requested_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
        self._requests[value.confirmation_id] = value
        return value

    def _resolve(
        self,
        confirmation_id: UUID,
        approved: bool,
        tier: ServiceConfirmationTier,
        plan: ServiceActionPlan,
        preview: ServiceActionPreview,
    ) -> ServiceActionConfirmation:
        request = self._get(confirmation_id)
        if request.tier is not tier or request.state is not ServiceConfirmationState.PENDING:
            raise ServiceConfirmationError("Service confirmation is wrong or already resolved")
        self._require_current(request, plan, preview)
        self._require_not_expired(request)
        resolved = request.model_copy(
            update={
                "state": (
                    ServiceConfirmationState.APPROVED
                    if approved
                    else ServiceConfirmationState.REJECTED
                ),
                "confirmed_at": self._now() if approved else None,
            }
        )
        self._requests[confirmation_id] = resolved
        return resolved

    def _get(self, confirmation_id: UUID | None) -> ServiceActionConfirmation:
        if confirmation_id is None:
            raise ServiceConfirmationError("Missing service confirmation")
        try:
            return self._requests[confirmation_id]
        except KeyError as exc:
            raise ServiceConfirmationError("Unknown service confirmation") from exc

    def _require_not_expired(self, request: ServiceActionConfirmation) -> None:
        if self._now() >= request.expires_at:
            self._requests[request.confirmation_id] = request.model_copy(
                update={"state": ServiceConfirmationState.EXPIRED}
            )
            raise ServiceConfirmationError("Service confirmation expired")

    @staticmethod
    def _require_executable(plan: ServiceActionPlan, preview: ServiceActionPreview) -> None:
        if (
            not preview.executable
            or preview.plan_id != plan.plan_id
            or preview.transaction_id != plan.transaction_id
            or preview.plan_digest != plan.canonical_digest()
            or preview.action is not plan.action
            or preview.observation.identity.canonical_digest()
            != plan.target_identity.canonical_digest()
        ):
            raise ServiceConfirmationError("Service Preview is blocked, stale, or mismatched")

    @classmethod
    def _require_current(
        cls,
        request: ServiceActionConfirmation,
        plan: ServiceActionPlan,
        preview: ServiceActionPreview,
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
            request.state_digest,
            request.dependency_digest,
            request.permission_digest,
        )
        expected = (
            plan.action,
            plan.transaction_id,
            plan.operation_id,
            plan.plan_id,
            plan.canonical_digest(),
            plan.target_identity.canonical_digest(),
            preview.current_state_digest,
            preview.dependencies.graph_digest,
            preview.permissions.canonical_digest(),
        )
        if actual != expected:
            raise ServiceConfirmationError("Service confirmation bindings changed")
        if not allow_new_preview and (
            request.preview_id != preview.preview_id
            or request.preview_digest != preview.canonical_digest()
        ):
            raise ServiceConfirmationError("Service Preview changed after confirmation")
