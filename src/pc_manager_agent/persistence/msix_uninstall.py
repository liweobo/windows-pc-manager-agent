"""Durable MSIX transactions, confirmations, and one-shot execution guard."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import JSON, DateTime, String, Text, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from pc_manager_agent.confirmation.msix_uninstall import (
    MsixConfirmationState,
    MsixConfirmationTier,
    MsixUninstallConfirmation,
)
from pc_manager_agent.domain.msix_uninstall import (
    MsixTransactionState,
    MsixUninstallPlan,
    MsixUninstallPreview,
    MsixUninstallRequest,
    ValidatedMsixRemovalAction,
)
from pc_manager_agent.persistence.database import create_sqlite_engine
from pc_manager_agent.tools.execution import (
    ExecutionAuthorization,
    WriteExecutionGuard,
    arguments_digest,
)
from pc_manager_agent.tools.registry import WriteAuthorizationError


class MsixUninstallStoreError(RuntimeError):
    """Raised when durable MSIX authorization cannot be trusted."""


class MsixUninstallBase(DeclarativeBase):
    """Declarative base isolated from earlier additive persistence modules."""


class MsixTransactionRow(MsixUninstallBase):
    """Privacy-minimized transaction and exact request reservation."""

    __tablename__ = "msix_uninstall_transactions"

    transaction_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    operation_id: Mapped[str] = mapped_column(String(36), unique=True)
    plan_id: Mapped[str] = mapped_column(String(36), index=True)
    preview_id: Mapped[str] = mapped_column(String(36))
    state: Mapped[str] = mapped_column(String(50), index=True)
    package_identity_digest: Mapped[str] = mapped_column(String(64), index=True)
    dependency_digest: Mapped[str] = mapped_column(String(64))
    plan_digest: Mapped[str] = mapped_column(String(64))
    preview_digest: Mapped[str] = mapped_column(String(64))
    invariant_digest: Mapped[str] = mapped_column(String(64))
    tool_name: Mapped[str] = mapped_column(String(120))
    arguments_digest: Mapped[str] = mapped_column(String(64))
    runtime_confirmation_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)


class MsixConfirmationRow(MsixUninstallBase):
    """Durable digest-bound gate; no manifest or user data is stored."""

    __tablename__ = "msix_uninstall_confirmations"

    confirmation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    transaction_id: Mapped[str] = mapped_column(String(36), index=True)
    parent_confirmation_id: Mapped[str | None] = mapped_column(String(36), index=True)
    tier: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(20), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


_TERMINAL = frozenset(
    {
        MsixTransactionState.VERIFIED_REMOVED,
        MsixTransactionState.COMPLETED_UNVERIFIED,
        MsixTransactionState.ACCESS_DENIED,
        MsixTransactionState.CANCELLED,
        MsixTransactionState.FAILED,
        MsixTransactionState.BLOCKED,
        MsixTransactionState.INTERRUPTED,
    }
)


class MsixUninstallRepository:
    """Persist one global software-uninstall workflow and its two approvals."""

    def __init__(self, database_path: Path) -> None:
        self._engine = create_sqlite_engine(database_path)
        self._sessions = sessionmaker(self._engine, expire_on_commit=False)
        self._initialized = False

    def initialize(self) -> tuple[UUID, ...]:
        """Create tables, expire approvals, and mark abandoned removals interrupted."""
        interrupted: list[UUID] = []
        try:
            MsixUninstallBase.metadata.create_all(self._engine)
            current = datetime.now(UTC)
            with self._sessions.begin() as session:
                for transaction_row in session.scalars(select(MsixTransactionRow)):
                    if MsixTransactionState(transaction_row.state) not in _TERMINAL:
                        interrupted.append(UUID(transaction_row.transaction_id))
                        transaction_row.state = MsixTransactionState.INTERRUPTED.value
                        transaction_row.error_message = (
                            "Application restarted; MSIX removal will not retry."
                        )
                        transaction_row.updated_at = current
                for confirmation_row in session.scalars(select(MsixConfirmationRow)):
                    if confirmation_row.state in {
                        MsixConfirmationState.PENDING.value,
                        MsixConfirmationState.APPROVED.value,
                    }:
                        payload = dict(confirmation_row.payload)
                        payload["state"] = MsixConfirmationState.EXPIRED.value
                        confirmation_row.payload = payload
                        confirmation_row.state = MsixConfirmationState.EXPIRED.value
        except SQLAlchemyError as exc:
            raise MsixUninstallStoreError("MSIX repository initialization failed") from exc
        self._initialized = True
        return tuple(interrupted)

    def create(self, plan: MsixUninstallPlan, preview: MsixUninstallPreview) -> None:
        """Reserve the global uninstall slot before any approval is issued."""
        self._require_initialized()
        request = _request_for_preview(preview)
        current = datetime.now(UTC)
        try:
            with self._sessions.begin() as session:
                if _any_active_uninstall(session):
                    raise MsixUninstallStoreError(
                        "Another MSI, Vendor, winget, or MSIX uninstall is active"
                    )
                session.add(
                    MsixTransactionRow(
                        transaction_id=str(plan.transaction_id),
                        operation_id=str(plan.operation_id),
                        plan_id=str(plan.plan_id),
                        preview_id=str(preview.preview_id),
                        state=MsixTransactionState.PREVIEWED.value,
                        package_identity_digest=plan.package_identity_digest,
                        dependency_digest=plan.dependency_digest,
                        plan_digest=plan.canonical_digest(),
                        preview_digest=preview.canonical_digest(),
                        invariant_digest=preview.invariant_digest(),
                        tool_name=plan.tool_name,
                        arguments_digest=arguments_digest(request.model_dump(mode="json")),
                        runtime_confirmation_id=None,
                        created_at=current,
                        updated_at=current,
                        result=None,
                        error_message=None,
                    )
                )
        except MsixUninstallStoreError:
            raise
        except SQLAlchemyError as exc:
            raise MsixUninstallStoreError("MSIX transaction creation failed") from exc

    def has_active_uninstall(self, exclude_msix_transaction: UUID | None = None) -> bool:
        """Return whether any supported software-uninstall transaction is active."""
        self._require_initialized()
        with self._sessions() as session:
            return _any_active_uninstall(session, exclude_msix_transaction)

    def state(self, transaction_id: UUID) -> MsixTransactionState:
        """Return one durable transaction state."""
        self._require_initialized()
        with self._sessions() as session:
            row = self._transaction(session, transaction_id)
            return MsixTransactionState(row.state)

    def transition(self, transaction_id: UUID, state: MsixTransactionState) -> None:
        """Persist a forward state change; terminal transactions stay terminal."""
        self._require_initialized()
        with self._sessions.begin() as session:
            row = self._transaction(session, transaction_id)
            current = MsixTransactionState(row.state)
            if current in _TERMINAL or current is state:
                if current is state:
                    return
                raise MsixUninstallStoreError("terminal MSIX transaction cannot transition")
            row.state = state.value
            row.updated_at = datetime.now(UTC)

    def save_confirmation(self, confirmation: MsixUninstallConfirmation) -> None:
        """Persist one pending digest-bound gate."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                session.add(_confirmation_row(confirmation))
                transaction = self._transaction(session, confirmation.transaction_id)
                transaction.state = (
                    MsixTransactionState.AWAITING_PLAN_CONFIRMATION.value
                    if confirmation.tier is MsixConfirmationTier.PLAN
                    else MsixTransactionState.AWAITING_RUNTIME_CONFIRMATION.value
                )
                transaction.updated_at = datetime.now(UTC)
        except SQLAlchemyError as exc:
            raise MsixUninstallStoreError("MSIX confirmation persistence failed") from exc

    def get_confirmation(self, confirmation_id: UUID) -> MsixUninstallConfirmation:
        """Load one exact gate from durable storage."""
        self._require_initialized()
        with self._sessions() as session:
            row = session.get(MsixConfirmationRow, str(confirmation_id))
            if row is None:
                raise MsixUninstallStoreError("unknown MSIX confirmation")
            return MsixUninstallConfirmation.model_validate(row.payload)

    def update_confirmation(self, confirmation: MsixUninstallConfirmation) -> None:
        """Persist an approval, rejection, expiry, or consumption state."""
        self._require_initialized()
        with self._sessions.begin() as session:
            row = session.get(MsixConfirmationRow, str(confirmation.confirmation_id))
            if row is None:
                raise MsixUninstallStoreError("unknown MSIX confirmation")
            row.state = confirmation.state.value
            row.payload = confirmation.model_dump(mode="json")
            transaction = self._transaction(session, confirmation.transaction_id)
            if (
                confirmation.state is MsixConfirmationState.APPROVED
                and confirmation.tier is MsixConfirmationTier.PLAN
            ):
                transaction.state = MsixTransactionState.PLAN_CONFIRMED.value
            elif confirmation.state in {
                MsixConfirmationState.REJECTED,
                MsixConfirmationState.EXPIRED,
            }:
                transaction.state = MsixTransactionState.CANCELLED.value
            transaction.updated_at = datetime.now(UTC)

    def consume_pair(
        self,
        plan_confirmation: MsixUninstallConfirmation,
        runtime_confirmation: MsixUninstallConfirmation,
    ) -> None:
        """Atomically consume both approvals and reserve one dispatch."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                rows = [
                    session.get(MsixConfirmationRow, str(plan_confirmation.confirmation_id)),
                    session.get(MsixConfirmationRow, str(runtime_confirmation.confirmation_id)),
                ]
                if any(
                    row is None or row.state != MsixConfirmationState.APPROVED.value for row in rows
                ):
                    raise MsixUninstallStoreError("MSIX confirmation replay or mismatch")
                for row in rows:
                    if row is None:
                        raise MsixUninstallStoreError("MSIX confirmation disappeared")
                    payload = dict(row.payload)
                    payload["state"] = MsixConfirmationState.CONSUMED.value
                    row.state = MsixConfirmationState.CONSUMED.value
                    row.payload = payload
                transaction = self._transaction(session, runtime_confirmation.transaction_id)
                if transaction.state != MsixTransactionState.AWAITING_RUNTIME_CONFIRMATION.value:
                    raise MsixUninstallStoreError("MSIX transaction is not dispatchable")
                transaction.runtime_confirmation_id = str(runtime_confirmation.confirmation_id)
                transaction.state = MsixTransactionState.DISPATCHING.value
                transaction.updated_at = datetime.now(UTC)
        except MsixUninstallStoreError:
            raise
        except SQLAlchemyError as exc:
            raise MsixUninstallStoreError("MSIX approval consumption failed") from exc

    def close(self) -> None:
        """Dispose database resources."""
        self._engine.dispose()

    def _transaction(self, session: Session, transaction_id: UUID) -> MsixTransactionRow:
        row = session.get(MsixTransactionRow, str(transaction_id))
        if row is None:
            raise MsixUninstallStoreError("unknown MSIX transaction")
        return row

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise MsixUninstallStoreError("MSIX repository is not initialized")


class MsixUninstallExecutionGuard(WriteExecutionGuard):
    """Require consumed durable approval and atomically mark execution started."""

    def __init__(self, repository: MsixUninstallRepository) -> None:
        self._repository = repository

    def require(
        self,
        authorization: ExecutionAuthorization,
        tool_name: str,
        arguments: Mapping[str, JsonValue],
    ) -> None:
        """Fail closed unless the exact reserved request owns one-shot dispatch."""
        try:
            self._repository._require_initialized()
            with self._repository._sessions.begin() as session:
                row = self._repository._transaction(session, authorization.transaction_id)
                runtime = (
                    session.get(MsixConfirmationRow, str(authorization.runtime_confirmation_id))
                    if authorization.runtime_confirmation_id is not None
                    else None
                )
                if (
                    row.state != MsixTransactionState.DISPATCHING.value
                    or row.operation_id != str(authorization.operation_id)
                    or row.plan_id != str(authorization.plan_id)
                    or row.preview_id != str(authorization.preview_id)
                    or row.tool_name != tool_name
                    or row.arguments_digest != authorization.arguments_digest
                    or row.arguments_digest != arguments_digest(arguments)
                    or runtime is None
                    or runtime.state != MsixConfirmationState.CONSUMED.value
                ):
                    raise WriteAuthorizationError("MSIX removal lacks exact durable authorization")
                row.state = MsixTransactionState.EXECUTING.value
                row.updated_at = datetime.now(UTC)
        except MsixUninstallStoreError as exc:
            raise WriteAuthorizationError("MSIX authorization store failed") from exc


def _request_for_preview(preview: MsixUninstallPreview) -> MsixUninstallRequest:
    """Build the sole typed request whose digest may be reserved."""
    return MsixUninstallRequest(
        transaction_id=preview.transaction_id,
        operation_id=preview.operation_id,
        plan_id=preview.plan_id,
        preview_id=preview.preview_id,
        action=ValidatedMsixRemovalAction(
            transaction_id=preview.transaction_id,
            identity=preview.package.identity,
            dependency_digest=preview.dependencies.canonical_digest(),
            assessment_digest=preview.assessment.canonical_digest(),
            preflight_digest=preview.preflight.canonical_digest(),
            validated_at=preview.generated_at,
        ),
    )


def _confirmation_row(confirmation: MsixUninstallConfirmation) -> MsixConfirmationRow:
    """Convert an immutable gate into its privacy-minimized database row."""
    return MsixConfirmationRow(
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


def _any_active_uninstall(session: Session, exclude_msix: UUID | None = None) -> bool:
    """Enforce one active uninstall across MSI, Vendor, winget, and MSIX tables."""
    if any(
        MsixTransactionState(row.state) not in _TERMINAL
        for row in session.scalars(select(MsixTransactionRow))
        if exclude_msix is None or row.transaction_id != str(exclude_msix)
    ):
        return True
    tables = {
        "msi_uninstall_transactions": (
            text("SELECT state FROM msi_uninstall_transactions"),
            {
                "verified_removed",
                "reboot_required",
                "user_cancelled",
                "privilege_required",
                "completed_unverified",
                "failed",
                "interrupted",
                "blocked",
                "cancelled",
            },
        ),
        "vendor_uninstall_transactions": (
            text("SELECT state FROM vendor_uninstall_transactions"),
            {
                "verified_removed",
                "completed_unverified",
                "stopped_monitoring",
                "user_cancelled",
                "failed",
                "interrupted",
                "blocked",
                "cancelled",
            },
        ),
        "winget_uninstall_transactions": (
            text("SELECT state FROM winget_uninstall_transactions"),
            {
                "verified_removed",
                "completed_unverified",
                "reboot_required",
                "privilege_required",
                "cancelled",
                "failed",
                "blocked",
                "interrupted",
            },
        ),
    }
    for table_name, (statement, terminal) in tables.items():
        present = session.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:table_name"),
            {"table_name": table_name},
        ).scalar_one_or_none()
        if present is None:
            continue
        states = session.execute(statement).scalars()
        if any(str(state) not in terminal for state in states):
            return True
    return False
