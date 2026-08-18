"""Two-tier one-time confirmations for exact service startup configuration writes."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import Field

from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.service_startup_actions import (
    ServiceStartupActionPlan,
    ServiceStartupActionPreview,
    ServiceStartupActionType,
)


class ServiceStartupConfirmationTier(StrEnum):
    """Plan and immediate runtime gates for one persistent configuration write."""

    PLAN = "PLAN"
    RUNTIME = "RUNTIME"


class ServiceStartupConfirmationState(StrEnum):
    """One-time approval lifecycle."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CONSUMED = "CONSUMED"


class ServiceStartupActionConfirmation(FrozenModel):
    """Approval bound to exact identity, source, target, impact, backup, and expiry."""

    confirmation_id: UUID = Field(default_factory=uuid4)
    parent_confirmation_id: UUID | None = None
    tier: ServiceStartupConfirmationTier
    action: ServiceStartupActionType
    transaction_id: UUID
    operation_id: UUID
    plan_id: UUID
    preview_id: UUID
    plan_digest: str = Field(min_length=64, max_length=64)
    preview_digest: str = Field(min_length=64, max_length=64)
    identity_digest: str = Field(min_length=64, max_length=64)
    source_configuration_digest: str = Field(min_length=64, max_length=64)
    target_configuration_digest: str = Field(min_length=64, max_length=64)
    state_digest: str = Field(min_length=64, max_length=64)
    impact_digest: str = Field(min_length=64, max_length=64)
    permission_digest: str = Field(min_length=64, max_length=64)
    backup_id: UUID
    backup_digest: str = Field(min_length=64, max_length=64)
    requested_at: datetime
    confirmed_at: datetime | None = None
    expires_at: datetime
    state: ServiceStartupConfirmationState = ServiceStartupConfirmationState.PENDING


class ServiceStartupConfirmationError(RuntimeError):
    """Raised for missing, stale, expired, wrong-action, or replayed confirmation."""


