"""Durable service transactions and ordered one-step execution capabilities."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from pc_manager_agent.confirmation.service_actions import (
    ServiceActionConfirmation,
    ServiceConfirmationState,
    ServiceConfirmationTier,
)
from pc_manager_agent.domain.service_actions import (
    ServiceActionPlan,
    ServiceActionPreview,
    ServiceActionTransaction,
    ServiceActionType,
    ServiceErrorCode,
    ServiceTransactionState,
)
from pc_manager_agent.persistence.database import create_sqlite_engine
from pc_manager_agent.tools.execution import ExecutionAuthorization, arguments_digest


class ServiceActionStoreError(RuntimeError):
    """Raised when service transaction persistence cannot be trusted."""


class ServiceActionBase(DeclarativeBase):
    """Declarative base isolated from other action journals."""


class ServiceActionTransactionRow(ServiceActionBase):
    """Relational record for one start, stop, or explicit restart transaction."""

    __tablename__ = "service_action_transactions"

    transaction_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    operation_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    plan_id: Mapped[str] = mapped_column(String(36), index=True)
    preview_id: Mapped[str] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(20))
    service_name: Mapped[str] = mapped_column(String(256), index=True)
    identity_digest: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(40), index=True)
    plan_digest: Mapped[str] = mapped_column(String(64))
    preview_digest: Mapped[str] = mapped_column(String(64))
    step_tools: Mapped[list[str]] = mapped_column(JSON)
    step_argument_digests: Mapped[list[str]] = mapped_column(JSON)
    next_step_index: Mapped[int] = mapped_column(Integer, default=0)
    step_in_progress: Mapped[bool] = mapped_column(Boolean, default=False)
    plan_confirmation_id: Mapped[str | None] = mapped_column(String(36))
    runtime_confirmation_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class ServiceActionConfirmationRow(ServiceActionBase):
    """Durable evidence for both one-time confirmation tiers."""

    __tablename__ = "service_action_confirmations"

    confirmation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    parent_confirmation_id: Mapped[str | None] = mapped_column(String(36), index=True)
    transaction_id: Mapped[str] = mapped_column(String(36), index=True)
    tier: Mapped[str] = mapped_column(String(20))
    action: Mapped[str] = mapped_column(String(20))
    plan_digest: Mapped[str] = mapped_column(String(64))
    preview_digest: Mapped[str] = mapped_column(String(64))
    identity_digest: Mapped[str] = mapped_column(String(64))
    state_digest: Mapped[str] = mapped_column(String(64))
    dependency_digest: Mapped[str] = mapped_column(String(64))
    permission_digest: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(20))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


_ALLOWED_TRANSITIONS: dict[ServiceTransactionState, frozenset[ServiceTransactionState]] = {
    ServiceTransactionState.PREVIEWED: frozenset(
        {
            ServiceTransactionState.AWAITING_CONFIRMATION,
            ServiceTransactionState.BLOCKED,
            ServiceTransactionState.FAILED,
        }
    ),
    ServiceTransactionState.AWAITING_CONFIRMATION: frozenset(
        {
            ServiceTransactionState.AWAITING_RUNTIME_CONFIRMATION,
            ServiceTransactionState.CANCELLED,
        }
    ),
    ServiceTransactionState.AWAITING_RUNTIME_CONFIRMATION: frozenset(
        {
            ServiceTransactionState.CONFIRMED,
            ServiceTransactionState.CANCELLED,
            ServiceTransactionState.BLOCKED,
            ServiceTransactionState.FAILED,
        }
    ),
    ServiceTransactionState.CONFIRMED: frozenset(
        {ServiceTransactionState.VALIDATING, ServiceTransactionState.CANCELLED}
    ),
    ServiceTransactionState.VALIDATING: frozenset(
        {
            ServiceTransactionState.EXECUTING_START,
            ServiceTransactionState.EXECUTING_STOP,
            ServiceTransactionState.COMPLETED,
            ServiceTransactionState.CANCELLED,
            ServiceTransactionState.BLOCKED,
            ServiceTransactionState.FAILED,
        }
    ),
    ServiceTransactionState.EXECUTING_START: frozenset(
        {
            ServiceTransactionState.WAITING_RUNNING,
            ServiceTransactionState.COMPLETED,
            ServiceTransactionState.PARTIALLY_COMPLETED,
            ServiceTransactionState.FAILED,
        }
    ),
    ServiceTransactionState.WAITING_RUNNING: frozenset(
        {
            ServiceTransactionState.COMPLETED,
            ServiceTransactionState.PARTIALLY_COMPLETED,
            ServiceTransactionState.FAILED,
        }
    ),
    ServiceTransactionState.EXECUTING_STOP: frozenset(
        {
            ServiceTransactionState.WAITING_STOPPED,
            ServiceTransactionState.STOP_COMPLETED,
            ServiceTransactionState.COMPLETED,
            ServiceTransactionState.FAILED,
        }
    ),
    ServiceTransactionState.WAITING_STOPPED: frozenset(
        {
            ServiceTransactionState.STOP_COMPLETED,
            ServiceTransactionState.COMPLETED,
            ServiceTransactionState.FAILED,
        }
    ),
    ServiceTransactionState.STOP_COMPLETED: frozenset(
        {
            ServiceTransactionState.EXECUTING_START,
            ServiceTransactionState.PARTIALLY_COMPLETED,
            ServiceTransactionState.CANCELLED,
        }
    ),
}


class ServiceActionRepository:
    """Persist Preview, confirmations, ordered steps, results, and interruptions."""

    def __init__(self, database_path: Path) -> None:
        self._engine = create_sqlite_engine(database_path)
        self._sessions = sessionmaker(self._engine, expire_on_commit=False)
        self._initialized = False

    def initialize(self) -> tuple[UUID, ...]:
        """Create additive tables and mark active writes interrupted without resuming."""
        try:
            ServiceActionBase.metadata.create_all(self._engine)
            with self._engine.begin() as connection:
                connection.execute(text("SELECT 1"))
            active = {
                ServiceTransactionState.VALIDATING.value,
                ServiceTransactionState.EXECUTING_START.value,
                ServiceTransactionState.WAITING_RUNNING.value,
                ServiceTransactionState.EXECUTING_STOP.value,
                ServiceTransactionState.WAITING_STOPPED.value,
                ServiceTransactionState.STOP_COMPLETED.value,
            }
            pending = {
                ServiceTransactionState.PREVIEWED.value,
                ServiceTransactionState.AWAITING_CONFIRMATION.value,
                ServiceTransactionState.AWAITING_RUNTIME_CONFIRMATION.value,
                ServiceTransactionState.CONFIRMED.value,
            }
            interrupted: list[UUID] = []
            now = datetime.now(UTC)
            with self._sessions.begin() as session:
                for row in tuple(
                    session.scalars(
                        select(ServiceActionTransactionRow).where(
                            ServiceActionTransactionRow.state.in_(active)
                        )
                    )
                ):
                    interrupted.append(UUID(row.transaction_id))
                    row.state = ServiceTransactionState.INTERRUPTED.value
                    row.step_in_progress = False
                    row.updated_at = now
                    row.error_code = ServiceErrorCode.PLATFORM_ERROR.value
                    row.error_message = (
                        "Application stopped during service control; inspect current SCM state"
                    )
                for row in tuple(
                    session.scalars(
                        select(ServiceActionTransactionRow).where(
                            ServiceActionTransactionRow.state.in_(pending)
                        )
                    )
                ):
                    row.state = ServiceTransactionState.CANCELLED.value
                    row.updated_at = now
                    row.error_message = "In-memory service confirmation was invalidated by restart"
        except SQLAlchemyError as exc:
            raise ServiceActionStoreError("Service action database initialization failed") from exc
        self._initialized = True
        return tuple(interrupted)

    def create(
        self,
        plan: ServiceActionPlan,
        preview: ServiceActionPreview,
        steps: tuple[tuple[str, Mapping[str, object]], ...],
    ) -> ServiceActionTransaction:
        """Persist the exact ordered tool sequence before any confirmation exists."""
        self._require_initialized()
        now = datetime.now(UTC)
        row = ServiceActionTransactionRow(
            transaction_id=str(plan.transaction_id),
            operation_id=str(plan.operation_id),
            plan_id=str(plan.plan_id),
            preview_id=str(preview.preview_id),
            action=plan.action.value,
            service_name=plan.target_identity.service_name,
            identity_digest=plan.target_identity.canonical_digest(),
            state=ServiceTransactionState.PREVIEWED.value,
            plan_digest=plan.canonical_digest(),
            preview_digest=preview.canonical_digest(),
            step_tools=[name for name, _arguments in steps],
            step_argument_digests=[arguments_digest(arguments) for _name, arguments in steps],
            next_step_index=0,
            step_in_progress=False,
            created_at=now,
            updated_at=now,
        )
        try:
            with self._sessions.begin() as session:
                session.add(row)
        except SQLAlchemyError as exc:
            raise ServiceActionStoreError("Service action transaction creation failed") from exc
        return self.get(plan.transaction_id)

    def transition(
        self,
        transaction_id: UUID,
        state: ServiceTransactionState,
        *,
        error_code: ServiceErrorCode | None = None,
        error_message: str | None = None,
        result: dict[str, JsonValue] | None = None,
    ) -> ServiceActionTransaction:
        """Apply one checked state transition."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                row = session.get(ServiceActionTransactionRow, str(transaction_id))
                if row is None:
                    raise ServiceActionStoreError("Unknown service action transaction")
                current = ServiceTransactionState(row.state)
                if state not in _ALLOWED_TRANSITIONS.get(current, frozenset()):
                    raise ServiceActionStoreError(
                        f"Invalid service action transition: {current.value} -> {state.value}"
                    )
                row.state = state.value
                row.updated_at = datetime.now(UTC)
                row.error_code = error_code.value if error_code else None
                row.error_message = error_message
                row.result = result
                if state in {
                    ServiceTransactionState.COMPLETED,
                    ServiceTransactionState.PARTIALLY_COMPLETED,
                    ServiceTransactionState.FAILED,
                    ServiceTransactionState.BLOCKED,
                    ServiceTransactionState.CANCELLED,
                    ServiceTransactionState.INTERRUPTED,
                }:
                    row.step_in_progress = False
        except SQLAlchemyError as exc:
            raise ServiceActionStoreError("Service action transition failed") from exc
        return self.get(transaction_id)

    def bind_runtime_preview(
        self,
        transaction_id: UUID,
        preview: ServiceActionPreview,
    ) -> None:
        """Replace initial Preview binding only while awaiting runtime approval."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                row = session.get(ServiceActionTransactionRow, str(transaction_id))
                if (
                    row is None
                    or row.state != ServiceTransactionState.AWAITING_RUNTIME_CONFIRMATION.value
                    or row.plan_id != str(preview.plan_id)
                    or row.identity_digest != preview.observation.identity.canonical_digest()
                ):
                    raise ServiceActionStoreError("Runtime service Preview is stale")
                row.preview_id = str(preview.preview_id)
                row.preview_digest = preview.canonical_digest()
                row.updated_at = datetime.now(UTC)
        except SQLAlchemyError as exc:
            raise ServiceActionStoreError("Runtime service Preview binding failed") from exc

    def record_confirmation(self, value: ServiceActionConfirmation) -> None:
        """Insert or update non-secret service confirmation evidence."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                row = session.get(ServiceActionConfirmationRow, str(value.confirmation_id))
                if row is None:
                    row = ServiceActionConfirmationRow(
                        confirmation_id=str(value.confirmation_id),
                        parent_confirmation_id=(
                            str(value.parent_confirmation_id)
                            if value.parent_confirmation_id
                            else None
                        ),
                        transaction_id=str(value.transaction_id),
                        tier=value.tier.value,
                        action=value.action.value,
                        plan_digest=value.plan_digest,
                        preview_digest=value.preview_digest,
                        identity_digest=value.identity_digest,
                        state_digest=value.state_digest,
                        dependency_digest=value.dependency_digest,
                        permission_digest=value.permission_digest,
                        state=value.state.value,
                        confirmed_at=value.confirmed_at,
                        expires_at=value.expires_at,
                    )
                    session.add(row)
                else:
                    row.state = value.state.value
                    row.confirmed_at = value.confirmed_at
        except SQLAlchemyError as exc:
            raise ServiceActionStoreError("Service confirmation persistence failed") from exc

    def bind_confirmation(
        self,
        transaction_id: UUID,
        confirmation_id: UUID,
        *,
        runtime: bool,
    ) -> None:
        """Bind one confirmation identifier to its durable transaction."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                row = session.get(ServiceActionTransactionRow, str(transaction_id))
                if row is None:
                    raise ServiceActionStoreError("Unknown service action transaction")
                if runtime:
                    row.runtime_confirmation_id = str(confirmation_id)
                else:
                    row.plan_confirmation_id = str(confirmation_id)
                row.updated_at = datetime.now(UTC)
        except SQLAlchemyError as exc:
            raise ServiceActionStoreError("Service confirmation binding failed") from exc

    def consume_confirmation_pair(
        self,
        plan_confirmation_id: UUID,
        runtime_confirmation: ServiceActionConfirmation,
    ) -> None:
        """Atomically consume matching same-action plan/runtime evidence."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                plan_row = session.get(ServiceActionConfirmationRow, str(plan_confirmation_id))
                runtime_row = session.get(
                    ServiceActionConfirmationRow,
                    str(runtime_confirmation.confirmation_id),
                )
                if (
                    plan_row is None
                    or runtime_row is None
                    or plan_row.state != ServiceConfirmationState.APPROVED.value
                    or runtime_row.state != ServiceConfirmationState.APPROVED.value
                    or plan_row.tier != ServiceConfirmationTier.PLAN.value
                    or runtime_row.tier != ServiceConfirmationTier.RUNTIME.value
                    or runtime_row.parent_confirmation_id != plan_row.confirmation_id
                    or plan_row.transaction_id != runtime_row.transaction_id
                    or plan_row.action != runtime_row.action
                    or plan_row.identity_digest != runtime_row.identity_digest
                ):
                    raise ServiceActionStoreError(
                        "Service confirmation pair is stale or mismatched"
                    )
                plan_row.state = ServiceConfirmationState.CONSUMED.value
                runtime_row.state = ServiceConfirmationState.CONSUMED.value
        except SQLAlchemyError as exc:
            raise ServiceActionStoreError("Service confirmation consumption failed") from exc

    def begin_step(
        self,
        transaction_id: UUID,
        step_index: int,
        expected_state: ServiceTransactionState,
    ) -> None:
        """Reserve the exact next ordered step before SCM dispatch."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                row = session.get(ServiceActionTransactionRow, str(transaction_id))
                if (
                    row is None
                    or row.state != expected_state.value
                    or row.next_step_index != step_index
                    or row.step_in_progress
                ):
                    raise ServiceActionStoreError("Service step order or state is invalid")
                row.step_in_progress = True
                row.updated_at = datetime.now(UTC)
        except SQLAlchemyError as exc:
            raise ServiceActionStoreError("Service step reservation failed") from exc

    def mark_dispatched(self, transaction_id: UUID, wait_state: ServiceTransactionState) -> None:
        """Persist that SCM control was sent before bounded verification continues."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                row = session.get(ServiceActionTransactionRow, str(transaction_id))
                if row is None or not row.step_in_progress:
                    raise ServiceActionStoreError("No service step is awaiting dispatch evidence")
                row.state = wait_state.value
                row.updated_at = datetime.now(UTC)
        except SQLAlchemyError as exc:
            raise ServiceActionStoreError("Service dispatch evidence persistence failed") from exc

    def complete_step(
        self,
        transaction_id: UUID,
        step_index: int,
        state: ServiceTransactionState,
    ) -> None:
        """Advance the ordered capability only after verified step completion."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                row = session.get(ServiceActionTransactionRow, str(transaction_id))
                if row is None or row.next_step_index != step_index or not row.step_in_progress:
                    raise ServiceActionStoreError("Service step completion is stale or replayed")
                row.next_step_index = step_index + 1
                row.step_in_progress = False
                row.state = state.value
                row.updated_at = datetime.now(UTC)
        except SQLAlchemyError as exc:
            raise ServiceActionStoreError("Service step completion failed") from exc

    def get(self, transaction_id: UUID) -> ServiceActionTransaction:
        """Return one public durable service transaction."""
        self._require_initialized()
        try:
            with self._sessions() as session:
                row = session.get(ServiceActionTransactionRow, str(transaction_id))
                if row is None:
                    raise ServiceActionStoreError("Unknown service action transaction")
                return _from_row(row)
        except SQLAlchemyError as exc:
            raise ServiceActionStoreError("Service transaction read failed") from exc

    def record_terminal_result(
        self,
        transaction_id: UUID,
        result: dict[str, JsonValue],
    ) -> None:
        """Attach the verified final/partial result without reopening a terminal state."""
        self._require_initialized()
        terminal = {
            ServiceTransactionState.COMPLETED.value,
            ServiceTransactionState.PARTIALLY_COMPLETED.value,
            ServiceTransactionState.CANCELLED.value,
        }
        try:
            with self._sessions.begin() as session:
                row = session.get(ServiceActionTransactionRow, str(transaction_id))
                if row is None or row.state not in terminal:
                    raise ServiceActionStoreError("Service result requires a terminal transaction")
                row.result = result
                row.step_in_progress = False
                row.updated_at = datetime.now(UTC)
        except SQLAlchemyError as exc:
            raise ServiceActionStoreError("Service terminal result persistence failed") from exc

    def close(self) -> None:
        """Dispose this repository engine."""
        self._engine.dispose()

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise ServiceActionStoreError("Service action repository is not initialized")


class ServiceExecutionGuard:
    """Authorize only the exact next service tool step in a confirmed transaction."""

    def __init__(self, repository: ServiceActionRepository) -> None:
        self._repository = repository

    def require(
        self,
        authorization: ExecutionAuthorization,
        tool_name: str,
        arguments: Mapping[str, JsonValue],
    ) -> None:
        """Fail closed on stale state, wrong order, argument change, or replay."""
        value = self._repository.get(authorization.transaction_id)
        expected_states = {
            "system.service.start": {
                ServiceTransactionState.EXECUTING_START,
                ServiceTransactionState.WAITING_RUNNING,
            },
            "system.service.stop": {
                ServiceTransactionState.EXECUTING_STOP,
                ServiceTransactionState.WAITING_STOPPED,
            },
        }
        if (
            value.operation_id != authorization.operation_id
            or value.plan_id != authorization.plan_id
            or value.preview_id != authorization.preview_id
            or value.runtime_confirmation_id != authorization.runtime_confirmation_id
            or value.state not in expected_states.get(tool_name, set())
        ):
            raise ServiceActionStoreError("Service execution capability is stale or mismatched")
        with self._repository._sessions() as session:
            row = session.get(ServiceActionTransactionRow, str(value.transaction_id))
            if (
                row is None
                or not row.step_in_progress
                or row.next_step_index >= len(row.step_tools)
            ):
                raise ServiceActionStoreError("No reserved service step is available")
            if (
                row.step_tools[row.next_step_index] != tool_name
                or row.step_argument_digests[row.next_step_index] != arguments_digest(arguments)
                or authorization.arguments_digest != arguments_digest(arguments)
            ):
                raise ServiceActionStoreError("Service tool order or arguments changed")


def _from_row(row: ServiceActionTransactionRow) -> ServiceActionTransaction:
    return ServiceActionTransaction(
        transaction_id=UUID(row.transaction_id),
        operation_id=UUID(row.operation_id),
        plan_id=UUID(row.plan_id),
        preview_id=UUID(row.preview_id),
        action=ServiceActionType(row.action),
        service_name=row.service_name,
        identity_digest=row.identity_digest,
        state=ServiceTransactionState(row.state),
        plan_digest=row.plan_digest,
        preview_digest=row.preview_digest,
        next_step_index=row.next_step_index,
        step_in_progress=row.step_in_progress,
        plan_confirmation_id=(UUID(row.plan_confirmation_id) if row.plan_confirmation_id else None),
        runtime_confirmation_id=(
            UUID(row.runtime_confirmation_id) if row.runtime_confirmation_id else None
        ),
        created_at=row.created_at,
        updated_at=row.updated_at,
        error_code=ServiceErrorCode(row.error_code) if row.error_code else None,
        error_message=row.error_message,
        result=row.result,
    )
