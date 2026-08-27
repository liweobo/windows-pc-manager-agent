"""Durable Stage 4D2A transaction, confirmation, and one-shot execution guard."""

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

from pc_manager_agent.confirmation.software_uninstall_execution import (
    MsiUninstallConfirmation,
    MsiUninstallConfirmationState,
    MsiUninstallConfirmationTier,
)
from pc_manager_agent.domain.software_uninstall_execution import (
    MsiUninstallPlan,
    MsiUninstallPreview,
    MsiUninstallRequest,
    MsiUninstallTransactionState,
)
from pc_manager_agent.persistence.database import create_sqlite_engine
from pc_manager_agent.tools.execution import (
    ExecutionAuthorization,
    WriteExecutionGuard,
    arguments_digest,
)
from pc_manager_agent.tools.registry import WriteAuthorizationError


class MsiUninstallStoreError(RuntimeError):
    """Raised when uninstall state cannot be durably trusted."""


class MsiUninstallBase(DeclarativeBase):
    """Declarative base isolated from other additive subsystems."""


class MsiUninstallTransactionRow(MsiUninstallBase):
    """Privacy-minimized durable transaction and exact tool reservation."""

    __tablename__ = "msi_uninstall_transactions"

    transaction_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    operation_id: Mapped[str] = mapped_column(String(36), unique=True)
    plan_id: Mapped[str] = mapped_column(String(36), index=True)
    preview_id: Mapped[str] = mapped_column(String(36))
    state: Mapped[str] = mapped_column(String(50), index=True)
    identity_digest: Mapped[str] = mapped_column(String(64), index=True)
    product_code_digest: Mapped[str] = mapped_column(String(64), index=True)
    plan_digest: Mapped[str] = mapped_column(String(64))
    preview_digest: Mapped[str] = mapped_column(String(64))
    invariant_digest: Mapped[str] = mapped_column(String(64))
    risk_level: Mapped[str] = mapped_column(String(30))
    tool_name: Mapped[str] = mapped_column(String(120))
    arguments_digest: Mapped[str] = mapped_column(String(64))
    plan_confirmation_id: Mapped[str | None] = mapped_column(String(36))
    runtime_confirmation_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    installer_result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    verification_result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)


class MsiUninstallConfirmationRow(MsiUninstallBase):
    """Durable exact bindings for both confirmation tiers."""

    __tablename__ = "msi_uninstall_confirmations"

    confirmation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    parent_confirmation_id: Mapped[str | None] = mapped_column(String(36), index=True)
    tier: Mapped[str] = mapped_column(String(20))
    transaction_id: Mapped[str] = mapped_column(String(36), index=True)
    operation_id: Mapped[str] = mapped_column(String(36))
    plan_id: Mapped[str] = mapped_column(String(36))
    preview_id: Mapped[str] = mapped_column(String(36))
    plan_digest: Mapped[str] = mapped_column(String(64))
    preview_digest: Mapped[str] = mapped_column(String(64))
    invariant_digest: Mapped[str] = mapped_column(String(64))
    identity_digest: Mapped[str] = mapped_column(String(64))
    product_code_digest: Mapped[str] = mapped_column(String(64))
    capability_digest: Mapped[str] = mapped_column(String(64))
    safety_digest: Mapped[str] = mapped_column(String(64))
    preflight_digest: Mapped[str] = mapped_column(String(64))
    risk_level: Mapped[str] = mapped_column(String(30))
    object_summary: Mapped[str] = mapped_column(String(1_000))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(20), index=True)


_TERMINAL_STATES = frozenset(
    {
        MsiUninstallTransactionState.VERIFIED_REMOVED,
        MsiUninstallTransactionState.REBOOT_REQUIRED,
        MsiUninstallTransactionState.USER_CANCELLED,
        MsiUninstallTransactionState.PRIVILEGE_REQUIRED,
        MsiUninstallTransactionState.COMPLETED_UNVERIFIED,
        MsiUninstallTransactionState.FAILED,
        MsiUninstallTransactionState.INTERRUPTED,
        MsiUninstallTransactionState.BLOCKED,
        MsiUninstallTransactionState.CANCELLED,
    }
)

