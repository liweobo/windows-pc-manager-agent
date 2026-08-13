"""Durable Stage 4A process transactions and exact write capabilities."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import JSON, DateTime, Integer, String, Text, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from pc_manager_agent.confirmation.process_actions import (
    ProcessActionConfirmation,
    ProcessConfirmationState,
    ProcessConfirmationTier,
)
from pc_manager_agent.domain.process_actions import (
    ProcessActionErrorCode,
    ProcessActionPlan,
    ProcessActionPreview,
    ProcessActionState,
    ProcessActionTransaction,
    ProcessActionType,
)
from pc_manager_agent.persistence.database import create_sqlite_engine
from pc_manager_agent.tools.execution import ExecutionAuthorization, arguments_digest


class ProcessActionStoreError(RuntimeError):
    """Raised when process transaction persistence cannot be trusted."""


class ProcessActionBase(DeclarativeBase):
    """Declarative base isolated from the existing file-operation journal."""


class ProcessActionTransactionRow(ProcessActionBase):
    """Relational record for one non-reversible process action."""

    __tablename__ = "process_action_transactions"

    transaction_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    operation_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    plan_id: Mapped[str] = mapped_column(String(36), index=True)
    preview_id: Mapped[str] = mapped_column(String(36))
    parent_transaction_id: Mapped[str | None] = mapped_column(String(36), index=True)
    action: Mapped[str] = mapped_column(String(40))
    state: Mapped[str] = mapped_column(String(40), index=True)
    plan_digest: Mapped[str] = mapped_column(String(64))
    preview_digest: Mapped[str] = mapped_column(String(64))
    target_set_digest: Mapped[str] = mapped_column(String(64))
    tool_name: Mapped[str] = mapped_column(String(120))
    arguments_digest: Mapped[str] = mapped_column(String(64))
    process_count: Mapped[int] = mapped_column(Integer)
    plan_confirmation_id: Mapped[str | None] = mapped_column(String(36))
    runtime_confirmation_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class ProcessActionConfirmationRow(ProcessActionBase):
    """Durable evidence for both confirmation tiers and one-time consumption."""

    __tablename__ = "process_action_confirmations"

    confirmation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    parent_confirmation_id: Mapped[str | None] = mapped_column(String(36), index=True)
    transaction_id: Mapped[str] = mapped_column(String(36), index=True)
    tier: Mapped[str] = mapped_column(String(20))
    action: Mapped[str] = mapped_column(String(40))
    plan_digest: Mapped[str] = mapped_column(String(64))
    preview_digest: Mapped[str] = mapped_column(String(64))
    target_set_digest: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(20))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


_ALLOWED_TRANSITIONS: dict[ProcessActionState, frozenset[ProcessActionState]] = {
    ProcessActionState.PREVIEWED: frozenset(
        {
            ProcessActionState.AWAITING_CONFIRMATION,
            ProcessActionState.BLOCKED,
            ProcessActionState.FAILED,
        }
    ),
    ProcessActionState.AWAITING_CONFIRMATION: frozenset(
        {ProcessActionState.AWAITING_RUNTIME_CONFIRMATION, ProcessActionState.CANCELLED}
    ),
    ProcessActionState.AWAITING_RUNTIME_CONFIRMATION: frozenset(
        {
            ProcessActionState.CONFIRMED,
            ProcessActionState.CANCELLED,
            ProcessActionState.ALREADY_EXITED,
            ProcessActionState.BLOCKED,
            ProcessActionState.FAILED,
        }
    ),
    ProcessActionState.CONFIRMED: frozenset(
        {ProcessActionState.VALIDATING, ProcessActionState.CANCELLED}
    ),
    ProcessActionState.VALIDATING: frozenset(
        {
            ProcessActionState.REQUESTING_GRACEFUL_EXIT,
            ProcessActionState.FORCE_TERMINATING,
            ProcessActionState.ALREADY_EXITED,
            ProcessActionState.BLOCKED,
            ProcessActionState.FAILED,
        }
    ),
    ProcessActionState.REQUESTING_GRACEFUL_EXIT: frozenset(
        {
            ProcessActionState.WAITING_FOR_EXIT,
            ProcessActionState.GRACEFUL_COMPLETED,
            ProcessActionState.GRACEFUL_TIMEOUT,
            ProcessActionState.CANCELLED,
            ProcessActionState.ALREADY_EXITED,
            ProcessActionState.BLOCKED,
            ProcessActionState.FAILED,
            ProcessActionState.UNKNOWN,
        }
    ),
    ProcessActionState.WAITING_FOR_EXIT: frozenset(
        {
            ProcessActionState.GRACEFUL_COMPLETED,
            ProcessActionState.GRACEFUL_TIMEOUT,
            ProcessActionState.CANCELLED,
            ProcessActionState.FAILED,
        }
    ),
    ProcessActionState.FORCE_TERMINATING: frozenset(
        {
            ProcessActionState.COMPLETED,
            ProcessActionState.ALREADY_EXITED,
            ProcessActionState.CANCELLED,
            ProcessActionState.FAILED,
            ProcessActionState.UNKNOWN,
        }
    ),
}


class ProcessActionRepository:
    """Persist Preview, confirmations, execution state, and verified results."""

    def __init__(self, database_path: Path) -> None:
        self._engine = create_sqlite_engine(database_path)
        self._sessions = sessionmaker(self._engine, expire_on_commit=False)
        self._initialized = False

    def initialize(self) -> tuple[UUID, ...]:
        """Create additive tables and mark interrupted mutations truthfully unknown."""
        try:
            ProcessActionBase.metadata.create_all(self._engine)
            with self._engine.begin() as connection:
                connection.execute(text("SELECT 1"))
            interrupted: list[UUID] = []
            active_states = {
                ProcessActionState.VALIDATING.value,
                ProcessActionState.REQUESTING_GRACEFUL_EXIT.value,
                ProcessActionState.WAITING_FOR_EXIT.value,
                ProcessActionState.FORCE_TERMINATING.value,
            }
            current = datetime.now(UTC)
            with self._sessions.begin() as session:
                rows = tuple(
                    session.scalars(
                        select(ProcessActionTransactionRow).where(
                            ProcessActionTransactionRow.state.in_(active_states)
                        )
                    )
                )
                for row in rows:
                    interrupted.append(UUID(row.transaction_id))
                    row.state = ProcessActionState.INTERRUPTED.value
                    row.updated_at = current
                    row.error_code = ProcessActionErrorCode.PLATFORM_ERROR.value
                    row.error_message = "Application stopped during a process action; inspect state"
                pending_states = {
                    ProcessActionState.PREVIEWED.value,
                    ProcessActionState.AWAITING_CONFIRMATION.value,
                    ProcessActionState.AWAITING_RUNTIME_CONFIRMATION.value,
                    ProcessActionState.CONFIRMED.value,
                }
                pending = tuple(
                    session.scalars(
                        select(ProcessActionTransactionRow).where(
                            ProcessActionTransactionRow.state.in_(pending_states)
                        )
                    )
                )
                for row in pending:
                    row.state = ProcessActionState.CANCELLED.value
                    row.updated_at = current
                    row.error_message = "In-memory confirmation was invalidated by restart"
        except SQLAlchemyError as exc:
            raise ProcessActionStoreError("Process action database initialization failed") from exc
        self._initialized = True
        return tuple(interrupted)

    def create(
        self,
        plan: ProcessActionPlan,
        preview: ProcessActionPreview,
        tool_name: str,
        argument_payload: Mapping[str, object],
    ) -> ProcessActionTransaction:
        """Persist the exact Preview and reserve tool arguments before confirmation."""
        self._require_initialized()
        value = ProcessActionTransaction(
            transaction_id=plan.transaction_id,
            operation_id=plan.operation_id,
            plan_id=plan.plan_id,
            preview_id=preview.preview_id,
            parent_transaction_id=plan.parent_transaction_id,
            action=plan.action,
            state=ProcessActionState.PREVIEWED,
            plan_digest=plan.canonical_digest(),
            preview_digest=preview.canonical_digest(),
            target_set_digest=preview.target_set_digest,
            process_count=preview.process_count,
        )
        row = _to_row(value, tool_name, arguments_digest(argument_payload))
        try:
            with self._sessions.begin() as session:
                session.add(row)
        except SQLAlchemyError as exc:
            raise ProcessActionStoreError("Process transaction creation failed") from exc
        return value

    def transition(
        self,
        transaction_id: UUID,
        state: ProcessActionState,
        *,
        error_code: ProcessActionErrorCode | None = None,
        error_message: str | None = None,
        result: dict[str, JsonValue] | None = None,
    ) -> ProcessActionTransaction:
        """Apply one checked state transition and return the updated transaction."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                row = session.get(ProcessActionTransactionRow, str(transaction_id))
                if row is None:
                    raise ProcessActionStoreError("Unknown process action transaction")
                current = ProcessActionState(row.state)
                if state not in _ALLOWED_TRANSITIONS.get(current, frozenset()):
                    raise ProcessActionStoreError(
                        f"Invalid process action transition: {current.value} -> {state.value}"
                    )
                row.state = state.value
                row.updated_at = datetime.now(UTC)
                row.error_code = error_code.value if error_code else None
                row.error_message = error_message
                row.result = result
        except SQLAlchemyError as exc:
            raise ProcessActionStoreError("Process transaction transition failed") from exc
        return self.get(transaction_id)

    def record_confirmation(self, value: ProcessActionConfirmation) -> None:
        """Insert or update non-secret confirmation evidence."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                row = session.get(ProcessActionConfirmationRow, str(value.confirmation_id))
                if row is None:
                    row = ProcessActionConfirmationRow(
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
                        target_set_digest=value.target_set_digest,
                        state=value.state.value,
                        confirmed_at=value.confirmed_at,
                        expires_at=value.expires_at,
                    )
                    session.add(row)
                else:
                    row.state = value.state.value
                    row.confirmed_at = value.confirmed_at
        except SQLAlchemyError as exc:
            raise ProcessActionStoreError("Process confirmation persistence failed") from exc

    def bind_plan_confirmation(self, transaction_id: UUID, confirmation_id: UUID) -> None:
        """Bind the approved plan token to its durable transaction."""
        self._bind_confirmation(transaction_id, confirmation_id, runtime=False)

    def bind_runtime_confirmation(self, transaction_id: UUID, confirmation_id: UUID) -> None:
        """Bind the approved runtime token to its durable transaction."""
        self._bind_confirmation(transaction_id, confirmation_id, runtime=True)

    def bind_runtime_preview(
        self,
        transaction_id: UUID,
        preview: ProcessActionPreview,
    ) -> None:
        """Replace the initial Preview binding with the freshly revalidated runtime Preview."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                row = session.get(ProcessActionTransactionRow, str(transaction_id))
                if (
                    row is None
                    or row.state != ProcessActionState.AWAITING_RUNTIME_CONFIRMATION.value
                    or row.plan_id != str(preview.plan_id)
                    or row.target_set_digest != preview.target_set_digest
                ):
                    raise ProcessActionStoreError("Runtime Preview cannot replace stale bindings")
                row.preview_id = str(preview.preview_id)
                row.preview_digest = preview.canonical_digest()
                row.updated_at = datetime.now(UTC)
        except SQLAlchemyError as exc:
            raise ProcessActionStoreError("Runtime Preview binding failed") from exc

    def consume_confirmation_pair(
        self,
        plan_confirmation_id: UUID,
        runtime_confirmation: ProcessActionConfirmation,
    ) -> None:
        """Atomically mark matching same-action plan/runtime evidence consumed."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                plan_row = session.get(ProcessActionConfirmationRow, str(plan_confirmation_id))
                runtime_row = session.get(
                    ProcessActionConfirmationRow, str(runtime_confirmation.confirmation_id)
                )
                if (
                    plan_row is None
                    or runtime_row is None
                    or plan_row.state != ProcessConfirmationState.APPROVED.value
                    or runtime_row.state != ProcessConfirmationState.APPROVED.value
                    or plan_row.tier != ProcessConfirmationTier.PLAN.value
                    or runtime_row.tier != ProcessConfirmationTier.RUNTIME.value
                    or runtime_row.parent_confirmation_id != plan_row.confirmation_id
                    or plan_row.transaction_id != runtime_row.transaction_id
                    or plan_row.action != runtime_row.action
                    or plan_row.target_set_digest != runtime_row.target_set_digest
                ):
                    raise ProcessActionStoreError(
                        "Process confirmation pair is stale or mismatched"
                    )
                plan_row.state = ProcessConfirmationState.CONSUMED.value
                runtime_row.state = ProcessConfirmationState.CONSUMED.value
        except SQLAlchemyError as exc:
            raise ProcessActionStoreError("Process confirmation consumption failed") from exc

    def get(self, transaction_id: UUID) -> ProcessActionTransaction:
        """Return one durable process action transaction."""
        self._require_initialized()
        try:
            with self._sessions() as session:
                row = session.get(ProcessActionTransactionRow, str(transaction_id))
        except SQLAlchemyError as exc:
            raise ProcessActionStoreError("Process transaction lookup failed") from exc
        if row is None:
            raise ProcessActionStoreError("Unknown process action transaction")
        return _from_row(row)

    def list_recent(self, limit: int = 100) -> tuple[ProcessActionTransaction, ...]:
        """Return newest process actions with a strict upper bound."""
        self._require_initialized()
        statement = (
            select(ProcessActionTransactionRow)
            .order_by(ProcessActionTransactionRow.created_at.desc())
            .limit(max(1, min(limit, 500)))
        )
        try:
            with self._sessions() as session:
                rows = tuple(session.scalars(statement))
        except SQLAlchemyError as exc:
            raise ProcessActionStoreError("Process transaction history failed") from exc
        return tuple(_from_row(row) for row in rows)

    def require_execution_authorization(
        self,
        authorization: ExecutionAuthorization,
        tool_name: str,
        argument_payload: Mapping[str, JsonValue],
    ) -> None:
        """Validate action, state, reserved arguments, and consumed two-tier proof."""
        self._require_initialized()
        try:
            with self._sessions() as session:
                row = session.get(ProcessActionTransactionRow, str(authorization.transaction_id))
                runtime = session.get(
                    ProcessActionConfirmationRow,
                    str(authorization.runtime_confirmation_id),
                )
                plan = (
                    session.get(ProcessActionConfirmationRow, row.plan_confirmation_id)
                    if row is not None and row.plan_confirmation_id is not None
                    else None
                )
                expected_state = (
                    ProcessActionState.REQUESTING_GRACEFUL_EXIT
                    if tool_name == "system.process.request_exit"
                    else ProcessActionState.FORCE_TERMINATING
                )
                if (
                    row is None
                    or row.state != expected_state.value
                    or row.operation_id != str(authorization.operation_id)
                    or row.plan_id != str(authorization.plan_id)
                    or row.preview_id != str(authorization.preview_id)
                    or row.tool_name != tool_name
                    or row.arguments_digest != authorization.arguments_digest
                    or row.arguments_digest != arguments_digest(argument_payload)
                    or runtime is None
                    or plan is None
                    or runtime.state != ProcessConfirmationState.CONSUMED.value
                    or plan.state != ProcessConfirmationState.CONSUMED.value
                    or runtime.parent_confirmation_id != plan.confirmation_id
                    or runtime.action != row.action
                    or runtime.target_set_digest != row.target_set_digest
                    or row.runtime_confirmation_id != runtime.confirmation_id
                    or row.preview_digest != runtime.preview_digest
                ):
                    raise ProcessActionStoreError(
                        "Process write capability lacks exact consumed authorization"
                    )
        except SQLAlchemyError as exc:
            raise ProcessActionStoreError("Process authorization lookup failed") from exc

    def close(self) -> None:
        """Dispose database connections and invalidate further actions."""
        self._engine.dispose()
        self._initialized = False

    def _bind_confirmation(
        self,
        transaction_id: UUID,
        confirmation_id: UUID,
        *,
        runtime: bool,
    ) -> None:
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                row = session.get(ProcessActionTransactionRow, str(transaction_id))
                if row is None:
                    raise ProcessActionStoreError("Unknown process action transaction")
                if runtime:
                    row.runtime_confirmation_id = str(confirmation_id)
                else:
                    row.plan_confirmation_id = str(confirmation_id)
        except SQLAlchemyError as exc:
            raise ProcessActionStoreError("Process confirmation binding failed") from exc

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise ProcessActionStoreError("Process action repository is not initialized")


class ProcessExecutionGuard:
    """Registry guard backed by one durable process action transaction."""

    def __init__(self, repository: ProcessActionRepository) -> None:
        self._repository = repository

    def require(
        self,
        authorization: ExecutionAuthorization,
        tool_name: str,
        arguments: Mapping[str, JsonValue],
    ) -> None:
        """Delegate exact capability validation to the process action journal."""
        self._repository.require_execution_authorization(authorization, tool_name, arguments)


def _to_row(
    value: ProcessActionTransaction,
    tool_name: str,
    argument_digest: str,
) -> ProcessActionTransactionRow:
    return ProcessActionTransactionRow(
        transaction_id=str(value.transaction_id),
        operation_id=str(value.operation_id),
        plan_id=str(value.plan_id),
        preview_id=str(value.preview_id),
        parent_transaction_id=(
            str(value.parent_transaction_id) if value.parent_transaction_id else None
        ),
        action=value.action.value,
        state=value.state.value,
        plan_digest=value.plan_digest,
        preview_digest=value.preview_digest,
        target_set_digest=value.target_set_digest,
        tool_name=tool_name,
        arguments_digest=argument_digest,
        process_count=value.process_count,
        plan_confirmation_id=None,
        runtime_confirmation_id=None,
        created_at=value.created_at,
        updated_at=value.updated_at,
        error_code=value.error_code.value if value.error_code else None,
        error_message=value.error_message,
        result=value.result,
    )


def _from_row(row: ProcessActionTransactionRow) -> ProcessActionTransaction:
    return ProcessActionTransaction(
        transaction_id=row.transaction_id,
        operation_id=row.operation_id,
        plan_id=row.plan_id,
        preview_id=row.preview_id,
        parent_transaction_id=row.parent_transaction_id,
        action=ProcessActionType(row.action),
        state=ProcessActionState(row.state),
        plan_digest=row.plan_digest,
        preview_digest=row.preview_digest,
        target_set_digest=row.target_set_digest,
        process_count=row.process_count,
        plan_confirmation_id=row.plan_confirmation_id,
        runtime_confirmation_id=row.runtime_confirmation_id,
        created_at=_as_utc(row.created_at),
        updated_at=_as_utc(row.updated_at),
        error_code=ProcessActionErrorCode(row.error_code) if row.error_code else None,
        error_message=row.error_message,
        result=row.result,
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
