"""Two-tier one-time confirmations for each exact process action."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import Field

from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.process_actions import (
    ProcessActionPlan,
    ProcessActionPreview,
    ProcessActionType,
)


class ProcessConfirmationTier(StrEnum):
    """Plan and immediate runtime gates required for one action."""

    PLAN = "PLAN"
    RUNTIME = "RUNTIME"


class ProcessConfirmationState(StrEnum):
    """Lifecycle that makes confirmation replay impossible."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CONSUMED = "CONSUMED"


class ProcessActionConfirmation(FrozenModel):
    """Authorization bound to an action, Preview, identity set, and expiry."""

    confirmation_id: UUID = Field(default_factory=uuid4)
    parent_confirmation_id: UUID | None = None
    tier: ProcessConfirmationTier
    action: ProcessActionType
    transaction_id: UUID
    operation_id: UUID
    plan_id: UUID
    preview_id: UUID
    plan_digest: str = Field(min_length=64, max_length=64)
    preview_digest: str = Field(min_length=64, max_length=64)
    target_set_digest: str = Field(min_length=64, max_length=64)
    process_count: int = Field(ge=1, le=20)
    requested_at: datetime
    confirmed_at: datetime | None = None
    expires_at: datetime
    state: ProcessConfirmationState = ProcessConfirmationState.PENDING


class ProcessConfirmationError(RuntimeError):
    """Raised for missing, stale, expired, wrong-action, or replayed confirmation."""


