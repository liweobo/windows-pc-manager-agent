"""Two independent, one-time confirmations bound to an exact Stage 2B Preview."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import Field

from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.trash import TrashPlan, TrashPreview


class TrashConfirmationTier(StrEnum):
    """The two mandatory R2 authorization boundaries."""

    PLAN = "PLAN"
    RUNTIME = "RUNTIME"


class TrashConfirmationState(StrEnum):
    """Lifecycle of one exact confirmation request."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CONSUMED = "CONSUMED"


class TrashConfirmation(FrozenModel):
    """R2 authorization bound to plan, live object set, counts, size, and expiry."""

    confirmation_id: UUID = Field(default_factory=uuid4)
    tier: TrashConfirmationTier
    parent_confirmation_id: UUID | None = None
    transaction_id: UUID
    plan_id: UUID
    preview_id: UUID
    plan_digest: str = Field(min_length=64, max_length=64)
    preview_digest: str = Field(min_length=64, max_length=64)
    object_set_digest: str = Field(min_length=64, max_length=64)
    selected_count: int = Field(ge=1)
    ready_count: int = Field(ge=1)
    total_size_bytes: int = Field(ge=0)
    requested_at: datetime
    confirmed_at: datetime | None = None
    expires_at: datetime
    state: TrashConfirmationState = TrashConfirmationState.PENDING


class TrashConfirmationError(RuntimeError):
    """Raised when either R2 confirmation is absent, stale, expired, or replayed."""


