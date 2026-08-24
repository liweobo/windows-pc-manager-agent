"""Durable Stage 4D4 plans, confirmations, items, recovery, and write guard."""

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

from pc_manager_agent.confirmation.residual_cleanup import (
    ResidualCleanupConfirmation,
    ResidualCleanupConfirmationState,
    ResidualCleanupConfirmationTier,
)
from pc_manager_agent.domain.residual_cleanup import (
    PlannedResidualCleanupItem,
    ResidualCleanupItemResult,
    ResidualCleanupItemState,
    ResidualCleanupPlan,
    ResidualCleanupPreview,
    ResidualCleanupTransactionState,
    ResidualCleanupTrashRequest,
)
from pc_manager_agent.persistence.database import create_sqlite_engine
from pc_manager_agent.recovery.models import TrashRecoveryRecord
from pc_manager_agent.tools.execution import (
    ExecutionAuthorization,
    WriteExecutionGuard,
    arguments_digest,
)
from pc_manager_agent.tools.registry import WriteAuthorizationError


class ResidualCleanupStoreError(RuntimeError):
    """Raised when Stage 4D4 durable authorization cannot be trusted."""


class ResidualCleanupBase(DeclarativeBase):
    """Declarative base isolated from report-only and generic file tables."""


class ResidualCleanupTransactionRow(ResidualCleanupBase):
    """One exact batch with immutable plan and latest confirmed Preview payloads."""

    __tablename__ = "residual_cleanup_transactions"

    transaction_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    preview_id: Mapped[str] = mapped_column(String(36), index=True)
    source_report_id: Mapped[str] = mapped_column(String(36), index=True)
    state: Mapped[str] = mapped_column(String(50), index=True)
    risk_level: Mapped[str] = mapped_column(String(30))
    plan_digest: Mapped[str] = mapped_column(String(64))
    preview_digest: Mapped[str] = mapped_column(String(64))
    item_set_digest: Mapped[str] = mapped_column(String(64))
    runtime_confirmation_id: Mapped[str | None] = mapped_column(String(36), index=True)
    plan_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    preview_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)