class ServiceStartupActionConfirmationService:
    """Issue, resolve, and consume plan/runtime approvals exactly once."""

    def __init__(
        self,
        plan_ttl_seconds: int = 300,
        runtime_ttl_seconds: int = 60,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if plan_ttl_seconds <= 0 or runtime_ttl_seconds <= 0:
            raise ValueError("Service startup confirmation TTLs must be positive")
        self._plan_ttl = plan_ttl_seconds
        self._runtime_ttl = runtime_ttl_seconds
        self._now = now or (lambda: datetime.now(UTC))
        self._requests: dict[UUID, ServiceStartupActionConfirmation] = {}

    def request_plan(
        self,
        plan: ServiceStartupActionPlan,
        preview: ServiceStartupActionPreview,
    ) -> ServiceStartupActionConfirmation:
        """Create the first approval only for a matching executable Preview."""
        self._require_executable(plan, preview)
        return self._create(ServiceStartupConfirmationTier.PLAN, plan, preview, self._plan_ttl)

    def resolve_plan(
        self,
        confirmation_id: UUID,
        approved: bool,
        plan: ServiceStartupActionPlan,
        preview: ServiceStartupActionPreview,
    ) -> ServiceStartupActionConfirmation:
        """Approve or reject the first gate after rechecking all immutable bindings."""
        request = self._require_pending(confirmation_id, plan, preview)
        if request.tier is not ServiceStartupConfirmationTier.PLAN:
            raise ServiceStartupConfirmationError("Expected a plan confirmation")
        return self._resolve(request, approved)

    def request_runtime(
        self,
        plan_confirmation_id: UUID,
        plan: ServiceStartupActionPlan,
        preview: ServiceStartupActionPreview,
    ) -> ServiceStartupActionConfirmation:
        """Create a short-lived child approval after fresh execution-time observation."""
        parent = self._requests.get(plan_confirmation_id)
        if parent is None or parent.state is not ServiceStartupConfirmationState.APPROVED:
            raise ServiceStartupConfirmationError("Plan confirmation is not approved")
        if parent.tier is not ServiceStartupConfirmationTier.PLAN:
            raise ServiceStartupConfirmationError("Runtime confirmation parent is invalid")
        self._require_same_binding(parent, plan, preview, allow_fresh_preview=True)
        self._require_executable(plan, preview)
        return self._create(
            ServiceStartupConfirmationTier.RUNTIME,
            plan,
            preview,
            self._runtime_ttl,
            parent_confirmation_id=parent.confirmation_id,
        )

    def resolve_runtime(
        self,
        confirmation_id: UUID,
        approved: bool,
        plan: ServiceStartupActionPlan,
        preview: ServiceStartupActionPreview,
    ) -> ServiceStartupActionConfirmation:
        """Approve or reject the immediate gate for the fresh runtime Preview."""
        request = self._require_pending(confirmation_id, plan, preview)
        if request.tier is not ServiceStartupConfirmationTier.RUNTIME:
            raise ServiceStartupConfirmationError("Expected a runtime confirmation")
        return self._resolve(request, approved)

    def consume_runtime(
        self,
        confirmation_id: UUID,
        plan: ServiceStartupActionPlan,
        preview: ServiceStartupActionPreview,
    ) -> ServiceStartupActionConfirmation:
        """Consume the approved immediate gate exactly once before registry execution."""
        request = self._requests.get(confirmation_id)
        if request is None:
            raise ServiceStartupConfirmationError("Unknown runtime confirmation")
        if request.state is ServiceStartupConfirmationState.CONSUMED:
            raise ServiceStartupConfirmationError("Runtime confirmation was already consumed")
        if request.state is not ServiceStartupConfirmationState.APPROVED:
            raise ServiceStartupConfirmationError("Runtime confirmation is not approved")
        if request.tier is not ServiceStartupConfirmationTier.RUNTIME:
            raise ServiceStartupConfirmationError("Only runtime confirmation can execute")
        self._require_unexpired(request)
        self._require_same_binding(request, plan, preview)
        consumed = request.model_copy(update={"state": ServiceStartupConfirmationState.CONSUMED})
        self._requests[confirmation_id] = consumed
        return consumed

    def _create(
        self,
        tier: ServiceStartupConfirmationTier,
        plan: ServiceStartupActionPlan,
        preview: ServiceStartupActionPreview,
        ttl_seconds: int,
        *,
        parent_confirmation_id: UUID | None = None,
    ) -> ServiceStartupActionConfirmation:
        now = self._now()
        request = ServiceStartupActionConfirmation(
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
            source_configuration_digest=plan.source_configuration.canonical_digest(),
            target_configuration_digest=plan.target_configuration.canonical_digest(),
            state_digest=preview.current_state_digest,
            impact_digest=preview.impact.canonical_digest(),
            permission_digest=preview.permissions.canonical_digest(),
            backup_id=plan.backup_id,
            backup_digest=plan.backup_digest,
            requested_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
        self._requests[request.confirmation_id] = request
        return request

    def _require_pending(
        self,
        confirmation_id: UUID,
        plan: ServiceStartupActionPlan,
        preview: ServiceStartupActionPreview,
    ) -> ServiceStartupActionConfirmation:
        request = self._requests.get(confirmation_id)
        if request is None:
            raise ServiceStartupConfirmationError("Unknown service startup confirmation")
        if request.state is not ServiceStartupConfirmationState.PENDING:
            raise ServiceStartupConfirmationError("Confirmation is no longer pending")
        self._require_unexpired(request)
        self._require_same_binding(request, plan, preview)
        return request

    def _require_unexpired(self, request: ServiceStartupActionConfirmation) -> None:
        if self._now() >= request.expires_at:
            expired = request.model_copy(update={"state": ServiceStartupConfirmationState.EXPIRED})
            self._requests[request.confirmation_id] = expired
            raise ServiceStartupConfirmationError("Service startup confirmation expired")

    @staticmethod
    def _require_executable(
        plan: ServiceStartupActionPlan,
        preview: ServiceStartupActionPreview,
    ) -> None:
        if preview.plan_digest != plan.canonical_digest() or not preview.executable:
            raise ServiceStartupConfirmationError("Only an executable matching Preview can confirm")

    @staticmethod
    def _require_same_binding(
        request: ServiceStartupActionConfirmation,
        plan: ServiceStartupActionPlan,
        preview: ServiceStartupActionPreview,
        *,
        allow_fresh_preview: bool = False,
    ) -> None:
        if (
            request.action is not plan.action
            or request.transaction_id != plan.transaction_id
            or request.operation_id != plan.operation_id
            or request.plan_id != plan.plan_id
            or request.plan_digest != plan.canonical_digest()
            or request.identity_digest != plan.target_identity.canonical_digest()
            or request.source_configuration_digest != plan.source_configuration.canonical_digest()
            or request.target_configuration_digest != plan.target_configuration.canonical_digest()
            or request.backup_id != plan.backup_id
            or request.backup_digest != plan.backup_digest
        ):
            raise ServiceStartupConfirmationError("Plan or configuration binding changed")
        if not allow_fresh_preview and (
            request.preview_id != preview.preview_id
            or request.preview_digest != preview.canonical_digest()
        ):
            raise ServiceStartupConfirmationError("Preview binding changed")
        if (
            request.state_digest != preview.current_state_digest
            or request.impact_digest != preview.impact.canonical_digest()
            or request.permission_digest != preview.permissions.canonical_digest()
        ):
            raise ServiceStartupConfirmationError("Observed service evidence changed")

    def _resolve(
        self,
        request: ServiceStartupActionConfirmation,
        approved: bool,
    ) -> ServiceStartupActionConfirmation:
        resolved = request.model_copy(
            update={
                "state": (
                    ServiceStartupConfirmationState.APPROVED
                    if approved
                    else ServiceStartupConfirmationState.REJECTED
                ),
                "confirmed_at": self._now(),
            }
        )
        self._requests[request.confirmation_id] = resolved
        return resolved