class TrashConfirmationService:
    """Issue plan approval first and runtime approval only after fresh revalidation."""

    def __init__(
        self,
        plan_ttl_seconds: int = 300,
        runtime_ttl_seconds: int = 60,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if plan_ttl_seconds <= 0 or runtime_ttl_seconds <= 0:
            raise ValueError("Trash confirmation TTLs must be positive")
        self._plan_ttl = plan_ttl_seconds
        self._runtime_ttl = runtime_ttl_seconds
        self._now = now or (lambda: datetime.now(UTC))
        self._requests: dict[UUID, TrashConfirmation] = {}

    def request_plan(self, plan: TrashPlan, preview: TrashPreview) -> TrashConfirmation:
        """Create first-level approval only for a matching fully executable Preview."""
        self._require_executable(plan, preview)
        return self._create(TrashConfirmationTier.PLAN, plan, preview, self._plan_ttl)

    def resolve_plan(
        self,
        confirmation_id: UUID,
        approved: bool,
        plan: TrashPlan,
        preview: TrashPreview,
    ) -> TrashConfirmation:
        """Approve or reject the first level after rechecking all immutable bindings."""
        return self._resolve(confirmation_id, approved, TrashConfirmationTier.PLAN, plan, preview)

    def request_runtime(
        self,
        plan_confirmation_id: UUID,
        plan: TrashPlan,
        revalidated_preview: TrashPreview,
    ) -> TrashConfirmation:
        """Create immediate approval only from an approved first level and fresh Preview."""
        parent = self._get(plan_confirmation_id)
        if (
            parent.tier is not TrashConfirmationTier.PLAN
            or parent.state is not TrashConfirmationState.APPROVED
        ):
            raise TrashConfirmationError("An approved plan confirmation is required first")
        self._require_not_expired(parent)
        self._require_current(parent, plan, revalidated_preview, allow_new_preview_id=True)
        request = self._create(
            TrashConfirmationTier.RUNTIME,
            plan,
            revalidated_preview,
            self._runtime_ttl,
            parent_confirmation_id=parent.confirmation_id,
        )
        return request

    def resolve_runtime(
        self,
        confirmation_id: UUID,
        approved: bool,
        plan: TrashPlan,
        preview: TrashPreview,
    ) -> TrashConfirmation:
        """Resolve the immediate object-specific confirmation."""
        return self._resolve(
            confirmation_id, approved, TrashConfirmationTier.RUNTIME, plan, preview
        )

    def consume_runtime(
        self,
        confirmation_id: UUID,
        plan: TrashPlan,
        preview: TrashPreview,
    ) -> TrashConfirmation:
        """Consume runtime approval exactly once at the transaction RUNNING boundary."""
        request = self._get(confirmation_id)
        if request.tier is not TrashConfirmationTier.RUNTIME:
            raise TrashConfirmationError("Runtime confirmation is required")
        if request.state is not TrashConfirmationState.APPROVED:
            raise TrashConfirmationError("Runtime confirmation is not approved or was consumed")
        self._require_current(request, plan, preview)
        self._require_not_expired(request)
        consumed = request.model_copy(update={"state": TrashConfirmationState.CONSUMED})
        self._requests[confirmation_id] = consumed
        parent = self._get(request.parent_confirmation_id)
        if parent.state is not TrashConfirmationState.APPROVED:
            raise TrashConfirmationError("Plan confirmation is no longer approved")
        self._require_not_expired(parent)
        self._requests[parent.confirmation_id] = parent.model_copy(
            update={"state": TrashConfirmationState.CONSUMED}
        )
        return consumed

    def _create(
        self,
        tier: TrashConfirmationTier,
        plan: TrashPlan,
        preview: TrashPreview,
        ttl: int,
        *,
        parent_confirmation_id: UUID | None = None,
    ) -> TrashConfirmation:
        current = self._now()
        request = TrashConfirmation(
            tier=tier,
            parent_confirmation_id=parent_confirmation_id,
            transaction_id=preview.transaction_id,
            plan_id=plan.plan_id,
            preview_id=preview.preview_id,
            plan_digest=plan.canonical_digest(),
            preview_digest=preview.canonical_digest(),
            object_set_digest=preview.object_set_digest,
            selected_count=preview.selected_count,
            ready_count=preview.ready_count,
            total_size_bytes=preview.total_size_bytes,
            requested_at=current,
            expires_at=current + timedelta(seconds=ttl),
        )
        self._requests[request.confirmation_id] = request
        return request

    def _resolve(
        self,
        confirmation_id: UUID,
        approved: bool,
        tier: TrashConfirmationTier,
        plan: TrashPlan,
        preview: TrashPreview,
    ) -> TrashConfirmation:
        request = self._get(confirmation_id)
        if request.tier is not tier or request.state is not TrashConfirmationState.PENDING:
            raise TrashConfirmationError("Confirmation tier is wrong or was already resolved")
        self._require_current(request, plan, preview)
        self._require_not_expired(request)
        resolved = request.model_copy(
            update={
                "state": (
                    TrashConfirmationState.APPROVED if approved else TrashConfirmationState.REJECTED
                ),
                "confirmed_at": self._now() if approved else None,
            }
        )
        self._requests[confirmation_id] = resolved
        return resolved

    def _get(self, confirmation_id: UUID | None) -> TrashConfirmation:
        if confirmation_id is None:
            raise TrashConfirmationError("Missing trash confirmation")
        try:
            return self._requests[confirmation_id]
        except KeyError as exc:
            raise TrashConfirmationError("Unknown trash confirmation") from exc

    def _require_not_expired(self, request: TrashConfirmation) -> None:
        if self._now() >= request.expires_at:
            self._requests[request.confirmation_id] = request.model_copy(
                update={"state": TrashConfirmationState.EXPIRED}
            )
            raise TrashConfirmationError("Trash confirmation expired")

    @staticmethod
    def _require_executable(plan: TrashPlan, preview: TrashPreview) -> None:
        if preview.plan_id != plan.plan_id or preview.plan_digest != plan.canonical_digest():
            raise TrashConfirmationError("Trash Preview does not match the current plan")
        if preview.ready_count != preview.selected_count or preview.blocked_count != 0:
            raise TrashConfirmationError("Every selected object must be safe before confirmation")

    @classmethod
    def _require_current(
        cls,
        request: TrashConfirmation,
        plan: TrashPlan,
        preview: TrashPreview,
        *,
        allow_new_preview_id: bool = False,
    ) -> None:
        cls._require_executable(plan, preview)
        if request.transaction_id != preview.transaction_id:
            raise TrashConfirmationError("Trash transaction changed")
        if not allow_new_preview_id and request.preview_id != preview.preview_id:
            raise TrashConfirmationError("Trash Preview changed")
        expected = (
            plan.plan_id,
            plan.canonical_digest(),
            preview.object_set_digest,
            preview.selected_count,
            preview.ready_count,
            preview.total_size_bytes,
        )
        actual = (
            request.plan_id,
            request.plan_digest,
            request.object_set_digest,
            request.selected_count,
            request.ready_count,
            request.total_size_bytes,
        )
        if actual != expected:
            raise TrashConfirmationError("Trash confirmation is stale or mismatched")
