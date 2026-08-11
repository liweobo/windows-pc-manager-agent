"""SQLite write-ahead journal for Stage 2A transactions and Undo records."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from pc_manager_agent.domain.file_operations import (
    FileOperationPlan,
    FileOperationPreview,
    FileState,
    PlannedFileOperation,
    PreviewItemStatus,
)
from pc_manager_agent.domain.transactions import (
    FailurePolicy,
    OperationItemState,
    OperationTransaction,
    TransactionItem,
    TransactionState,
)
from pc_manager_agent.persistence.database import create_sqlite_engine
from pc_manager_agent.rollback.models import UndoRecord, UndoStatus
from pc_manager_agent.tools.execution import (
    ExecutionAuthorization,
    WriteExecutionGuard,
    arguments_digest,
)


class OperationStoreError(RuntimeError):
    """Raised when the mutation journal cannot be trusted."""


class OperationBase(DeclarativeBase):
    """Declarative base isolated from analysis, authorization, and audit tables."""


class OperationTransactionRow(OperationBase):
    """Internal durable transaction summary."""

    __tablename__ = "operation_transactions"

    transaction_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(36), index=True)
    preview_id: Mapped[str] = mapped_column(String(36), unique=True)
    plan_digest: Mapped[str] = mapped_column(String(64))
    preview_digest: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(40), index=True)
    failure_policy: Mapped[str] = mapped_column(String(50))
    operation_count: Mapped[int] = mapped_column(Integer)
    ready_count: Mapped[int] = mapped_column(Integer)
    completed_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    confirmation_id: Mapped[str | None] = mapped_column(String(36))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)


class OperationItemRow(OperationBase):
    """Internal durable operation item and exact argument reservation."""

    __tablename__ = "operation_items"
    __table_args__ = (
        UniqueConstraint("transaction_id", "sequence", name="uq_operation_sequence"),
        UniqueConstraint("transaction_id", "destination", name="uq_operation_destination"),
    )

    operation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    transaction_id: Mapped[str] = mapped_column(
        ForeignKey("operation_transactions.transaction_id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    operation_type: Mapped[str] = mapped_column(String(40))
    tool_name: Mapped[str] = mapped_column(String(120))
    source: Mapped[str | None] = mapped_column(String(2_048))
    destination: Mapped[str] = mapped_column(String(2_048))
    state: Mapped[str] = mapped_column(String(30), index=True)
    arguments_digest: Mapped[str] = mapped_column(String(64))
    rollback_tool_name: Mapped[str | None] = mapped_column(String(120))
    rollback_arguments_digest: Mapped[str | None] = mapped_column(String(64))
    operation_data: Mapped[dict[str, Any]] = mapped_column(JSON)
    preview_data: Mapped[dict[str, Any]] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UndoRecordRow(OperationBase):
    """Internal write-ahead Undo payload for one operation."""

    __tablename__ = "operation_undo_records"

    undo_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    operation_id: Mapped[str] = mapped_column(
        ForeignKey("operation_items.operation_id", ondelete="CASCADE"), unique=True
    )
    transaction_id: Mapped[str] = mapped_column(String(36), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), index=True)
    record_digest: Mapped[str] = mapped_column(String(64))
    undo_data: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


_ALLOWED_TRANSITIONS: dict[TransactionState, frozenset[TransactionState]] = {
    TransactionState.PREVIEWED: frozenset({TransactionState.AWAITING_CONFIRMATION}),
    TransactionState.AWAITING_CONFIRMATION: frozenset(
        {TransactionState.CONFIRMED, TransactionState.CANCELLED}
    ),
    TransactionState.CONFIRMED: frozenset({TransactionState.RUNNING, TransactionState.CANCELLED}),
    TransactionState.RUNNING: frozenset(
        {
            TransactionState.COMPLETED,
            TransactionState.PARTIALLY_COMPLETED,
            TransactionState.FAILED,
            TransactionState.CANCELLED,
            TransactionState.INTERRUPTED,
        }
    ),
    TransactionState.COMPLETED: frozenset({TransactionState.ROLLING_BACK}),
    TransactionState.PARTIALLY_COMPLETED: frozenset({TransactionState.ROLLING_BACK}),
    TransactionState.FAILED: frozenset({TransactionState.ROLLING_BACK}),
    TransactionState.CANCELLED: frozenset({TransactionState.ROLLING_BACK}),
    TransactionState.INTERRUPTED: frozenset({TransactionState.ROLLING_BACK}),
    TransactionState.PARTIALLY_ROLLED_BACK: frozenset({TransactionState.ROLLING_BACK}),
    TransactionState.ROLLBACK_FAILED: frozenset({TransactionState.ROLLING_BACK}),
    TransactionState.ROLLING_BACK: frozenset(
        {
            TransactionState.ROLLED_BACK,
            TransactionState.PARTIALLY_ROLLED_BACK,
            TransactionState.ROLLBACK_FAILED,
            TransactionState.INTERRUPTED,
        }
    ),
}


class OperationRepository:
    """Persist mutation intent before execution and every observed state transition."""

    def __init__(self, database_path: Path) -> None:
        self._engine = create_sqlite_engine(database_path)
        self._sessions = sessionmaker(self._engine, expire_on_commit=False)
        self._initialized = False

    def initialize(self) -> tuple[UUID, ...]:
        """Create additive tables and mark stale running transactions interrupted."""
        try:
            OperationBase.metadata.create_all(self._engine)
            with self._sessions.begin() as session:
                rows = tuple(
                    session.scalars(
                        select(OperationTransactionRow).where(
                            OperationTransactionRow.state.in_(
                                (
                                    TransactionState.PREVIEWED.value,
                                    TransactionState.AWAITING_CONFIRMATION.value,
                                    TransactionState.CONFIRMED.value,
                                    TransactionState.RUNNING.value,
                                    TransactionState.ROLLING_BACK.value,
                                )
                            )
                        )
                    )
                )
                current = datetime.now(UTC)
                interrupted: list[UUID] = []
                for row in rows:
                    if row.state in {
                        TransactionState.RUNNING.value,
                        TransactionState.ROLLING_BACK.value,
                    }:
                        row.state = TransactionState.INTERRUPTED.value
                        interrupted.append(UUID(row.transaction_id))
                    else:
                        # Preview/confirmation tokens are intentionally memory-only and
                        # cannot be trusted after restart. Cancel them rather than leave a
                        # durable transaction that could appear executable.
                        row.state = TransactionState.CANCELLED.value
                    row.updated_at = current
        except SQLAlchemyError as exc:
            raise OperationStoreError("Operation journal initialization failed") from exc
        self._initialized = True
        return tuple(interrupted)

    def create_from_preview(
        self,
        plan: FileOperationPlan,
        preview: FileOperationPreview,
        argument_payloads: Mapping[UUID, Mapping[str, JsonValue]],
    ) -> OperationTransaction:
        """Persist one immutable Preview and all destination reservations atomically."""
        self._require_initialized()
        if preview.plan_id != plan.plan_id or preview.plan_digest != plan.canonical_digest():
            raise OperationStoreError("Cannot persist a Preview that does not match its plan")
        operation_by_id = {operation.operation_id: operation for operation in plan.operations}
        if set(operation_by_id) != set(argument_payloads):
            raise OperationStoreError("Tool argument reservations do not match all operations")
        transaction = OperationTransaction(
            transaction_id=preview.transaction_id,
            plan_id=plan.plan_id,
            preview_id=preview.preview_id,
            plan_digest=plan.canonical_digest(),
            preview_digest=preview.canonical_digest(),
            state=TransactionState.PREVIEWED,
            operation_count=len(preview.items),
            ready_count=preview.ready_count,
            skipped_count=preview.conflict_count + preview.blocked_count,
        )
        row = self._transaction_to_row(transaction)
        item_rows: list[OperationItemRow] = []
        for preview_item in preview.items:
            operation = operation_by_id[preview_item.operation_id]
            payload = argument_payloads[operation.operation_id]
            state = (
                OperationItemState.PENDING
                if preview_item.status is PreviewItemStatus.READY
                else OperationItemState.SKIPPED
            )
            item_rows.append(
                OperationItemRow(
                    operation_id=str(operation.operation_id),
                    transaction_id=str(preview.transaction_id),
                    sequence=operation.sequence,
                    operation_type=operation.operation_type.value,
                    tool_name=operation.tool_name,
                    source=str(operation.source) if operation.source is not None else None,
                    destination=str(operation.destination),
                    state=state.value,
                    arguments_digest=arguments_digest(payload),
                    rollback_tool_name=None,
                    rollback_arguments_digest=None,
                    operation_data=operation.model_dump(mode="json"),
                    preview_data=preview_item.model_dump(mode="json"),
                    error_code=None,
                    error_message=None,
                    started_at=None,
                    completed_at=None,
                )
            )
        try:
            with self._sessions.begin() as session:
                session.add(row)
                # The rows do not expose an ORM relationship because the public model is
                # immutable. Flush the parent explicitly so SQLite can enforce the foreign
                # key while the complete Preview still commits atomically.
                session.flush()
                session.add_all(item_rows)
        except IntegrityError as exc:
            raise OperationStoreError(
                "Operation destination or identity reservation collided"
            ) from exc
        except SQLAlchemyError as exc:
            raise OperationStoreError("Operation Preview persistence failed") from exc
        return transaction

    def transition(
        self,
        transaction_id: UUID,
        new_state: TransactionState,
        *,
        confirmation_id: UUID | None = None,
        confirmed_at: datetime | None = None,
        error_message: str | None = None,
    ) -> OperationTransaction:
        """Apply one allowed transaction transition with compare-current semantics."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                row = session.get(OperationTransactionRow, str(transaction_id))
                if row is None:
                    raise OperationStoreError("Unknown operation transaction")
                current = TransactionState(row.state)
                if new_state not in _ALLOWED_TRANSITIONS.get(current, frozenset()):
                    raise OperationStoreError(
                        f"Invalid transaction transition: {current.value} -> {new_state.value}"
                    )
                row.state = new_state.value
                row.updated_at = datetime.now(UTC)
                if confirmation_id is not None:
                    row.confirmation_id = str(confirmation_id)
                if confirmed_at is not None:
                    row.confirmed_at = confirmed_at
                if error_message is not None:
                    row.error_message = error_message
        except OperationStoreError:
            raise
        except SQLAlchemyError as exc:
            raise OperationStoreError("Transaction state update failed") from exc
        return self.get_transaction(transaction_id)

    def begin_operation(
        self,
        transaction_id: UUID,
        operation_id: UUID,
        undo_record: UndoRecord,
    ) -> TransactionItem:
        """Mark one item RUNNING and persist PREPARED Undo before filesystem mutation."""
        self._require_initialized()
        if undo_record.transaction_id != transaction_id or undo_record.operation_id != operation_id:
            raise OperationStoreError("Undo record does not match the operation item")
        try:
            with self._sessions.begin() as session:
                transaction = session.get(OperationTransactionRow, str(transaction_id))
                item = session.get(OperationItemRow, str(operation_id))
                if (
                    transaction is None
                    or item is None
                    or item.transaction_id != str(transaction_id)
                ):
                    raise OperationStoreError("Unknown transaction operation item")
                if transaction.state != TransactionState.RUNNING.value:
                    raise OperationStoreError("Transaction is not RUNNING")
                if item.state != OperationItemState.PENDING.value:
                    raise OperationStoreError("Operation item is not PENDING")
                item.state = OperationItemState.RUNNING.value
                item.started_at = datetime.now(UTC)
                session.add(
                    UndoRecordRow(
                        undo_id=str(undo_record.undo_id),
                        operation_id=str(operation_id),
                        transaction_id=str(transaction_id),
                        sequence=undo_record.sequence,
                        status=undo_record.status.value,
                        record_digest=undo_record.canonical_digest(),
                        undo_data=undo_record.model_dump(mode="json"),
                        created_at=undo_record.created_at,
                    )
                )
        except OperationStoreError:
            raise
        except IntegrityError as exc:
            raise OperationStoreError("Undo write-ahead record already exists") from exc
        except SQLAlchemyError as exc:
            raise OperationStoreError("Operation write-ahead persistence failed") from exc
        return self.get_item(operation_id)

    def complete_operation(
        self,
        operation_id: UUID,
        after_state: FileState,
    ) -> tuple[TransactionItem, UndoRecord]:
        """Atomically finalize item success and make its Undo record available."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                item = session.get(OperationItemRow, str(operation_id))
                if item is None or item.state != OperationItemState.RUNNING.value:
                    raise OperationStoreError("Operation item is not RUNNING")
                undo_row = session.scalar(
                    select(UndoRecordRow).where(UndoRecordRow.operation_id == str(operation_id))
                )
                if undo_row is None:
                    raise OperationStoreError("Prepared Undo record is missing")
                undo = UndoRecord.model_validate(undo_row.undo_data)
                available = undo.model_copy(
                    update={"after_state": after_state, "status": UndoStatus.AVAILABLE}
                )
                item.state = OperationItemState.COMPLETED.value
                item.completed_at = datetime.now(UTC)
                undo_row.status = available.status.value
                undo_row.undo_data = available.model_dump(mode="json")
                undo_row.record_digest = available.canonical_digest()
                transaction = session.get(OperationTransactionRow, item.transaction_id)
                if transaction is None:
                    raise OperationStoreError("Operation transaction is missing")
                transaction.completed_count += 1
                transaction.updated_at = datetime.now(UTC)
        except OperationStoreError:
            raise
        except SQLAlchemyError as exc:
            raise OperationStoreError("Operation completion persistence failed") from exc
        return self.get_item(operation_id), self.get_undo(operation_id)

    def fail_operation(
        self,
        operation_id: UUID,
        *,
        error_code: str,
        error_message: str,
    ) -> TransactionItem:
        """Persist an explicit item failure and increment the durable failure count."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                item = session.get(OperationItemRow, str(operation_id))
                if item is None or item.state != OperationItemState.RUNNING.value:
                    raise OperationStoreError("Operation item is not RUNNING")
                item.state = OperationItemState.FAILED.value
                item.error_code = error_code
                item.error_message = error_message
                item.completed_at = datetime.now(UTC)
                transaction = session.get(OperationTransactionRow, item.transaction_id)
                if transaction is None:
                    raise OperationStoreError("Operation transaction is missing")
                transaction.failed_count += 1
                transaction.updated_at = datetime.now(UTC)
        except OperationStoreError:
            raise
        except SQLAlchemyError as exc:
            raise OperationStoreError("Operation failure persistence failed") from exc
        return self.get_item(operation_id)

    def mark_item_rolled_back(
        self,
        operation_id: UUID,
        *,
        success: bool,
        result: str,
    ) -> None:
        """Finalize one rollback item and its Undo record together."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                item = session.get(OperationItemRow, str(operation_id))
                undo_row = session.scalar(
                    select(UndoRecordRow).where(UndoRecordRow.operation_id == str(operation_id))
                )
                if item is None or undo_row is None:
                    raise OperationStoreError("Rollback operation or Undo record is missing")
                if item.state != OperationItemState.ROLLING_BACK.value:
                    raise OperationStoreError("Operation item is not ROLLING_BACK")
                undo = UndoRecord.model_validate(undo_row.undo_data)
                status = UndoStatus.ROLLED_BACK if success else UndoStatus.ROLLBACK_FAILED
                updated = undo.model_copy(update={"status": status, "rollback_result": result})
                item.state = (
                    OperationItemState.ROLLED_BACK.value
                    if success
                    else OperationItemState.ROLLBACK_FAILED.value
                )
                undo_row.status = status.value
                undo_row.undo_data = updated.model_dump(mode="json")
                undo_row.record_digest = updated.canonical_digest()
        except OperationStoreError:
            raise
        except SQLAlchemyError as exc:
            raise OperationStoreError("Rollback result persistence failed") from exc

    def begin_rollback_operation(
        self,
        operation_id: UUID,
        *,
        tool_name: str,
        argument_payload: Mapping[str, JsonValue],
    ) -> TransactionItem:
        """Reserve exact reverse arguments before invoking a registered rollback tool."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                item = session.get(OperationItemRow, str(operation_id))
                if item is None:
                    raise OperationStoreError("Unknown rollback operation item")
                transaction = session.get(OperationTransactionRow, item.transaction_id)
                if transaction is None or transaction.state != TransactionState.ROLLING_BACK.value:
                    raise OperationStoreError("Transaction is not ROLLING_BACK")
                if item.state not in {
                    OperationItemState.COMPLETED.value,
                    OperationItemState.RUNNING.value,
                    OperationItemState.ROLLBACK_FAILED.value,
                }:
                    raise OperationStoreError("Operation item cannot enter rollback")
                item.state = OperationItemState.ROLLING_BACK.value
                item.rollback_tool_name = tool_name
                item.rollback_arguments_digest = arguments_digest(argument_payload)
        except OperationStoreError:
            raise
        except SQLAlchemyError as exc:
            raise OperationStoreError("Rollback argument reservation failed") from exc
        return self.get_item(operation_id)

    def get_transaction(self, transaction_id: UUID) -> OperationTransaction:
        """Return one durable transaction or fail closed."""
        self._require_initialized()
        try:
            with self._sessions() as session:
                row = session.get(OperationTransactionRow, str(transaction_id))
        except SQLAlchemyError as exc:
            raise OperationStoreError("Transaction lookup failed") from exc
        if row is None:
            raise OperationStoreError("Unknown operation transaction")
        return self._row_to_transaction(row)

    def get_item(self, operation_id: UUID) -> TransactionItem:
        """Return one durable operation item or fail closed."""
        self._require_initialized()
        try:
            with self._sessions() as session:
                row = session.get(OperationItemRow, str(operation_id))
        except SQLAlchemyError as exc:
            raise OperationStoreError("Operation item lookup failed") from exc
        if row is None:
            raise OperationStoreError("Unknown operation item")
        return self._row_to_item(row)

    def get_operation(self, operation_id: UUID) -> PlannedFileOperation:
        """Return the exact immutable operation persisted with its Preview."""
        self._require_initialized()
        try:
            with self._sessions() as session:
                row = session.get(OperationItemRow, str(operation_id))
        except SQLAlchemyError as exc:
            raise OperationStoreError("Operation payload lookup failed") from exc
        if row is None:
            raise OperationStoreError("Unknown operation item")
        return PlannedFileOperation.model_validate(row.operation_data)

    def list_items(self, transaction_id: UUID) -> tuple[TransactionItem, ...]:
        """List transaction items in original execution order."""
        self._require_initialized()
        statement = (
            select(OperationItemRow)
            .where(OperationItemRow.transaction_id == str(transaction_id))
            .order_by(OperationItemRow.sequence)
        )
        try:
            with self._sessions() as session:
                rows = tuple(session.scalars(statement))
        except SQLAlchemyError as exc:
            raise OperationStoreError("Operation item query failed") from exc
        return tuple(self._row_to_item(row) for row in rows)

    def list_recent(self, limit: int = 100) -> tuple[OperationTransaction, ...]:
        """Return newest mutation transactions with a strict upper bound."""
        self._require_initialized()
        bounded = max(1, min(limit, 500))
        statement = (
            select(OperationTransactionRow)
            .order_by(OperationTransactionRow.created_at.desc())
            .limit(bounded)
        )
        try:
            with self._sessions() as session:
                rows = tuple(session.scalars(statement))
        except SQLAlchemyError as exc:
            raise OperationStoreError("Operation history query failed") from exc
        return tuple(self._row_to_transaction(row) for row in rows)

    def list_undo(self, transaction_id: UUID) -> tuple[UndoRecord, ...]:
        """Return integrity-checked Undo records in reverse execution order."""
        self._require_initialized()
        statement = (
            select(UndoRecordRow)
            .where(UndoRecordRow.transaction_id == str(transaction_id))
            .order_by(UndoRecordRow.sequence.desc())
        )
        try:
            with self._sessions() as session:
                rows = tuple(session.scalars(statement))
        except SQLAlchemyError as exc:
            raise OperationStoreError("Undo query failed") from exc
        return tuple(self._validate_undo_row(row) for row in rows)

    def get_undo(self, operation_id: UUID) -> UndoRecord:
        """Return one integrity-checked Undo record."""
        self._require_initialized()
        statement = select(UndoRecordRow).where(UndoRecordRow.operation_id == str(operation_id))
        try:
            with self._sessions() as session:
                row = session.scalar(statement)
        except SQLAlchemyError as exc:
            raise OperationStoreError("Undo lookup failed") from exc
        if row is None:
            raise OperationStoreError("Undo record is missing")
        return self._validate_undo_row(row)

    def require_execution_authorization(
        self,
        authorization: ExecutionAuthorization,
        tool_name: str,
        argument_payload: Mapping[str, JsonValue],
    ) -> None:
        """Validate an R1 capability against exact durable transaction and item state."""
        transaction = self.get_transaction(authorization.transaction_id)
        item = self.get_item(authorization.operation_id)
        forward = (
            transaction.state is TransactionState.RUNNING
            and item.state is OperationItemState.RUNNING
        )
        rollback = (
            transaction.state is TransactionState.ROLLING_BACK
            and item.state is OperationItemState.ROLLING_BACK
        )
        if (
            not (forward or rollback)
            or item.transaction_id != authorization.transaction_id
            or authorization.tool_name != tool_name
            or transaction.plan_id != authorization.plan_id
            or transaction.preview_id != authorization.preview_id
            or authorization.arguments_digest != arguments_digest(argument_payload)
        ):
            raise OperationStoreError("Write execution authorization is stale or mismatched")
        try:
            with self._sessions() as session:
                row = session.get(OperationItemRow, str(authorization.operation_id))
                expected_digest = None
                expected_tool = None
                if row is not None:
                    if forward:
                        expected_digest = row.arguments_digest
                        expected_tool = row.tool_name
                    else:
                        expected_digest = row.rollback_arguments_digest
                        expected_tool = row.rollback_tool_name
                if (
                    row is None
                    or expected_digest != authorization.arguments_digest
                    or expected_tool != tool_name
                ):
                    raise OperationStoreError("Write argument reservation changed")
        except SQLAlchemyError as exc:
            raise OperationStoreError("Write authorization lookup failed") from exc

    def close(self) -> None:
        """Dispose journal connections and invalidate future operations."""
        self._engine.dispose()
        self._initialized = False

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise OperationStoreError("Operation repository is not initialized")

    @staticmethod
    def _transaction_to_row(value: OperationTransaction) -> OperationTransactionRow:
        return OperationTransactionRow(
            transaction_id=str(value.transaction_id),
            plan_id=str(value.plan_id),
            preview_id=str(value.preview_id),
            plan_digest=value.plan_digest,
            preview_digest=value.preview_digest,
            state=value.state.value,
            failure_policy=value.failure_policy.value,
            operation_count=value.operation_count,
            ready_count=value.ready_count,
            completed_count=value.completed_count,
            failed_count=value.failed_count,
            skipped_count=value.skipped_count,
            created_at=value.created_at,
            updated_at=value.updated_at,
            confirmation_id=str(value.confirmation_id) if value.confirmation_id else None,
            confirmed_at=value.confirmed_at,
            error_message=value.error_message,
        )

    @staticmethod
    def _row_to_transaction(row: OperationTransactionRow) -> OperationTransaction:
        return OperationTransaction(
            transaction_id=row.transaction_id,
            plan_id=row.plan_id,
            preview_id=row.preview_id,
            plan_digest=row.plan_digest,
            preview_digest=row.preview_digest,
            state=row.state,
            failure_policy=FailurePolicy(row.failure_policy),
            operation_count=row.operation_count,
            ready_count=row.ready_count,
            completed_count=row.completed_count,
            failed_count=row.failed_count,
            skipped_count=row.skipped_count,
            created_at=_as_utc(row.created_at),
            updated_at=_as_utc(row.updated_at),
            confirmation_id=row.confirmation_id,
            confirmed_at=_as_utc(row.confirmed_at) if row.confirmed_at else None,
            error_message=row.error_message,
        )

    @staticmethod
    def _row_to_item(row: OperationItemRow) -> TransactionItem:
        return TransactionItem(
            transaction_id=row.transaction_id,
            operation_id=row.operation_id,
            sequence=row.sequence,
            operation_type=row.operation_type,
            tool_name=row.tool_name,
            source=Path(row.source) if row.source else None,
            destination=Path(row.destination),
            state=row.state,
            error_code=row.error_code,
            error_message=row.error_message,
            started_at=_as_utc(row.started_at) if row.started_at else None,
            completed_at=_as_utc(row.completed_at) if row.completed_at else None,
        )

    @staticmethod
    def _validate_undo_row(row: UndoRecordRow) -> UndoRecord:
        record = UndoRecord.model_validate(row.undo_data)
        if record.canonical_digest() != row.record_digest:
            raise OperationStoreError("Undo record integrity check failed")
        return record


class TransactionExecutionGuard(WriteExecutionGuard):
    """Registry guard backed by durable RUNNING transaction state."""

    def __init__(self, repository: OperationRepository) -> None:
        self._repository = repository

    def require(
        self,
        authorization: ExecutionAuthorization,
        tool_name: str,
        arguments: Mapping[str, JsonValue],
    ) -> None:
        """Delegate exact capability validation to the trusted operation journal."""
        self._repository.require_execution_authorization(authorization, tool_name, arguments)


def _as_utc(value: datetime) -> datetime:
    """Restore UTC timezone metadata omitted by SQLite."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