class ResidualCleanupItemRow(ResidualCleanupBase):
    """One ordered item and its reference-only tool reservation."""

    __tablename__ = "residual_cleanup_items"

    item_ref: Mapped[str] = mapped_column(String(36), primary_key=True)
    transaction_id: Mapped[str] = mapped_column(
        ForeignKey("residual_cleanup_transactions.transaction_id"), index=True
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


class ResidualCleanupConfirmationRow(ResidualCleanupBase):
    """One durable digest-bound plan or runtime approval."""

    __tablename__ = "residual_cleanup_confirmations"

    confirmation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    transaction_id: Mapped[str] = mapped_column(String(36), index=True)
    parent_confirmation_id: Mapped[str | None] = mapped_column(String(36), index=True)
    tier: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(20), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


_TERMINAL_STATES = frozenset(
    {
        ResidualCleanupTransactionState.COMPLETED,
        ResidualCleanupTransactionState.PARTIALLY_COMPLETED,
        ResidualCleanupTransactionState.FAILED,
        ResidualCleanupTransactionState.CANCELLED,
        ResidualCleanupTransactionState.INTERRUPTED,
        ResidualCleanupTransactionState.BLOCKED,
    }
)


class ResidualCleanupRepository:
    """Persist one non-resumable cleanup batch and its single-use capabilities."""

    def __init__(self, database_path: Path) -> None:
        self._engine = create_sqlite_engine(database_path)
        self._sessions = sessionmaker(self._engine, expire_on_commit=False)
        self._initialized = False

    def initialize(self) -> tuple[UUID, ...]:
        """Create tables, expire approvals, and mark abandoned batches interrupted."""
        interrupted: list[UUID] = []
        try:
            ResidualCleanupBase.metadata.create_all(self._engine)
            current = datetime.now(UTC)
            with self._sessions.begin() as session:
                for row in session.scalars(select(ResidualCleanupTransactionRow)):
                    state = ResidualCleanupTransactionState(row.state)
                    if state not in _TERMINAL_STATES:
                        interrupted.append(UUID(row.transaction_id))
                        row.state = ResidualCleanupTransactionState.INTERRUPTED.value
                        row.error_message = (
                            "Application restarted; residual cleanup will not continue "
                            "automatically."
                        )
                        row.updated_at = current
                for confirmation_row in session.scalars(select(ResidualCleanupConfirmationRow)):
                    if confirmation_row.state in {
                        ResidualCleanupConfirmationState.PENDING.value,
                        ResidualCleanupConfirmationState.APPROVED.value,
                    }:
                        payload = dict(confirmation_row.payload)
                        payload["state"] = ResidualCleanupConfirmationState.EXPIRED.value
                        confirmation_row.state = ResidualCleanupConfirmationState.EXPIRED.value
                        confirmation_row.payload = payload
        except SQLAlchemyError as exc:
            raise ResidualCleanupStoreError(
                "Residual cleanup repository initialization failed"
            ) from exc
        self._initialized = True
        return tuple(interrupted)

    def create(self, plan: ResidualCleanupPlan, preview: ResidualCleanupPreview) -> None:
        """Atomically reserve an exact plan and every reference-only item request."""
        self._require_initialized()
        current = datetime.now(UTC)
        try:
            with self._sessions.begin() as session:
                if any(
                    ResidualCleanupTransactionState(row.state) not in _TERMINAL_STATES
                    for row in session.scalars(select(ResidualCleanupTransactionRow))
                ):
                    raise ResidualCleanupStoreError("Another residual cleanup batch is active")
                session.add(
                    ResidualCleanupTransactionRow(
                        transaction_id=str(plan.transaction_id),
                        plan_id=str(plan.plan_id),
                        preview_id=str(preview.preview_id),
                        source_report_id=str(plan.source_report_id),
                        state=ResidualCleanupTransactionState.PREVIEWED.value,
                        risk_level=plan.risk_level.value,
                        plan_digest=plan.canonical_digest(),
                        preview_digest=preview.canonical_digest(),
                        item_set_digest=preview.item_set_digest,
                        runtime_confirmation_id=None,
                        plan_payload=plan.model_dump(mode="json"),
                        preview_payload=preview.model_dump(mode="json"),
                        created_at=current,
                        updated_at=current,
                        error_message=None,
                    )
                )
                session.flush()
                session.add_all(
                    self._item_row(plan, preview.preview_id, item, current) for item in plan.items
                )
        except ResidualCleanupStoreError:
            raise
        except SQLAlchemyError as exc:
            raise ResidualCleanupStoreError("Residual cleanup plan persistence failed") from exc

    def bind_runtime_preview(
        self,
        plan: ResidualCleanupPlan,
        preview: ResidualCleanupPreview,
    ) -> None:
        """Replace the initial Preview reservation with the freshly rescanned runtime Preview."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                transaction = self._transaction(session, plan.transaction_id)
                if transaction.state != ResidualCleanupTransactionState.PLAN_CONFIRMED.value:
                    raise ResidualCleanupStoreError(
                        "Residual cleanup transaction is not ready for runtime Preview"
                    )
                transaction.preview_id = str(preview.preview_id)
                transaction.preview_digest = preview.canonical_digest()
                transaction.item_set_digest = preview.item_set_digest
                transaction.preview_payload = preview.model_dump(mode="json")
                transaction.updated_at = datetime.now(UTC)
                rows = tuple(
                    session.scalars(
                        select(ResidualCleanupItemRow).where(
                            ResidualCleanupItemRow.transaction_id == str(plan.transaction_id)
                        )
                    )
                )
                by_ref = {str(item.item_ref): item for item in plan.items}
                for row in rows:
                    item = by_ref[row.item_ref]
                    request = self.request_for_item(plan, preview.preview_id, item)
                    row.arguments_digest = arguments_digest(request.model_dump(mode="json"))
                    row.updated_at = datetime.now(UTC)
        except ResidualCleanupStoreError:
            raise
        except SQLAlchemyError as exc:
            raise ResidualCleanupStoreError("Runtime Preview persistence failed") from exc

    def save_confirmation(self, confirmation: ResidualCleanupConfirmation) -> None:
        """Persist one pending gate and move the transaction to its waiting state."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                session.add(self._confirmation_row(confirmation))
                transaction = self._transaction(session, confirmation.transaction_id)
                transaction.state = (
                    ResidualCleanupTransactionState.AWAITING_PLAN_CONFIRMATION.value
                    if confirmation.tier is ResidualCleanupConfirmationTier.PLAN
                    else ResidualCleanupTransactionState.AWAITING_RUNTIME_CONFIRMATION.value
                )
                transaction.updated_at = datetime.now(UTC)
        except SQLAlchemyError as exc:
            raise ResidualCleanupStoreError(
                "Residual cleanup confirmation persistence failed"
            ) from exc

    def get_confirmation(self, confirmation_id: UUID) -> ResidualCleanupConfirmation:
        """Load one exact confirmation from durable storage."""
        self._require_initialized()
        with self._sessions() as session:
            row = session.get(ResidualCleanupConfirmationRow, str(confirmation_id))
            if row is None:
                raise ResidualCleanupStoreError("Unknown residual cleanup confirmation")
            return ResidualCleanupConfirmation.model_validate(row.payload)

    def update_confirmation(self, confirmation: ResidualCleanupConfirmation) -> None:
        """Persist approval, rejection, or expiry and its transaction state."""
        self._require_initialized()
        with self._sessions.begin() as session:
            row = session.get(
                ResidualCleanupConfirmationRow,
                str(confirmation.confirmation_id),
            )
            if row is None:
                raise ResidualCleanupStoreError("Unknown residual cleanup confirmation")
            row.state = confirmation.state.value
            row.payload = confirmation.model_dump(mode="json")
            transaction = self._transaction(session, confirmation.transaction_id)
            if (
                confirmation.state is ResidualCleanupConfirmationState.APPROVED
                and confirmation.tier is ResidualCleanupConfirmationTier.PLAN
            ):
                transaction.state = ResidualCleanupTransactionState.PLAN_CONFIRMED.value
            elif confirmation.state in {
                ResidualCleanupConfirmationState.REJECTED,
                ResidualCleanupConfirmationState.EXPIRED,
            }:
                transaction.state = ResidualCleanupTransactionState.CANCELLED.value
            transaction.updated_at = datetime.now(UTC)

    def consume_confirmation_pair(
        self,
        plan_confirmation: ResidualCleanupConfirmation,
        runtime_confirmation: ResidualCleanupConfirmation,
    ) -> None:
        """Atomically consume both approvals and reserve dispatch exactly once."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                rows = (
                    session.get(
                        ResidualCleanupConfirmationRow,
                        str(plan_confirmation.confirmation_id),
                    ),
                    session.get(
                        ResidualCleanupConfirmationRow,
                        str(runtime_confirmation.confirmation_id),
                    ),
                )
                if any(
                    row is None or row.state != ResidualCleanupConfirmationState.APPROVED.value
                    for row in rows
                ):
                    raise ResidualCleanupStoreError(
                        "Residual cleanup confirmation replay or mismatch"
                    )
                for row in rows:
                    if row is None:
                        raise ResidualCleanupStoreError("Residual cleanup confirmation disappeared")
                    payload = dict(row.payload)
                    payload["state"] = ResidualCleanupConfirmationState.CONSUMED.value
                    row.state = ResidualCleanupConfirmationState.CONSUMED.value
                    row.payload = payload
                transaction = self._transaction(
                    session,
                    runtime_confirmation.transaction_id,
                )
                if (
                    transaction.state
                    != ResidualCleanupTransactionState.AWAITING_RUNTIME_CONFIRMATION.value
                ):
                    raise ResidualCleanupStoreError(
                        "Residual cleanup transaction is not dispatchable"
                    )
                transaction.runtime_confirmation_id = str(runtime_confirmation.confirmation_id)
                transaction.state = ResidualCleanupTransactionState.DISPATCHING.value
                transaction.updated_at = datetime.now(UTC)
        except ResidualCleanupStoreError:
            raise
        except SQLAlchemyError as exc:
            raise ResidualCleanupStoreError("Residual cleanup approval consumption failed") from exc

    def begin_item_validation(self, transaction_id: UUID, item_ref: UUID) -> None:
        """Mark exactly one pending item validating before its final TOCTOU gate."""
        self._require_initialized()
        with self._sessions.begin() as session:
            transaction = self._transaction(session, transaction_id)
            if transaction.state not in {
                ResidualCleanupTransactionState.DISPATCHING.value,
                ResidualCleanupTransactionState.EXECUTING.value,
            }:
                raise ResidualCleanupStoreError("Residual cleanup batch is not executing")
            item = self._item(session, item_ref)
            if (
                item.transaction_id != str(transaction_id)
                or item.state != ResidualCleanupItemState.PLANNED.value
            ):
                raise ResidualCleanupStoreError("Residual cleanup item is not pending")
            item.state = ResidualCleanupItemState.VALIDATING.value
            item.updated_at = datetime.now(UTC)

    def mark_item_verifying(self, item_ref: UUID) -> None:
        """Record that the Shell call returned and identity verification is active."""
        self._require_initialized()
        with self._sessions.begin() as session:
            item = self._item(session, item_ref)
            if item.state != ResidualCleanupItemState.TRASHING.value:
                raise ResidualCleanupStoreError("Residual cleanup item was not trashing")
            item.state = ResidualCleanupItemState.VERIFYING.value
            item.updated_at = datetime.now(UTC)

    def prepare_recovery(self, item_ref: UUID, recovery: TrashRecoveryRecord) -> None:
        """Durably write MANUAL recovery evidence before the Shell call starts."""
        self._require_initialized()
        with self._sessions.begin() as session:
            item = self._item(session, item_ref)
            if item.state != ResidualCleanupItemState.VALIDATING.value:
                raise ResidualCleanupStoreError(
                    "Residual recovery can be prepared only during final validation"
                )
            item.recovery_payload = recovery.model_dump(mode="json")
            item.recovery_digest = recovery.canonical_digest()
            item.updated_at = datetime.now(UTC)

    def complete_item(
        self,
        result: ResidualCleanupItemResult,
        recovery: TrashRecoveryRecord | None,
    ) -> None:
        """Persist one terminal item result and integrity-protected recovery evidence."""
        self._require_initialized()
        if result.state not in {
            ResidualCleanupItemState.VERIFIED,
            ResidualCleanupItemState.FAILED,
            ResidualCleanupItemState.BLOCKED_CHANGED,
            ResidualCleanupItemState.SKIPPED,
        }:
            raise ResidualCleanupStoreError("Residual cleanup item result is not terminal")
        with self._sessions.begin() as session:
            item = self._item(session, result.item_ref)
            item.state = result.state.value
            item.result_payload = result.model_dump(mode="json")
            item.error_message = (
                result.message
                if result.state
                in {
                    ResidualCleanupItemState.FAILED,
                    ResidualCleanupItemState.BLOCKED_CHANGED,
                }
                else None
            )
            if recovery is not None:
                item.recovery_payload = recovery.model_dump(mode="json")
                item.recovery_digest = recovery.canonical_digest()
            item.updated_at = datetime.now(UTC)

    def skip_pending(self, transaction_id: UUID, message: str) -> None:
        """Mark only future PLANNED items skipped after failure or cancellation."""
        self._require_initialized()
        with self._sessions.begin() as session:
            for item in session.scalars(
                select(ResidualCleanupItemRow).where(
                    ResidualCleanupItemRow.transaction_id == str(transaction_id),
                    ResidualCleanupItemRow.state == ResidualCleanupItemState.PLANNED.value,
                )
            ):
                planned = PlannedResidualCleanupItem.model_validate(item.item_payload)
                result = ResidualCleanupItemResult(
                    item_ref=planned.item_ref,
                    operation_id=planned.operation_id,
                    sequence=planned.sequence,
                    source_candidate_id=planned.candidate.source_candidate_id,
                    path=planned.candidate.path,
                    state=ResidualCleanupItemState.SKIPPED,
                    verification_status="UNKNOWN",
                    message=message,
                )
                item.state = ResidualCleanupItemState.SKIPPED.value
                item.result_payload = result.model_dump(mode="json")
                item.updated_at = datetime.now(UTC)

    def transition(
        self,
        transaction_id: UUID,
        state: ResidualCleanupTransactionState,
        *,
        error_message: str | None = None,
    ) -> None:
        """Persist a terminal or forward batch transition without reopening terminals."""
        self._require_initialized()
        with self._sessions.begin() as session:
            row = self._transaction(session, transaction_id)
            current = ResidualCleanupTransactionState(row.state)
            if current in _TERMINAL_STATES and current is not state:
                raise ResidualCleanupStoreError("Terminal residual cleanup cannot transition")
            row.state = state.value
            row.error_message = error_message
            row.updated_at = datetime.now(UTC)

    def state(self, transaction_id: UUID) -> ResidualCleanupTransactionState:
        """Return one durable cleanup transaction state."""
        self._require_initialized()
        with self._sessions() as session:
            return ResidualCleanupTransactionState(self._transaction(session, transaction_id).state)

    def load_plan(self, transaction_id: UUID) -> ResidualCleanupPlan:
        """Load and validate the immutable persisted plan."""
        self._require_initialized()
        with self._sessions() as session:
            return ResidualCleanupPlan.model_validate(
                self._transaction(session, transaction_id).plan_payload
            )

    def load_preview(self, transaction_id: UUID) -> ResidualCleanupPreview:
        """Load and validate the latest runtime-bound Preview."""
        self._require_initialized()
        with self._sessions() as session:
            return ResidualCleanupPreview.model_validate(
                self._transaction(session, transaction_id).preview_payload
            )

    def load_item(self, transaction_id: UUID, item_ref: UUID) -> PlannedResidualCleanupItem:
        """Resolve one internal item reference only inside the guarded tool."""
        self._require_initialized()
        with self._sessions() as session:
            row = self._item(session, item_ref)
            if row.transaction_id != str(transaction_id):
                raise ResidualCleanupStoreError("Cleanup item belongs to another transaction")
            return PlannedResidualCleanupItem.model_validate(row.item_payload)

    def list_results(self, transaction_id: UUID) -> tuple[ResidualCleanupItemResult, ...]:
        """Return ordered terminal results while omitting untouched interrupted items."""
        self._require_initialized()
        with self._sessions() as session:
            rows = tuple(
                session.scalars(
                    select(ResidualCleanupItemRow)
                    .where(ResidualCleanupItemRow.transaction_id == str(transaction_id))
                    .order_by(ResidualCleanupItemRow.sequence)
                )
            )
            return tuple(
                ResidualCleanupItemResult.model_validate(row.result_payload)
                for row in rows
                if row.result_payload is not None
            )

    def list_recovery(self, transaction_id: UUID) -> tuple[TrashRecoveryRecord, ...]:
        """Return integrity-checked MANUAL recovery records in execution order."""
        self._require_initialized()
        with self._sessions() as session:
            rows = tuple(
                session.scalars(
                    select(ResidualCleanupItemRow)
                    .where(ResidualCleanupItemRow.transaction_id == str(transaction_id))
                    .order_by(ResidualCleanupItemRow.sequence)
                )
            )
            records: list[TrashRecoveryRecord] = []
            for row in rows:
                if row.recovery_payload is None or row.recovery_digest is None:
                    continue
                record = TrashRecoveryRecord.model_validate(row.recovery_payload)
                if record.canonical_digest() != row.recovery_digest:
                    raise ResidualCleanupStoreError("Residual recovery record integrity failed")
                records.append(record)
            return tuple(records)

    def close(self) -> None:
        """Dispose Stage 4D4 database resources."""
        self._engine.dispose()
        self._initialized = False

    @staticmethod
    def request_for_item(
        plan: ResidualCleanupPlan,
        preview_id: UUID,
        item: PlannedResidualCleanupItem,
    ) -> ResidualCleanupTrashRequest:
        """Build the sole reference-only request accepted by the write tool."""
        return ResidualCleanupTrashRequest(
            transaction_id=plan.transaction_id,
            cleanup_plan_id=plan.plan_id,
            preview_id=preview_id,
            validated_item_ref=item.item_ref,
        )

    def _item_row(
        self,
        plan: ResidualCleanupPlan,
        preview_id: UUID,
        item: PlannedResidualCleanupItem,
        current: datetime,
    ) -> ResidualCleanupItemRow:
        request = self.request_for_item(plan, preview_id, item)
        return ResidualCleanupItemRow(
            item_ref=str(item.item_ref),
            transaction_id=str(plan.transaction_id),
            operation_id=str(item.operation_id),
            sequence=item.sequence,
            source_candidate_id=str(item.candidate.source_candidate_id),
            state=ResidualCleanupItemState.PLANNED.value,
            tool_name=item.tool_name,
            arguments_digest=arguments_digest(request.model_dump(mode="json")),
            item_payload=item.model_dump(mode="json"),
            result_payload=None,
            recovery_payload=None,
            recovery_digest=None,
            error_message=None,
            updated_at=current,
        )

    @staticmethod
    def _confirmation_row(
        confirmation: ResidualCleanupConfirmation,
    ) -> ResidualCleanupConfirmationRow:
        return ResidualCleanupConfirmationRow(
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
    def _transaction(
        session: Session,
        transaction_id: UUID,
    ) -> ResidualCleanupTransactionRow:
        row = session.get(ResidualCleanupTransactionRow, str(transaction_id))
        if row is None:
            raise ResidualCleanupStoreError("Unknown residual cleanup transaction")
        return row

    @staticmethod
    def _item(session: Session, item_ref: UUID) -> ResidualCleanupItemRow:
        row = session.get(ResidualCleanupItemRow, str(item_ref))
        if row is None:
            raise ResidualCleanupStoreError("Unknown residual cleanup item")
        return row

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise ResidualCleanupStoreError("Residual cleanup repository is not initialized")


class ResidualCleanupExecutionGuard(WriteExecutionGuard):
    """Require consumed durable approvals before each exact referenced item executes."""

    def __init__(self, repository: ResidualCleanupRepository) -> None:
        self._repository = repository

    def require(
        self,
        authorization: ExecutionAuthorization,
        tool_name: str,
        arguments: Mapping[str, JsonValue],
    ) -> None:
        """Atomically validate one reference-only capability and mark it trashing."""
        try:
            self._repository._require_initialized()
            request = ResidualCleanupTrashRequest.model_validate(arguments)
            with self._repository._sessions.begin() as session:
                transaction = self._repository._transaction(
                    session,
                    authorization.transaction_id,
                )
                item = self._repository._item(session, request.validated_item_ref)
                runtime = (
                    session.get(
                        ResidualCleanupConfirmationRow,
                        str(authorization.runtime_confirmation_id),
                    )
                    if authorization.runtime_confirmation_id is not None
                    else None
                )
                if (
                    transaction.state
                    not in {
                        ResidualCleanupTransactionState.DISPATCHING.value,
                        ResidualCleanupTransactionState.EXECUTING.value,
                    }
                    or transaction.plan_id != str(authorization.plan_id)
                    or transaction.preview_id != str(authorization.preview_id)
                    or transaction.runtime_confirmation_id
                    != str(authorization.runtime_confirmation_id)
                    or request.transaction_id != authorization.transaction_id
                    or request.cleanup_plan_id != authorization.plan_id
                    or request.preview_id != authorization.preview_id
                    or item.transaction_id != transaction.transaction_id
                    or item.operation_id != str(authorization.operation_id)
                    or item.tool_name != tool_name
                    or item.state != ResidualCleanupItemState.VALIDATING.value
                    or item.arguments_digest != authorization.arguments_digest
                    or item.arguments_digest != arguments_digest(arguments)
                    or runtime is None
                    or runtime.state != ResidualCleanupConfirmationState.CONSUMED.value
                ):
                    raise WriteAuthorizationError(
                        "Residual cleanup lacks exact durable authorization"
                    )
                item.state = ResidualCleanupItemState.TRASHING.value
                item.updated_at = datetime.now(UTC)
                transaction.state = ResidualCleanupTransactionState.EXECUTING.value
                transaction.updated_at = datetime.now(UTC)
        except ResidualCleanupStoreError as exc:
            raise WriteAuthorizationError("Residual cleanup authorization store failed") from exc