_ALLOWED_TRANSITIONS: dict[
    MsiUninstallTransactionState, frozenset[MsiUninstallTransactionState]
] = {
    MsiUninstallTransactionState.PREVIEWED: frozenset(
        {
            MsiUninstallTransactionState.AWAITING_PLAN_CONFIRMATION,
            MsiUninstallTransactionState.BLOCKED,
        }
    ),
    MsiUninstallTransactionState.AWAITING_PLAN_CONFIRMATION: frozenset(
        {MsiUninstallTransactionState.PLAN_CONFIRMED, MsiUninstallTransactionState.CANCELLED}
    ),
    MsiUninstallTransactionState.PLAN_CONFIRMED: frozenset(
        {MsiUninstallTransactionState.VALIDATING, MsiUninstallTransactionState.CANCELLED}
    ),
    MsiUninstallTransactionState.VALIDATING: frozenset(
        {
            MsiUninstallTransactionState.AWAITING_RUNTIME_CONFIRMATION,
            MsiUninstallTransactionState.BLOCKED,
            MsiUninstallTransactionState.FAILED,
        }
    ),
    MsiUninstallTransactionState.AWAITING_RUNTIME_CONFIRMATION: frozenset(
        {MsiUninstallTransactionState.DISPATCHING, MsiUninstallTransactionState.CANCELLED}
    ),
    MsiUninstallTransactionState.DISPATCHING: frozenset(
        {MsiUninstallTransactionState.EXECUTING, MsiUninstallTransactionState.FAILED}
    ),
    MsiUninstallTransactionState.EXECUTING: frozenset(
        {
            MsiUninstallTransactionState.WAITING,
            MsiUninstallTransactionState.INSTALLER_COMPLETED,
            MsiUninstallTransactionState.FAILED,
        }
    ),
    MsiUninstallTransactionState.WAITING: frozenset(
        {
            MsiUninstallTransactionState.INSTALLER_COMPLETED,
            MsiUninstallTransactionState.FAILED,
        }
    ),
    MsiUninstallTransactionState.INSTALLER_COMPLETED: frozenset(
        {MsiUninstallTransactionState.VERIFYING, MsiUninstallTransactionState.FAILED}
    ),
    MsiUninstallTransactionState.VERIFYING: frozenset(_TERMINAL_STATES),
}


