"""Durable Stage 4E2 plans, approvals, item results, recovery, and write guard."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from pc_manager_agent.confirmation.system_cleanup import (
    SystemCleanupConfirmation,
    SystemCleanupConfirmationState,
    SystemCleanupConfirmationTier,
)
from pc_manager_agent.domain.system_cleanup_execution import (
    CleanupExecutionPlan,
    CleanupExecutionPreview,
    CleanupIrreversibilityRecord,
    CleanupItemResult,
    CleanupItemState,
    CleanupTransactionState,
    PlannedCleanupItem,
    RecycleBinEmptyPlan,
    RecycleBinEmptyPreview,
    RecycleBinEmptyRequest,
    RecycleBinEmptyResult,
    SystemCleanupTrashRequest,
)
from pc_manager_agent.persistence.database import create_sqlite_engine
from pc_manager_agent.recovery.models import TrashRecoveryRecord
from pc_manager_agent.tools.execution import (
    ExecutionAuthorization,
    WriteExecutionGuard,
    arguments_digest,
)
from pc_manager_agent.tools.registry import WriteAuthorizationError


class SystemCleanupStoreError(RuntimeError):
    """Raised when durable cleanup authorization cannot be trusted."""


class SystemCleanupBase(DeclarativeBase):
    """Declarative base isolated from report-only and other writer tables."""


class SystemCleanupTransactionRow(SystemCleanupBase):
    """One ordinary or Bin-empty transaction with immutable payloads."""

    __tablename__ = "system_cleanup_transactions"

    transaction_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    transaction_kind: Mapped[str] = mapped_column(String(30), index=True)
    plan_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    preview_id: Mapped[str] = mapped_column(String(36), index=True)
    operation_id: Mapped[str | None] = mapped_column(String(36), unique=True, index=True)
    source_report_id: Mapped[str | None] = mapped_column(String(36), index=True)
    state: Mapped[str] = mapped_column(String(50), index=True)
    risk_level: Mapped[str] = mapped_column(String(30))
    plan_digest: Mapped[str] = mapped_column(String(64))
    preview_digest: Mapped[str] = mapped_column(String(64))
    item_set_digest: Mapped[str] = mapped_column(String(64))
    arguments_digest: Mapped[str | None] = mapped_column(String(64))
    runtime_confirmation_id: Mapped[str | None] = mapped_column(String(36), index=True)
    plan_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    preview_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    result_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)


class SystemCleanupItemRow(SystemCleanupBase):
    """One ordered item and its exact reference-only tool capability."""

    __tablename__ = "system_cleanup_items"

    item_ref: Mapped[str] = mapped_column(String(36), primary_key=True)
    transaction_id: Mapped[str] = mapped_column(
        ForeignKey("system_cleanup_transactions.transaction_id"), index=True
    )
    operation_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    source_candidate_id: Mapped[str] = mapped_column(String(36), index=True)
    state: Mapped[str] = mapped_column(String(50), index=True)
    tool_name: Mapped[str] = mapped_column(String(120))
    arguments_digest: Mapped[str] = mapped_column(String(64))
    item_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    result_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    recovery_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    recovery_digest: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SystemCleanupConfirmationRow(SystemCleanupBase):
    """One durable digest-bound plan or runtime approval."""

    __tablename__ = "system_cleanup_confirmations"

    confirmation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    transaction_id: Mapped[str] = mapped_column(String(36), index=True)
    parent_confirmation_id: Mapped[str | None] = mapped_column(String(36), index=True)
    tier: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(20), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class SystemCleanupIrreversibilityRow(SystemCleanupBase):
    """Evidence for one Bin empty action; deliberately not a recovery record."""

    __tablename__ = "system_cleanup_irreversibility"

    record_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    transaction_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


_TERMINAL_STATES = frozenset(
    {
        CleanupTransactionState.COMPLETED,
        CleanupTransactionState.PARTIALLY_COMPLETED,
        CleanupTransactionState.FAILED,
        CleanupTransactionState.CANCELLED,
        CleanupTransactionState.INTERRUPTED,
        CleanupTransactionState.BLOCKED,
    }
)


class SystemCleanupRepository:
    """Persist non-resumable Stage 4E2 authorities and truthful outcome evidence."""

    def __init__(self, database_path: Path) -> None:
        self._engine = create_sqlite_engine(database_path)
        self._sessions = sessionmaker(self._engine, expire_on_commit=False)
        self._initialized = False

    def initialize(self) -> tuple[UUID, ...]:
        """Create tables, expire approvals, and interrupt abandoned transactions."""
        interrupted: list[UUID] = []
        try:
            SystemCleanupBase.metadata.create_all(self._engine)
            now = datetime.now(UTC)
            with self._sessions.begin() as session:
                for row in session.scalars(select(SystemCleanupTransactionRow)):
                    state = CleanupTransactionState(row.state)
                    if state not in _TERMINAL_STATES:
                        interrupted.append(UUID(row.transaction_id))
                        row.state = CleanupTransactionState.INTERRUPTED.value
                        row.error_message = (
                            "Application restarted; controlled cleanup will not continue "
                            "automatically. Run Fresh analysis and confirm again."
                        )
                        row.updated_at = now
                for confirmation_row in session.scalars(select(SystemCleanupConfirmationRow)):
                    if confirmation_row.state in {
                        SystemCleanupConfirmationState.PENDING.value,
                        SystemCleanupConfirmationState.APPROVED.value,
                    }:
                        payload = dict(confirmation_row.payload)
                        payload["state"] = SystemCleanupConfirmationState.EXPIRED.value
                        confirmation_row.state = SystemCleanupConfirmationState.EXPIRED.value
                        confirmation_row.payload = payload
        except SQLAlchemyError as exc:
            raise SystemCleanupStoreError(
                "System cleanup repository initialization failed"
            ) from exc
        self._initialized = True
        return tuple(interrupted)

    def create_item_cleanup(
        self,
        plan: CleanupExecutionPlan,
        preview: CleanupExecutionPreview,
    ) -> None:
        """Atomically reserve one exact ordinary plan and all item capabilities."""
        self._require_initialized()
        now = datetime.now(UTC)
        try:
            with self._sessions.begin() as session:
                self._require_no_active(session)
                session.add(
                    SystemCleanupTransactionRow(
                        transaction_id=str(plan.transaction_id),
                        transaction_kind="ITEM_CLEANUP",
                        plan_id=str(plan.plan_id),
                        preview_id=str(preview.preview_id),
                        operation_id=None,
                        source_report_id=str(plan.source_report_id),
                        state=CleanupTransactionState.PREVIEWED.value,
                        risk_level=plan.risk_level.value,
                        plan_digest=plan.canonical_digest(),
                        preview_digest=preview.canonical_digest(),
                        item_set_digest=preview.item_set_digest,
                        arguments_digest=None,
                        runtime_confirmation_id=None,
                        plan_payload=plan.model_dump(mode="json"),
                        preview_payload=preview.model_dump(mode="json"),
                        result_payload=None,
                        created_at=now,
                        updated_at=now,
                        error_message=None,
                    )
                )
                session.flush()
                session.add_all(
                    self._item_row(plan, preview.preview_id, item, now) for item in plan.items
                )
        except SystemCleanupStoreError:
            raise
        except SQLAlchemyError as exc:
            raise SystemCleanupStoreError("System cleanup plan persistence failed") from exc

    def create_empty(
        self,
        plan: RecycleBinEmptyPlan,
        preview: RecycleBinEmptyPreview,
    ) -> None:
        """Reserve a completely independent irreversible Bin-empty transaction."""
        self._require_initialized()
        now = datetime.now(UTC)
        request = self.request_for_empty(plan, preview.preview_id)
        try:
            with self._sessions.begin() as session:
                self._require_no_active(session)
                session.add(
                    SystemCleanupTransactionRow(
                        transaction_id=str(plan.transaction_id),
                        transaction_kind="RECYCLE_BIN_EMPTY",
                        plan_id=str(plan.plan_id),
                        preview_id=str(preview.preview_id),
                        operation_id=str(plan.operation_id),
                        source_report_id=None,
                        state=CleanupTransactionState.PREVIEWED.value,
                        risk_level=plan.risk_level.value,
                        plan_digest=plan.canonical_digest(),
                        preview_digest=preview.canonical_digest(),
                        item_set_digest=plan.snapshot_digest,
                        arguments_digest=arguments_digest(request.model_dump(mode="json")),
                        runtime_confirmation_id=None,
                        plan_payload=plan.model_dump(mode="json"),
                        preview_payload=preview.model_dump(mode="json"),
                        result_payload=None,
                        created_at=now,
                        updated_at=now,
                        error_message=None,
                    )
                )
        except SystemCleanupStoreError:
            raise
        except SQLAlchemyError as exc:
            raise SystemCleanupStoreError("Recycle Bin empty plan persistence failed") from exc

    def bind_runtime_preview(
        self,
        plan: CleanupExecutionPlan,
        preview: CleanupExecutionPreview,
    ) -> None:
        """Bind a newly revalidated item Preview before immediate confirmation."""
        self._bind_preview(plan.transaction_id, preview, preview.item_set_digest)
        with self._sessions.begin() as session:
            for row in session.scalars(
                select(SystemCleanupItemRow).where(
                    SystemCleanupItemRow.transaction_id == str(plan.transaction_id)
                )
            ):
                item = self._planned_item(row)
                request = self.request_for_item(plan, preview.preview_id, item)
                row.arguments_digest = arguments_digest(request.model_dump(mode="json"))
                row.updated_at = datetime.now(UTC)

    def bind_empty_runtime_preview(
        self,
        plan: RecycleBinEmptyPlan,
        preview: RecycleBinEmptyPreview,
    ) -> None:
        """Bind a new exact-volume snapshot before irreversible confirmation."""
        self._bind_preview(plan.transaction_id, preview, plan.snapshot_digest)
        request = self.request_for_empty(plan, preview.preview_id)
        with self._sessions.begin() as session:
            row = self._transaction(session, plan.transaction_id)
            row.arguments_digest = arguments_digest(request.model_dump(mode="json"))

    def _bind_preview(
        self,
        transaction_id: UUID,
        preview: CleanupExecutionPreview | RecycleBinEmptyPreview,
        item_set_digest: str,
    ) -> None:
        self._require_initialized()
        with self._sessions.begin() as session:
            row = self._transaction(session, transaction_id)
            if row.state != CleanupTransactionState.PLAN_CONFIRMED.value:
                raise SystemCleanupStoreError(
                    "Cleanup transaction is not ready for runtime Preview"
                )
            row.preview_id = str(preview.preview_id)
            row.preview_digest = preview.canonical_digest()
            row.item_set_digest = item_set_digest
            row.preview_payload = preview.model_dump(mode="json")
            row.updated_at = datetime.now(UTC)

    def save_confirmation(self, confirmation: SystemCleanupConfirmation) -> None:
        """Persist one pending confirmation and its waiting transaction state."""
        self._require_initialized()
        with self._sessions.begin() as session:
            session.add(self._confirmation_row(confirmation))
            row = self._transaction(session, confirmation.transaction_id)
            row.state = (
                CleanupTransactionState.AWAITING_PLAN_CONFIRMATION.value
                if confirmation.tier is SystemCleanupConfirmationTier.PLAN
                else CleanupTransactionState.AWAITING_RUNTIME_CONFIRMATION.value
            )
            row.updated_at = datetime.now(UTC)

    def get_confirmation(self, confirmation_id: UUID) -> SystemCleanupConfirmation:
        """Load one exact confirmation."""
        self._require_initialized()
        with self._sessions() as session:
            row = session.get(SystemCleanupConfirmationRow, str(confirmation_id))
            if row is None:
                raise SystemCleanupStoreError("Unknown system cleanup confirmation")
            return SystemCleanupConfirmation.model_validate(row.payload)

    def update_confirmation(self, confirmation: SystemCleanupConfirmation) -> None:
        """Persist approval, rejection, or expiry and update its transaction."""
        self._require_initialized()
        with self._sessions.begin() as session:
            row = session.get(SystemCleanupConfirmationRow, str(confirmation.confirmation_id))
            if row is None:
                raise SystemCleanupStoreError("Unknown system cleanup confirmation")
            row.state = confirmation.state.value
            row.payload = confirmation.model_dump(mode="json")
            transaction = self._transaction(session, confirmation.transaction_id)
            if (
                confirmation.state is SystemCleanupConfirmationState.APPROVED
                and confirmation.tier is SystemCleanupConfirmationTier.PLAN
            ):
                transaction.state = CleanupTransactionState.PLAN_CONFIRMED.value
            elif confirmation.state in {
                SystemCleanupConfirmationState.REJECTED,
                SystemCleanupConfirmationState.EXPIRED,
            }:
                transaction.state = CleanupTransactionState.CANCELLED.value
            transaction.updated_at = datetime.now(UTC)

    def consume_confirmation_pair(
        self,
        plan_confirmation: SystemCleanupConfirmation,
        runtime_confirmation: SystemCleanupConfirmation,
    ) -> None:
        """Atomically consume both approvals and reject every replay."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                rows = (
                    session.get(
                        SystemCleanupConfirmationRow,
                        str(plan_confirmation.confirmation_id),
                    ),
                    session.get(
                        SystemCleanupConfirmationRow,
                        str(runtime_confirmation.confirmation_id),
                    ),
                )
                if any(
                    row is None or row.state != SystemCleanupConfirmationState.APPROVED.value
                    for row in rows
                ):
                    raise SystemCleanupStoreError("Cleanup confirmation replay or mismatch")
                for row in rows:
                    if row is None:
                        raise SystemCleanupStoreError("Cleanup confirmation disappeared")
                    payload = dict(row.payload)
                    payload["state"] = SystemCleanupConfirmationState.CONSUMED.value
                    row.state = SystemCleanupConfirmationState.CONSUMED.value
                    row.payload = payload
                transaction = self._transaction(session, runtime_confirmation.transaction_id)
                if transaction.state != CleanupTransactionState.AWAITING_RUNTIME_CONFIRMATION.value:
                    raise SystemCleanupStoreError("Cleanup transaction is not dispatchable")
                transaction.runtime_confirmation_id = str(runtime_confirmation.confirmation_id)
                transaction.state = CleanupTransactionState.CONFIRMED.value
                transaction.updated_at = datetime.now(UTC)
        except SystemCleanupStoreError:
            raise
        except SQLAlchemyError as exc:
            raise SystemCleanupStoreError("Cleanup approval consumption failed") from exc

    def begin_item_validation(self, transaction_id: UUID, item_ref: UUID) -> None:
        """Mark one pending item validating before its final TOCTOU gate."""
        self._require_initialized()
        with self._sessions.begin() as session:
            transaction = self._transaction(session, transaction_id)
            if transaction.state not in {
                CleanupTransactionState.CONFIRMED.value,
                CleanupTransactionState.EXECUTING.value,
            }:
                raise SystemCleanupStoreError("Cleanup batch is not executable")
            item = self._item(session, item_ref)
            if (
                item.transaction_id != str(transaction_id)
                or item.state != CleanupItemState.PENDING.value
            ):
                raise SystemCleanupStoreError("Cleanup item is not pending")
            item.state = CleanupItemState.VALIDATING.value
            item.updated_at = datetime.now(UTC)

    def begin_empty_validation(self, transaction_id: UUID) -> None:
        """Reserve final validation for one independently confirmed empty action."""
        self.transition(transaction_id, CleanupTransactionState.VALIDATING)

    def mark_item_verifying(self, item_ref: UUID) -> None:
        """Record that Shell returned and original-identity verification is active."""
        self._require_initialized()
        with self._sessions.begin() as session:
            item = self._item(session, item_ref)
            if item.state is None or item.state != CleanupItemState.TRASHING.value:
                raise SystemCleanupStoreError("Cleanup item was not trashing")
            item.state = CleanupItemState.VERIFYING.value
            item.updated_at = datetime.now(UTC)

    def prepare_recovery(self, item_ref: UUID, recovery: TrashRecoveryRecord) -> None:
        """Durably write MANUAL recovery evidence before the Shell call."""
        self._require_initialized()
        with self._sessions.begin() as session:
            item = self._item(session, item_ref)
            if item.state != CleanupItemState.VALIDATING.value:
                raise SystemCleanupStoreError("Recovery can be prepared only while validating")
            item.recovery_payload = recovery.model_dump(mode="json")
            item.recovery_digest = recovery.canonical_digest()
            item.updated_at = datetime.now(UTC)

    def complete_item(
        self,
        result: CleanupItemResult,
        recovery: TrashRecoveryRecord | None,
    ) -> None:
        """Persist one terminal per-item result and integrity-protected recovery evidence."""
        if result.state not in {
            CleanupItemState.VERIFIED,
            CleanupItemState.FAILED,
            CleanupItemState.BLOCKED_CHANGED,
            CleanupItemState.IDENTITY_CHANGED,
            CleanupItemState.SKIPPED,
        }:
            raise SystemCleanupStoreError("Cleanup item result is not terminal")
        self._require_initialized()
        with self._sessions.begin() as session:
            item = self._item(session, result.item_ref)
            item.state = result.state.value
            item.result_payload = result.model_dump(mode="json")
            item.error_message = (
                result.message
                if result.state
                in {
                    CleanupItemState.FAILED,
                    CleanupItemState.BLOCKED_CHANGED,
                    CleanupItemState.IDENTITY_CHANGED,
                }
                else None
            )
            if recovery is not None:
                item.recovery_payload = recovery.model_dump(mode="json")
                item.recovery_digest = recovery.canonical_digest()
            item.updated_at = datetime.now(UTC)

    def skip_pending(self, transaction_id: UUID, message: str) -> None:
        """Mark only future pending items skipped after failure or cancellation."""
        self._require_initialized()
        with self._sessions.begin() as session:
            for row in session.scalars(
                select(SystemCleanupItemRow).where(
                    SystemCleanupItemRow.transaction_id == str(transaction_id),
                    SystemCleanupItemRow.state == CleanupItemState.PENDING.value,
                )
            ):
                item = self._planned_item(row)
                path = item.candidate.path
                if path is None:
                    raise SystemCleanupStoreError("Persisted eligible cleanup path disappeared")
                result = CleanupItemResult(
                    item_ref=item.candidate.item_ref,
                    operation_id=item.operation_id,
                    sequence=item.sequence,
                    source_candidate_id=item.candidate.source_candidate_id,
                    path=path,
                    state=CleanupItemState.SKIPPED,
                    verification_status="UNKNOWN",
                    message=message,
                )
                row.state = CleanupItemState.SKIPPED.value
                row.result_payload = result.model_dump(mode="json")
                row.updated_at = datetime.now(UTC)

    def transition(
        self,
        transaction_id: UUID,
        state: CleanupTransactionState,
        *,
        error_message: str | None = None,
    ) -> None:
        """Persist a forward or terminal transition without reopening terminals."""
        self._require_initialized()
        with self._sessions.begin() as session:
            row = self._transaction(session, transaction_id)
            current = CleanupTransactionState(row.state)
            if current in _TERMINAL_STATES and current is not state:
                raise SystemCleanupStoreError("Terminal cleanup transaction cannot transition")
            if (
                state is CleanupTransactionState.VALIDATING
                and current is not CleanupTransactionState.CONFIRMED
            ):
                raise SystemCleanupStoreError("Cleanup final validation lacks consumed authority")
            row.state = state.value
            row.error_message = error_message
            row.updated_at = datetime.now(UTC)

    def state(self, transaction_id: UUID) -> CleanupTransactionState:
        """Return one durable cleanup transaction state."""
        self._require_initialized()
        with self._sessions() as session:
            return CleanupTransactionState(self._transaction(session, transaction_id).state)

    def load_plan(self, transaction_id: UUID) -> CleanupExecutionPlan:
        """Load and validate one ordinary immutable plan."""
        row = self._load_transaction(transaction_id)
        if row.transaction_kind != "ITEM_CLEANUP":
            raise SystemCleanupStoreError("Transaction is not an item-cleanup plan")
        return CleanupExecutionPlan.model_validate(row.plan_payload)

    def load_preview(self, transaction_id: UUID) -> CleanupExecutionPreview:
        """Load and validate the latest ordinary runtime Preview."""
        row = self._load_transaction(transaction_id)
        if row.transaction_kind != "ITEM_CLEANUP":
            raise SystemCleanupStoreError("Transaction is not an item-cleanup Preview")
        return CleanupExecutionPreview.model_validate(row.preview_payload)

    def load_empty_plan(self, transaction_id: UUID) -> RecycleBinEmptyPlan:
        """Load and validate one independent irreversible plan."""
        row = self._load_transaction(transaction_id)
        if row.transaction_kind != "RECYCLE_BIN_EMPTY":
            raise SystemCleanupStoreError("Transaction is not a Recycle Bin empty plan")
        return RecycleBinEmptyPlan.model_validate(row.plan_payload)

    def load_empty_preview(self, transaction_id: UUID) -> RecycleBinEmptyPreview:
        """Load and validate the latest irreversible Preview."""
        row = self._load_transaction(transaction_id)
        if row.transaction_kind != "RECYCLE_BIN_EMPTY":
            raise SystemCleanupStoreError("Transaction is not a Recycle Bin empty Preview")
        return RecycleBinEmptyPreview.model_validate(row.preview_payload)

    def load_item(self, transaction_id: UUID, item_ref: UUID) -> PlannedCleanupItem:
        """Resolve one internal item reference only inside the guarded tool."""
        self._require_initialized()
        with self._sessions() as session:
            row = self._item(session, item_ref)
            if row.transaction_id != str(transaction_id):
                raise SystemCleanupStoreError("Cleanup item belongs to another transaction")
            return self._planned_item(row)

    def list_results(self, transaction_id: UUID) -> tuple[CleanupItemResult, ...]:
        """Return ordered terminal item results."""
        self._require_initialized()
        with self._sessions() as session:
            rows = tuple(
                session.scalars(
                    select(SystemCleanupItemRow)
                    .where(SystemCleanupItemRow.transaction_id == str(transaction_id))
                    .order_by(SystemCleanupItemRow.sequence)
                )
            )
            return tuple(
                CleanupItemResult.model_validate(row.result_payload)
                for row in rows
                if row.result_payload is not None
            )

    def list_recovery(self, transaction_id: UUID) -> tuple[TrashRecoveryRecord, ...]:
        """Return integrity-checked MANUAL recovery records."""
        self._require_initialized()
        with self._sessions() as session:
            rows = tuple(
                session.scalars(
                    select(SystemCleanupItemRow)
                    .where(SystemCleanupItemRow.transaction_id == str(transaction_id))
                    .order_by(SystemCleanupItemRow.sequence)
                )
            )
            records: list[TrashRecoveryRecord] = []
            for row in rows:
                if row.recovery_payload is None or row.recovery_digest is None:
                    continue
                recovery = TrashRecoveryRecord.model_validate(row.recovery_payload)
                if recovery.canonical_digest() != row.recovery_digest:
                    raise SystemCleanupStoreError("Cleanup recovery record integrity failed")
                records.append(recovery)
            return tuple(records)

    def complete_empty(
        self,
        result: RecycleBinEmptyResult,
        record: CleanupIrreversibilityRecord,
    ) -> None:
        """Persist verified/uncertain empty result and an explicit no-recovery record."""
        self._require_initialized()
        with self._sessions.begin() as session:
            row = self._transaction(session, result.transaction_id)
            row.result_payload = result.model_dump(mode="json")
            row.updated_at = datetime.now(UTC)
            session.add(
                SystemCleanupIrreversibilityRow(
                    record_id=str(record.record_id),
                    transaction_id=str(record.transaction_id),
                    payload=record.model_dump(mode="json"),
                )
            )

    def close(self) -> None:
        """Dispose Stage 4E2 database resources."""
        self._engine.dispose()
        self._initialized = False

    @staticmethod
    def request_for_item(
        plan: CleanupExecutionPlan,
        preview_id: UUID,
        item: PlannedCleanupItem,
    ) -> SystemCleanupTrashRequest:
        """Build the sole path-free item request accepted by the writer."""
        return SystemCleanupTrashRequest(
            transaction_id=plan.transaction_id,
            cleanup_plan_id=plan.plan_id,
            preview_id=preview_id,
            validated_item_ref=item.candidate.item_ref,
        )

    @staticmethod
    def request_for_empty(
        plan: RecycleBinEmptyPlan,
        preview_id: UUID,
    ) -> RecycleBinEmptyRequest:
        """Build the sole path-free irreversible request."""
        return RecycleBinEmptyRequest(
            transaction_id=plan.transaction_id,
            plan_id=plan.plan_id,
            preview_id=preview_id,
        )

    def _load_transaction(self, transaction_id: UUID) -> SystemCleanupTransactionRow:
        self._require_initialized()
        with self._sessions() as session:
            return self._transaction(session, transaction_id)

    @staticmethod
    def _item_row(
        plan: CleanupExecutionPlan,
        preview_id: UUID,
        item: PlannedCleanupItem,
        now: datetime,
    ) -> SystemCleanupItemRow:
        request = SystemCleanupRepository.request_for_item(plan, preview_id, item)
        return SystemCleanupItemRow(
            item_ref=str(item.candidate.item_ref),
            transaction_id=str(plan.transaction_id),
            operation_id=str(item.operation_id),
            sequence=item.sequence,
            source_candidate_id=str(item.candidate.source_candidate_id),
            state=CleanupItemState.PENDING.value,
            tool_name=item.tool_name,
            arguments_digest=arguments_digest(request.model_dump(mode="json")),
            item_payload=item.model_dump(mode="json"),
            result_payload=None,
            recovery_payload=None,
            recovery_digest=None,
            error_message=None,
            updated_at=now,
        )

    @staticmethod
    def _confirmation_row(
        confirmation: SystemCleanupConfirmation,
    ) -> SystemCleanupConfirmationRow:
        return SystemCleanupConfirmationRow(
            confirmation_id=str(confirmation.confirmation_id),
            transaction_id=str(confirmation.transaction_id),
            parent_confirmation_id=(
                str(confirmation.parent_confirmation_id)
                if confirmation.parent_confirmation_id is not None
                else None
            ),
            tier=confirmation.tier.value,
            state=confirmation.state.value,
            payload=confirmation.model_dump(mode="json"),
        )

    @staticmethod
    def _planned_item(row: SystemCleanupItemRow) -> PlannedCleanupItem:
        return PlannedCleanupItem.model_validate(row.item_payload)

    @staticmethod
    def _transaction(session: Session, transaction_id: UUID) -> SystemCleanupTransactionRow:
        row = session.get(SystemCleanupTransactionRow, str(transaction_id))
        if row is None:
            raise SystemCleanupStoreError("Unknown system cleanup transaction")
        return row

    @staticmethod
    def _item(session: Session, item_ref: UUID) -> SystemCleanupItemRow:
        row = session.get(SystemCleanupItemRow, str(item_ref))
        if row is None:
            raise SystemCleanupStoreError("Unknown system cleanup item")
        return row

    @staticmethod
    def _require_no_active(session: Session) -> None:
        if any(
            CleanupTransactionState(row.state) not in _TERMINAL_STATES
            for row in session.scalars(select(SystemCleanupTransactionRow))
        ):
            raise SystemCleanupStoreError("Another system cleanup transaction is active")

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise SystemCleanupStoreError("System cleanup repository is not initialized")