class ProcessActionConfirmationService:
    """Issue, resolve, and consume plan/runtime approvals for one exact action."""

    def __init__(
        self,
        plan_ttl_seconds: int = 300,
        runtime_ttl_seconds: int = 60,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if plan_ttl_seconds <= 0 or runtime_ttl_seconds <= 0:
            raise ValueError("Process confirmation TTLs must be positive")
        self._plan_ttl = plan_ttl_seconds
        self._runtime_ttl = runtime_ttl_seconds
        self._now = now or (lambda: datetime.now(UTC))
        self._requests: dict[UUID, ProcessActionConfirmation] = {}

    def request_plan(
        self,
        plan: ProcessActionPlan,
        preview: ProcessActionPreview,
    ) -> ProcessActionConfirmation:
        """Create the first approval only for a matching executable Preview."""
        self._require_executable(plan, preview)
        return self._create(ProcessConfirmationTier.PLAN, plan, preview, self._plan_ttl)

    def resolve_plan(
        self,
        confirmation_id: UUID,
        approved: bool,
        plan: ProcessActionPlan,
        preview: ProcessActionPreview,
    ) -> ProcessActionConfirmation:
        """Resolve a pending unexpired plan confirmation."""
        return self._resolve(confirmation_id, approved, ProcessConfirmationTier.PLAN, plan, preview)

    def request_runtime(
        self,
        plan_confirmation_id: UUID,
        plan: ProcessActionPlan,
        revalidated_preview: ProcessActionPreview,
    ) -> ProcessActionConfirmation:
        """Issue the immediate confirmation after fresh identity revalidation."""
        parent = self._get(plan_confirmation_id)
        if (
            parent.tier is not ProcessConfirmationTier.PLAN
            or parent.state is not ProcessConfirmationState.APPROVED
            or parent.action is not plan.action
        ):
            raise ProcessConfirmationError("An approved same-action plan confirmation is required")
        self._require_not_expired(parent)
        self._require_current(parent, plan, revalidated_preview, allow_new_preview_id=True)
        return self._create(
            ProcessConfirmationTier.RUNTIME,
            plan,
            revalidated_preview,
            self._runtime_ttl,
            parent_confirmation_id=parent.confirmation_id,
        )

    def resolve_runtime(
        self,
        confirmation_id: UUID,
        approved: bool,
        plan: ProcessActionPlan,
        preview: ProcessActionPreview,
    ) -> ProcessActionConfirmation:
        """Resolve the immediate object-specific approval."""
        return self._resolve(
            confirmation_id, approved, ProcessConfirmationTier.RUNTIME, plan, preview
        )

    def consume_runtime(
        self,
        confirmation_id: UUID,
        plan: ProcessActionPlan,
        preview: ProcessActionPreview,
    ) -> ProcessActionConfirmation:
        """Consume both approvals exactly once at the execution boundary."""
        request = self._get(confirmation_id)
        if (
            request.tier is not ProcessConfirmationTier.RUNTIME
            or request.state is not ProcessConfirmationState.APPROVED
            or request.action is not plan.action
        ):
            raise ProcessConfirmationError("Runtime confirmation is wrong, absent, or consumed")
        self._require_current(request, plan, preview)
        self._require_not_expired(request)
        parent = self._get(request.parent_confirmation_id)
        if (
            parent.tier is not ProcessConfirmationTier.PLAN
            or parent.state is not ProcessConfirmationState.APPROVED
            or parent.action is not request.action
        ):
            raise ProcessConfirmationError("Plan confirmation is no longer valid")
        self._require_not_expired(parent)
        self._requests[parent.confirmation_id] = parent.model_copy(
            update={"state": ProcessConfirmationState.CONSUMED}
        )
        consumed = request.model_copy(update={"state": ProcessConfirmationState.CONSUMED})
        self._requests[confirmation_id] = consumed
        return consumed

    def _create(
        self,
        tier: ProcessConfirmationTier,
        plan: ProcessActionPlan,
        preview: ProcessActionPreview,
        ttl_seconds: int,
        *,
        parent_confirmation_id: UUID | None = None,
    ) -> ProcessActionConfirmation:
        current = self._now()
        request = ProcessActionConfirmation(
            parent_confirmation_id=parent_confirmation_id,
            tier=tier,
            action=plan.action,
            transaction_id=plan.transaction_id,
            operation_id=plan.operation_id,
            plan_id=plan.plan_id,
            preview_id=preview.preview_id,
            plan_digest=plan.canonical_digest(),
            preview_digest=preview.canonical_digest(),
            target_set_digest=preview.target_set_digest,
            process_count=preview.process_count,
            requested_at=current,
            expires_at=current + timedelta(seconds=ttl_seconds),
        )
        self._requests[request.confirmation_id] = request
        return request

    def _resolve(
        self,
        confirmation_id: UUID,
        approved: bool,
        tier: ProcessConfirmationTier,
        plan: ProcessActionPlan,
        preview: ProcessActionPreview,
    ) -> ProcessActionConfirmation:
        request = self._get(confirmation_id)
        if request.tier is not tier or request.state is not ProcessConfirmationState.PENDING:
            raise ProcessConfirmationError("Confirmation tier is wrong or already resolved")
        self._require_current(request, plan, preview)
        self._require_not_expired(request)
        resolved = request.model_copy(
            update={
                "state": (
                    ProcessConfirmationState.APPROVED
                    if approved
                    else ProcessConfirmationState.REJECTED
                ),
                "confirmed_at": self._now() if approved else None,
            }
        )
        self._requests[confirmation_id] = resolved
        return resolved

    def _get(self, confirmation_id: UUID | None) -> ProcessActionConfirmation:
        if confirmation_id is None:
            raise ProcessConfirmationError("Missing process confirmation")
        try:
            return self._requests[confirmation_id]
        except KeyError as exc:
            raise ProcessConfirmationError("Unknown process confirmation") from exc

    def _require_not_expired(self, request: ProcessActionConfirmation) -> None:
        if self._now() >= request.expires_at:
            self._requests[request.confirmation_id] = request.model_copy(
                update={"state": ProcessConfirmationState.EXPIRED}
            )
            raise ProcessConfirmationError("Process confirmation expired")

    @staticmethod
    def _require_executable(
        plan: ProcessActionPlan,
        preview: ProcessActionPreview,
    ) -> None:
        if (
            not preview.executable
            or preview.plan_id != plan.plan_id
            or preview.transaction_id != plan.transaction_id
            or preview.plan_digest != plan.canonical_digest()
            or preview.action is not plan.action
            or preview.target_set_digest != plan.target_set_digest()
        ):
            raise ProcessConfirmationError("Process Preview is blocked, stale, or mismatched")

    @classmethod
    def _require_current(
        cls,
        request: ProcessActionConfirmation,
        plan: ProcessActionPlan,
        preview: ProcessActionPreview,
        *,
        allow_new_preview_id: bool = False,
    ) -> None:
        cls._require_executable(plan, preview)
        actual = (
            request.action,
            request.transaction_id,
            request.operation_id,
            request.plan_id,
            request.plan_digest,
            request.target_set_digest,
            request.process_count,
        )
        expected = (
            plan.action,
            plan.transaction_id,
            plan.operation_id,
            plan.plan_id,
            plan.canonical_digest(),
            preview.target_set_digest,
            preview.process_count,
        )
        if actual != expected:
            raise ProcessConfirmationError("Process confirmation bindings changed")
        if not allow_new_preview_id and (
            request.preview_id != preview.preview_id
            or request.preview_digest != preview.canonical_digest()
        ):
            raise ProcessConfirmationError("Process Preview changed after confirmation")