class MsiUninstallRepository:
    """Persist exact single-product lifecycle and confirmation capabilities."""

    def __init__(self, database_path: Path) -> None:
        self._engine = create_sqlite_engine(database_path)
        self._sessions = sessionmaker(self._engine, expire_on_commit=False)
        self._initialized = False

    def initialize(self, *, reconcile_active: bool = True) -> tuple[UUID, ...]:
        """Create tables and optionally reconcile work owned by a prior process run."""
        interrupted: list[UUID] = []
        try:
            MsiUninstallBase.metadata.create_all(self._engine)
            with self._engine.begin() as connection:
                connection.execute(text("SELECT 1"))
            current = datetime.now(UTC)
            with self._sessions.begin() as session:
                rows = (
                    tuple(session.scalars(select(MsiUninstallTransactionRow)))
                    if reconcile_active
                    else ()
                )
                for row in rows:
                    state = MsiUninstallTransactionState(row.state)
                    if state in _TERMINAL_STATES:
                        continue
                    if state in {
                        MsiUninstallTransactionState.DISPATCHING,
                        MsiUninstallTransactionState.EXECUTING,
                        MsiUninstallTransactionState.WAITING,
                        MsiUninstallTransactionState.INSTALLER_COMPLETED,
                        MsiUninstallTransactionState.VERIFYING,
                    }:
                        row.state = MsiUninstallTransactionState.INTERRUPTED.value
                        row.error_code = "application_interrupted"
                        row.error_message = (
                            "Application stopped after MSI dispatch; refresh inventory, never retry"
                        )
                        interrupted.append(UUID(row.transaction_id))
                    else:
                        row.state = MsiUninstallTransactionState.CANCELLED.value
                        row.error_message = (
                            "Pending confirmation invalidated by application restart"
                        )
                    row.updated_at = current
                pending = (
                    tuple(
                        session.scalars(
                            select(MsiUninstallConfirmationRow).where(
                                MsiUninstallConfirmationRow.state.in_(
                                    {
                                        MsiUninstallConfirmationState.PENDING.value,
                                        MsiUninstallConfirmationState.APPROVED.value,
                                    }
                                )
                            )
                        )
                    )
                    if reconcile_active
                    else ()
                )
                for confirmation_row in pending:
                    confirmation_row.state = MsiUninstallConfirmationState.EXPIRED.value
        except SQLAlchemyError as exc:
            raise MsiUninstallStoreError("MSI uninstall database initialization failed") from exc
        self._initialized = True
        return tuple(interrupted)

    def create(self, plan: MsiUninstallPlan, preview: MsiUninstallPreview) -> None:
        """Reserve the sole MSI-or-Vendor uninstall workflow before confirmation."""
        self._require_initialized()
        current = datetime.now(UTC)
        request = _request_for_preview(preview)
        try:
            with self._sessions.begin() as session:
                active = tuple(session.scalars(select(MsiUninstallTransactionRow)))
                for existing in active:
                    state = MsiUninstallTransactionState(existing.state)
                    if state not in _TERMINAL_STATES:
                        if existing.identity_digest == plan.identity_digest:
                            raise MsiUninstallStoreError(
                                "An active uninstall already exists for this software identity"
                            )
                        raise MsiUninstallStoreError(
                            "Only one MSI uninstall may be active at a time"
                        )
                if (
                    _active_vendor_transaction(session)
                    or _active_winget_transaction(session)
                    or _active_msix_transaction(session)
                ):
                    raise MsiUninstallStoreError(
                        "Only one MSI, Vendor, or winget uninstall may be active at a time; "
                        "MSIX transactions share the same global slot"
                    )
                session.add(
                    MsiUninstallTransactionRow(
                        transaction_id=str(plan.transaction_id),
                        operation_id=str(plan.operation_id),
                        plan_id=str(plan.plan_id),
                        preview_id=str(preview.preview_id),
                        state=MsiUninstallTransactionState.PREVIEWED.value,
                        identity_digest=plan.identity_digest,
                        product_code_digest=preview.validated_product.product_code_digest,
                        plan_digest=plan.canonical_digest(),
                        preview_digest=preview.canonical_digest(),
                        invariant_digest=preview.invariant_digest(),
                        risk_level=plan.risk_level.value,
                        tool_name=plan.tool_name,
                        arguments_digest=arguments_digest(request.model_dump(mode="json")),
                        plan_confirmation_id=None,
                        runtime_confirmation_id=None,
                        created_at=current,
                        updated_at=current,
                        installer_result=None,
                        verification_result=None,
                        error_code=None,
                        error_message=None,
                    )
                )
        except MsiUninstallStoreError:
            raise
        except SQLAlchemyError as exc:
            raise MsiUninstallStoreError("MSI uninstall transaction creation failed") from exc

    def has_active_uninstall(self) -> bool:
        """Return whether any MSI/Vendor/winget/MSIX transaction owns the global slot."""
        self._require_initialized()
        try:
            with self._sessions() as session:
                msi_active = any(
                    MsiUninstallTransactionState(row.state) not in _TERMINAL_STATES
                    for row in session.scalars(select(MsiUninstallTransactionRow))
                )
                return bool(
                    msi_active
                    or _active_vendor_transaction(session)
                    or _active_winget_transaction(session)
                    or _active_msix_transaction(session)
                )
        except SQLAlchemyError as exc:
            raise MsiUninstallStoreError("Uninstall activity lookup failed") from exc

    def transition(
        self,
        transaction_id: UUID,
        state: MsiUninstallTransactionState,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
        installer_result: dict[str, JsonValue] | None = None,
        verification_result: dict[str, JsonValue] | None = None,
    ) -> None:
        """Apply one checked lifecycle transition with optional minimized result data."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                row = self._transaction(session, transaction_id)
                current = MsiUninstallTransactionState(row.state)
                if state not in _ALLOWED_TRANSITIONS.get(current, frozenset()):
                    raise MsiUninstallStoreError(
                        f"Invalid MSI uninstall transition: {current.value} -> {state.value}"
                    )
                row.state = state.value
                row.updated_at = datetime.now(UTC)
                row.error_code = error_code
                row.error_message = error_message
                if installer_result is not None:
                    row.installer_result = installer_result
                if verification_result is not None:
                    row.verification_result = verification_result
        except MsiUninstallStoreError:
            raise
        except SQLAlchemyError as exc:
            raise MsiUninstallStoreError("MSI uninstall transaction update failed") from exc

    def state(self, transaction_id: UUID) -> MsiUninstallTransactionState:
        """Return the current durable state."""
        self._require_initialized()
        try:
            with self._sessions() as session:
                return MsiUninstallTransactionState(
                    self._transaction(session, transaction_id).state
                )
        except MsiUninstallStoreError:
            raise
        except SQLAlchemyError as exc:
            raise MsiUninstallStoreError("MSI uninstall transaction read failed") from exc

    def save_plan_confirmation(self, confirmation: MsiUninstallConfirmation) -> None:
        """Persist the plan gate and advance PREVIEWED atomically."""
        self._save_confirmation(
            confirmation,
            expected=MsiUninstallTransactionState.PREVIEWED,
            target=MsiUninstallTransactionState.AWAITING_PLAN_CONFIRMATION,
            preview=None,
        )

    def save_runtime_confirmation(
        self,
        confirmation: MsiUninstallConfirmation,
        preview: MsiUninstallPreview,
    ) -> None:
        """Persist fresh evidence and reserve its exact tool arguments."""
        self._save_confirmation(
            confirmation,
            expected=MsiUninstallTransactionState.VALIDATING,
            target=MsiUninstallTransactionState.AWAITING_RUNTIME_CONFIRMATION,
            preview=preview,
        )

    def get_confirmation(self, confirmation_id: UUID) -> MsiUninstallConfirmation:
        """Load one durable confirmation model."""
        self._require_initialized()
        try:
            with self._sessions() as session:
                row = session.get(MsiUninstallConfirmationRow, str(confirmation_id))
                if row is None:
                    raise MsiUninstallStoreError("Unknown MSI uninstall confirmation")
                return _confirmation_from_row(row)
        except MsiUninstallStoreError:
            raise
        except SQLAlchemyError as exc:
            raise MsiUninstallStoreError("MSI confirmation read failed") from exc

    def resolve_confirmation(self, confirmation: MsiUninstallConfirmation) -> None:
        """Persist a confirmation resolution and its corresponding transaction state."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                row = session.get(MsiUninstallConfirmationRow, str(confirmation.confirmation_id))
                if row is None:
                    raise MsiUninstallStoreError("Unknown MSI uninstall confirmation")
                if row.state not in {
                    MsiUninstallConfirmationState.PENDING.value,
                    MsiUninstallConfirmationState.APPROVED.value,
                }:
                    raise MsiUninstallStoreError("MSI confirmation is already terminal")
                row.state = confirmation.state.value
                row.confirmed_at = confirmation.confirmed_at
                transaction = self._transaction(session, confirmation.transaction_id)
                current = MsiUninstallTransactionState(transaction.state)
                if confirmation.tier is MsiUninstallConfirmationTier.PLAN:
                    if confirmation.state is MsiUninstallConfirmationState.APPROVED:
                        expected = MsiUninstallTransactionState.AWAITING_PLAN_CONFIRMATION
                        target = MsiUninstallTransactionState.PLAN_CONFIRMED
                    else:
                        expected = MsiUninstallTransactionState.AWAITING_PLAN_CONFIRMATION
                        target = MsiUninstallTransactionState.CANCELLED
                    if current is not expected:
                        raise MsiUninstallStoreError("Plan confirmation transaction state changed")
                    transaction.state = target.value
                    transaction.plan_confirmation_id = str(confirmation.confirmation_id)
                elif confirmation.state in {
                    MsiUninstallConfirmationState.REJECTED,
                    MsiUninstallConfirmationState.EXPIRED,
                }:
                    if current is MsiUninstallTransactionState.AWAITING_RUNTIME_CONFIRMATION:
                        transaction.state = MsiUninstallTransactionState.CANCELLED.value
                transaction.updated_at = datetime.now(UTC)
        except MsiUninstallStoreError:
            raise
        except SQLAlchemyError as exc:
            raise MsiUninstallStoreError("MSI confirmation update failed") from exc

    def consume_confirmation_pair(
        self,
        plan_confirmation: MsiUninstallConfirmation,
        runtime_confirmation: MsiUninstallConfirmation,
    ) -> None:
        """Atomically consume both approvals and move the transaction to DISPATCHING."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                plan_row = session.get(
                    MsiUninstallConfirmationRow, str(plan_confirmation.confirmation_id)
                )
                runtime_row = session.get(
                    MsiUninstallConfirmationRow, str(runtime_confirmation.confirmation_id)
                )
                if plan_row is None or runtime_row is None:
                    raise MsiUninstallStoreError("MSI confirmation pair is incomplete")
                if (
                    plan_row.state != MsiUninstallConfirmationState.APPROVED.value
                    or runtime_row.state != MsiUninstallConfirmationState.APPROVED.value
                    or runtime_row.parent_confirmation_id != plan_row.confirmation_id
                ):
                    raise MsiUninstallStoreError("MSI confirmation pair is stale or replayed")
                transaction = self._transaction(session, runtime_confirmation.transaction_id)
                if (
                    transaction.state
                    != MsiUninstallTransactionState.AWAITING_RUNTIME_CONFIRMATION.value
                    or transaction.plan_confirmation_id != plan_row.confirmation_id
                    or transaction.preview_id != runtime_row.preview_id
                    or transaction.preview_digest != runtime_row.preview_digest
                    or transaction.invariant_digest != runtime_row.invariant_digest
                    or transaction.identity_digest != runtime_row.identity_digest
                    or transaction.product_code_digest != runtime_row.product_code_digest
                    or transaction.risk_level != runtime_row.risk_level
                ):
                    raise MsiUninstallStoreError("MSI transaction bindings changed")
                plan_row.state = MsiUninstallConfirmationState.CONSUMED.value
                runtime_row.state = MsiUninstallConfirmationState.CONSUMED.value
                transaction.runtime_confirmation_id = runtime_row.confirmation_id
                transaction.state = MsiUninstallTransactionState.DISPATCHING.value
                transaction.updated_at = datetime.now(UTC)
        except MsiUninstallStoreError:
            raise
        except SQLAlchemyError as exc:
            raise MsiUninstallStoreError("MSI confirmation consumption failed") from exc

    def close(self) -> None:
        """Release database resources."""
        self._engine.dispose()
        self._initialized = False

    def _save_confirmation(
        self,
        confirmation: MsiUninstallConfirmation,
        *,
        expected: MsiUninstallTransactionState,
        target: MsiUninstallTransactionState,
        preview: MsiUninstallPreview | None,
    ) -> None:
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                transaction = self._transaction(session, confirmation.transaction_id)
                if MsiUninstallTransactionState(transaction.state) is not expected:
                    raise MsiUninstallStoreError(
                        "MSI transaction state changed before confirmation"
                    )
                if preview is not None:
                    request = _request_for_preview(preview)
                    transaction.preview_id = str(preview.preview_id)
                    transaction.preview_digest = preview.canonical_digest()
                    transaction.invariant_digest = preview.invariant_digest()
                    transaction.arguments_digest = arguments_digest(request.model_dump(mode="json"))
                session.add(_confirmation_to_row(confirmation))
                transaction.state = target.value
                transaction.updated_at = datetime.now(UTC)
        except MsiUninstallStoreError:
            raise
        except SQLAlchemyError as exc:
            raise MsiUninstallStoreError("MSI confirmation creation failed") from exc

    def _transaction(self, session: Session, transaction_id: UUID) -> MsiUninstallTransactionRow:
        row = session.get(MsiUninstallTransactionRow, str(transaction_id))
        if row is None:
            raise MsiUninstallStoreError("Unknown MSI uninstall transaction")
        return row

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise MsiUninstallStoreError("MSI uninstall repository is not initialized")


class MsiUninstallExecutionGuard(WriteExecutionGuard):
    """Require consumed durable confirmation and the exact validated tool arguments."""

    def __init__(self, repository: MsiUninstallRepository) -> None:
        self._repository = repository

    def require(
        self,
        authorization: ExecutionAuthorization,
        tool_name: str,
        arguments: Mapping[str, JsonValue],
    ) -> None:
        """Fail unless this one transaction currently owns the dispatch capability."""
        try:
            self._repository._require_initialized()
            with self._repository._sessions() as session:
                row = self._repository._transaction(session, authorization.transaction_id)
                runtime = (
                    session.get(
                        MsiUninstallConfirmationRow,
                        str(authorization.runtime_confirmation_id),
                    )
                    if authorization.runtime_confirmation_id is not None
                    else None
                )
                actual_digest = arguments_digest(arguments)
                if (
                    row.state != MsiUninstallTransactionState.DISPATCHING.value
                    or row.operation_id != str(authorization.operation_id)
                    or row.plan_id != str(authorization.plan_id)
                    or row.preview_id != str(authorization.preview_id)
                    or row.tool_name != tool_name
                    or row.arguments_digest != authorization.arguments_digest
                    or row.arguments_digest != actual_digest
                    or row.runtime_confirmation_id != str(authorization.runtime_confirmation_id)
                    or runtime is None
                    or runtime.state != MsiUninstallConfirmationState.CONSUMED.value
                ):
                    raise WriteAuthorizationError(
                        "MSI uninstall lacks exact consumed durable authorization"
                    )
                row.state = MsiUninstallTransactionState.EXECUTING.value
                row.updated_at = datetime.now(UTC)
                session.commit()
        except MsiUninstallStoreError as exc:
            raise WriteAuthorizationError("MSI uninstall authorization store failed") from exc


def _request_for_preview(preview: MsiUninstallPreview) -> MsiUninstallRequest:
    return MsiUninstallRequest(
        transaction_id=preview.transaction_id,
        operation_id=preview.operation_id,
        plan_id=preview.plan_id,
        preview_id=preview.preview_id,
        product=preview.validated_product,
    )


def _confirmation_to_row(value: MsiUninstallConfirmation) -> MsiUninstallConfirmationRow:
    return MsiUninstallConfirmationRow(
        confirmation_id=str(value.confirmation_id),
        parent_confirmation_id=(
            str(value.parent_confirmation_id) if value.parent_confirmation_id else None
        ),
        tier=value.tier.value,
        transaction_id=str(value.transaction_id),
        operation_id=str(value.operation_id),
        plan_id=str(value.plan_id),
        preview_id=str(value.preview_id),
        plan_digest=value.plan_digest,
        preview_digest=value.preview_digest,
        invariant_digest=value.invariant_digest,
        identity_digest=value.identity_digest,
        product_code_digest=value.product_code_digest,
        capability_digest=value.capability_digest,
        safety_digest=value.safety_digest,
        preflight_digest=value.preflight_digest,
        risk_level=value.risk_level.value,
        object_summary=value.object_summary,
        requested_at=value.requested_at,
        confirmed_at=value.confirmed_at,
        expires_at=value.expires_at,
        state=value.state.value,
    )


def _confirmation_from_row(row: MsiUninstallConfirmationRow) -> MsiUninstallConfirmation:
    return MsiUninstallConfirmation(
        confirmation_id=UUID(row.confirmation_id),
        parent_confirmation_id=(
            UUID(row.parent_confirmation_id) if row.parent_confirmation_id else None
        ),
        tier=MsiUninstallConfirmationTier(row.tier),
        transaction_id=UUID(row.transaction_id),
        operation_id=UUID(row.operation_id),
        plan_id=UUID(row.plan_id),
        preview_id=UUID(row.preview_id),
        plan_digest=row.plan_digest,
        preview_digest=row.preview_digest,
        invariant_digest=row.invariant_digest,
        identity_digest=row.identity_digest,
        product_code_digest=row.product_code_digest,
        capability_digest=row.capability_digest,
        safety_digest=row.safety_digest,
        preflight_digest=row.preflight_digest,
        risk_level=row.risk_level,
        object_summary=row.object_summary,
        requested_at=_as_utc(row.requested_at),
        confirmed_at=_as_utc(row.confirmed_at) if row.confirmed_at else None,
        expires_at=_as_utc(row.expires_at),
        state=MsiUninstallConfirmationState(row.state),
    )


def _as_utc(value: datetime) -> datetime:
    """Restore UTC awareness because SQLite stores timezone-naive timestamp text."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _active_vendor_transaction(session: Session) -> bool:
    """Read the additive Vendor table when present and identify non-terminal work."""
    present = session.execute(
        text(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='vendor_uninstall_transactions'"
        )
    ).scalar_one_or_none()
    if present is None:
        return False
    terminal = {
        "verified_removed",
        "completed_unverified",
        "stopped_monitoring",
        "user_cancelled",
        "failed",
        "interrupted",
        "blocked",
        "cancelled",
    }
    states = session.execute(text("SELECT state FROM vendor_uninstall_transactions")).scalars()
    return any(str(state) not in terminal for state in states)


def _active_winget_transaction(session: Session) -> bool:
    """Read the additive winget table and identify non-terminal work."""
    present = session.execute(
        text(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='winget_uninstall_transactions'"
        )
    ).scalar_one_or_none()
    if present is None:
        return False
    terminal = {
        "verified_removed",
        "completed_unverified",
        "reboot_required",
        "privilege_required",
        "failed",
        "interrupted",
        "blocked",
        "cancelled",
    }
    states = session.execute(text("SELECT state FROM winget_uninstall_transactions")).scalars()
    return any(str(state) not in terminal for state in states)


def _active_msix_transaction(session: Session) -> bool:
    """Read the additive MSIX table and identify non-terminal work."""
    present = session.execute(
        text(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='msix_uninstall_transactions'"
        )
    ).scalar_one_or_none()
    if present is None:
        return False
    terminal = {
        "verified_removed",
        "completed_unverified",
        "access_denied",
        "cancelled",
        "failed",
        "blocked",
        "interrupted",
    }
    states = session.execute(text("SELECT state FROM msix_uninstall_transactions")).scalars()
    return any(str(state) not in terminal for state in states)