class SystemCleanupExecutionGuard(WriteExecutionGuard):
    """Require consumed durable approval before one exact item or Bin-empty dispatch."""

    def __init__(self, repository: SystemCleanupRepository) -> None:
        self._repository = repository

    def require(
        self,
        authorization: ExecutionAuthorization,
        tool_name: str,
        arguments: Mapping[str, JsonValue],
    ) -> None:
        """Atomically validate the path-free capability and reserve exactly one call."""
        try:
            if tool_name == "optimization.cleanup.trash":
                self._require_item(authorization, arguments)
            elif tool_name == "optimization.recycle_bin.empty":
                self._require_empty(authorization, arguments)
            else:
                raise WriteAuthorizationError("System cleanup guard rejected unknown writer")
        except SystemCleanupStoreError as exc:
            raise WriteAuthorizationError("System cleanup authorization store failed") from exc

    def _require_item(
        self,
        authorization: ExecutionAuthorization,
        arguments: Mapping[str, JsonValue],
    ) -> None:
        request = SystemCleanupTrashRequest.model_validate(arguments)
        with self._repository._sessions.begin() as session:
            transaction = self._repository._transaction(session, authorization.transaction_id)
            item = self._repository._item(session, request.validated_item_ref)
            runtime = self._runtime_confirmation(session, authorization)
            if (
                transaction.transaction_kind != "ITEM_CLEANUP"
                or transaction.state
                not in {
                    CleanupTransactionState.CONFIRMED.value,
                    CleanupTransactionState.EXECUTING.value,
                }
                or transaction.plan_id != str(authorization.plan_id)
                or transaction.preview_id != str(authorization.preview_id)
                or transaction.runtime_confirmation_id != str(authorization.runtime_confirmation_id)
                or request.transaction_id != authorization.transaction_id
                or request.cleanup_plan_id != authorization.plan_id
                or request.preview_id != authorization.preview_id
                or item.transaction_id != transaction.transaction_id
                or item.operation_id != str(authorization.operation_id)
                or item.tool_name != "optimization.cleanup.trash"
                or item.state != CleanupItemState.VALIDATING.value
                or item.arguments_digest != authorization.arguments_digest
                or item.arguments_digest != arguments_digest(arguments)
                or runtime is None
                or runtime.state != SystemCleanupConfirmationState.CONSUMED.value
            ):
                raise WriteAuthorizationError("Cleanup item lacks exact durable authorization")
            item.state = CleanupItemState.TRASHING.value
            item.updated_at = datetime.now(UTC)
            transaction.state = CleanupTransactionState.EXECUTING.value
            transaction.updated_at = datetime.now(UTC)

    def _require_empty(
        self,
        authorization: ExecutionAuthorization,
        arguments: Mapping[str, JsonValue],
    ) -> None:
        request = RecycleBinEmptyRequest.model_validate(arguments)
        with self._repository._sessions.begin() as session:
            transaction = self._repository._transaction(session, authorization.transaction_id)
            runtime = self._runtime_confirmation(session, authorization)
            if (
                transaction.transaction_kind != "RECYCLE_BIN_EMPTY"
                or transaction.state != CleanupTransactionState.VALIDATING.value
                or transaction.plan_id != str(authorization.plan_id)
                or transaction.preview_id != str(authorization.preview_id)
                or transaction.operation_id != str(authorization.operation_id)
                or transaction.runtime_confirmation_id != str(authorization.runtime_confirmation_id)
                or request.transaction_id != authorization.transaction_id
                or request.plan_id != authorization.plan_id
                or request.preview_id != authorization.preview_id
                or transaction.arguments_digest != authorization.arguments_digest
                or transaction.arguments_digest != arguments_digest(arguments)
                or runtime is None
                or runtime.state != SystemCleanupConfirmationState.CONSUMED.value
            ):
                raise WriteAuthorizationError("Bin emptying lacks exact durable authorization")
            transaction.state = CleanupTransactionState.EXECUTING.value
            transaction.updated_at = datetime.now(UTC)

    @staticmethod
    def _runtime_confirmation(
        session: Session,
        authorization: ExecutionAuthorization,
    ) -> SystemCleanupConfirmationRow | None:
        if authorization.runtime_confirmation_id is None:
            return None
        return session.get(
            SystemCleanupConfirmationRow,
            str(authorization.runtime_confirmation_id),
        )
