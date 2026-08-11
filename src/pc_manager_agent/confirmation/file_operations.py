"""One-time confirmations bound to a concrete live file-operation Preview."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import Field

from pc_manager_agent.domain.file_operations import FileOperationPlan, FileOperationPreview
from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.rollback.models import RollbackPlan


class OperationConfirmationState(StrEnum):
    """Lifecycle of an immutable Preview-bound confirmation."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CONSUMED = "CONSUMED"


class OperationConfirmation(FrozenModel):
    """Exact R1 authorization for one transaction and Preview snapshot."""

    confirmation_id: UUID = Field(default_factory=uuid4)
    transaction_id: UUID
    plan_id: UUID
    preview_id: UUID
    plan_digest: str = Field(min_length=64, max_length=64)
    preview_digest: str = Field(min_length=64, max_length=64)
    operation_count: int = Field(ge=1)
    ready_count: int = Field(ge=1)
    requested_at: datetime
    confirmed_at: datetime | None = None
    expires_at: datetime
    state: OperationConfirmationState = OperationConfirmationState.PENDING


class OperationConfirmationError(RuntimeError):
    """Raised when a Preview confirmation is missing, stale, expired, or replayed."""


class OperationConfirmationService:
    """Issue, resolve, and consume exact R1 confirmations without trusting UI state."""

    def __init__(
        self,
        ttl_seconds: int = 300,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("Confirmation TTL must be positive")
        self._ttl_seconds = ttl_seconds
        self._now = now or (lambda: datetime.now(UTC))
        self._requests: dict[UUID, OperationConfirmation] = {}

    def request(
        self,
        plan: FileOperationPlan,
        preview: FileOperationPreview,
    ) -> OperationConfirmation:
        """Create a confirmation only for a matching Preview with executable items."""
        self._require_matching(plan, preview)
        if preview.ready_count == 0:
            raise OperationConfirmationError("Preview has no safe operation to confirm")
        current = self._now()
        request = OperationConfirmation(
            transaction_id=preview.transaction_id,
            plan_id=plan.plan_id,
            preview_id=preview.preview_id,
            plan_digest=plan.canonical_digest(),
            preview_digest=preview.canonical_digest(),
            operation_count=len(preview.items),
            ready_count=preview.ready_count,
            requested_at=current,
            expires_at=current + timedelta(seconds=self._ttl_seconds),
        )
        self._requests[request.confirmation_id] = request
        return request

    def resolve(
        self,
        confirmation_id: UUID,
        approved: bool,
        plan: FileOperationPlan,
        preview: FileOperationPreview,
    ) -> OperationConfirmation:
        """Approve or reject only when all Plan and Preview bindings still match."""
        request = self._get_pending(confirmation_id)
        self._require_current(request, plan, preview)
        current = self._now()
        if current >= request.expires_at:
            expired = request.model_copy(update={"state": OperationConfirmationState.EXPIRED})
            self._requests[confirmation_id] = expired
            raise OperationConfirmationError("Operation confirmation expired")
        state = (
            OperationConfirmationState.APPROVED if approved else OperationConfirmationState.REJECTED
        )
        resolved = request.model_copy(
            update={"state": state, "confirmed_at": current if approved else None}
        )
        self._requests[confirmation_id] = resolved
        return resolved

    def consume(
        self,
        confirmation_id: UUID,
        plan: FileOperationPlan,
        preview: FileOperationPreview,
    ) -> OperationConfirmation:
        """Consume an approval once as a Transaction begins; replay is rejected."""
        try:
            request = self._requests[confirmation_id]
        except KeyError as exc:
            raise OperationConfirmationError("Unknown operation confirmation") from exc
        if request.state is not OperationConfirmationState.APPROVED:
            raise OperationConfirmationError(
                "Operation confirmation is not approved or was consumed"
            )
        self._require_current(request, plan, preview)
        if self._now() >= request.expires_at:
            expired = request.model_copy(update={"state": OperationConfirmationState.EXPIRED})
            self._requests[confirmation_id] = expired
            raise OperationConfirmationError("Operation confirmation expired")
        consumed = request.model_copy(update={"state": OperationConfirmationState.CONSUMED})
        self._requests[confirmation_id] = consumed
        return consumed

    def _get_pending(self, confirmation_id: UUID) -> OperationConfirmation:
        try:
            request = self._requests[confirmation_id]
        except KeyError as exc:
            raise OperationConfirmationError("Unknown operation confirmation") from exc
        if request.state is not OperationConfirmationState.PENDING:
            raise OperationConfirmationError("Operation confirmation was already resolved")
        return request

    @staticmethod
    def _require_matching(plan: FileOperationPlan, preview: FileOperationPreview) -> None:
        if preview.plan_id != plan.plan_id or preview.plan_digest != plan.canonical_digest():
            raise OperationConfirmationError("Preview does not match the current operation plan")
        if len(preview.items) != len(plan.operations):
            raise OperationConfirmationError("Preview operation count does not match the plan")

    @classmethod
    def _require_current(
        cls,
        request: OperationConfirmation,
        plan: FileOperationPlan,
        preview: FileOperationPreview,
    ) -> None:
        cls._require_matching(plan, preview)
        expected = (
            preview.transaction_id,
            plan.plan_id,
            preview.preview_id,
            plan.canonical_digest(),
            preview.canonical_digest(),
            len(preview.items),
            preview.ready_count,
        )
        actual = (
            request.transaction_id,
            request.plan_id,
            request.preview_id,
            request.plan_digest,
            request.preview_digest,
            request.operation_count,
            request.ready_count,
        )
        if actual != expected:
            raise OperationConfirmationError("Operation confirmation is stale or mismatched")


class RollbackConfirmation(FrozenModel):
    """One-time authorization bound to an exact rollback plan and live state snapshot."""

    confirmation_id: UUID = Field(default_factory=uuid4)
    transaction_id: UUID
    rollback_plan_id: UUID
    rollback_digest: str = Field(min_length=64, max_length=64)
    ready_count: int = Field(ge=1)
    requested_at: datetime
    confirmed_at: datetime | None = None
    expires_at: datetime
    state: OperationConfirmationState = OperationConfirmationState.PENDING


class RollbackConfirmationService:
    """Issue and consume rollback confirmations independently of forward approval."""

    def __init__(
        self,
        ttl_seconds: int = 300,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("Confirmation TTL must be positive")
        self._ttl_seconds = ttl_seconds
        self._now = now or (lambda: datetime.now(UTC))
        self._requests: dict[UUID, RollbackConfirmation] = {}

    def request(self, plan: RollbackPlan) -> RollbackConfirmation:
        """Create a confirmation only when the rollback plan has a safe executable item."""
        ready = sum(item.status.value == "READY" for item in plan.items)
        if ready == 0:
            raise OperationConfirmationError("Rollback plan has no safe item to confirm")
        current = self._now()
        request = RollbackConfirmation(
            transaction_id=plan.transaction_id,
            rollback_plan_id=plan.rollback_plan_id,
            rollback_digest=plan.canonical_digest(),
            ready_count=ready,
            requested_at=current,
            expires_at=current + timedelta(seconds=self._ttl_seconds),
        )
        self._requests[request.confirmation_id] = request
        return request

    def resolve(
        self,
        confirmation_id: UUID,
        approved: bool,
        plan: RollbackPlan,
    ) -> RollbackConfirmation:
        """Resolve only an unexpired pending confirmation for the exact rollback plan."""
        request = self._get(confirmation_id)
        if request.state is not OperationConfirmationState.PENDING:
            raise OperationConfirmationError("Rollback confirmation was already resolved")
        self._require_current(request, plan)
        current = self._now()
        if current >= request.expires_at:
            expired = request.model_copy(update={"state": OperationConfirmationState.EXPIRED})
            self._requests[confirmation_id] = expired
            raise OperationConfirmationError("Rollback confirmation expired")
        state = (
            OperationConfirmationState.APPROVED if approved else OperationConfirmationState.REJECTED
        )
        resolved = request.model_copy(
            update={"state": state, "confirmed_at": current if approved else None}
        )
        self._requests[confirmation_id] = resolved
        return resolved

    def consume(
        self,
        confirmation_id: UUID,
        plan: RollbackPlan,
    ) -> RollbackConfirmation:
        """Consume one approved rollback confirmation and reject replay."""
        request = self._get(confirmation_id)
        if request.state is not OperationConfirmationState.APPROVED:
            raise OperationConfirmationError(
                "Rollback confirmation is not approved or was consumed"
            )
        self._require_current(request, plan)
        if self._now() >= request.expires_at:
            expired = request.model_copy(update={"state": OperationConfirmationState.EXPIRED})
            self._requests[confirmation_id] = expired
            raise OperationConfirmationError("Rollback confirmation expired")
        consumed = request.model_copy(update={"state": OperationConfirmationState.CONSUMED})
        self._requests[confirmation_id] = consumed
        return consumed

    def _get(self, confirmation_id: UUID) -> RollbackConfirmation:
        try:
            return self._requests[confirmation_id]
        except KeyError as exc:
            raise OperationConfirmationError("Unknown rollback confirmation") from exc

    @staticmethod
    def _require_current(request: RollbackConfirmation, plan: RollbackPlan) -> None:
        ready = sum(item.status.value == "READY" for item in plan.items)
        actual = (
            request.transaction_id,
            request.rollback_plan_id,
            request.rollback_digest,
            request.ready_count,
        )
        expected = (
            plan.transaction_id,
            plan.rollback_plan_id,
            plan.canonical_digest(),
            ready,
        )
        if actual != expected:
            raise OperationConfirmationError("Rollback confirmation is stale or mismatched")
