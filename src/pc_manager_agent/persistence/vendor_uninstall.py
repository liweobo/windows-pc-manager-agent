"""Durable Vendor transaction, confirmation, and one-shot execution guard."""

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

from pc_manager_agent.confirmation.vendor_uninstall import (
    VendorUninstallConfirmation,
    VendorUninstallConfirmationState,
    VendorUninstallConfirmationTier,
)
from pc_manager_agent.domain.software_uninstall_execution import MsiUninstallTransactionState
from pc_manager_agent.domain.vendor_uninstall import (
    VendorUninstallPlan,
    VendorUninstallPreview,
    VendorUninstallRequest,
    VendorUninstallTransactionState,
)
from pc_manager_agent.persistence.database import create_sqlite_engine
from pc_manager_agent.tools.execution import (
    ExecutionAuthorization,
    WriteExecutionGuard,
    arguments_digest,
)
from pc_manager_agent.tools.registry import WriteAuthorizationError


class VendorUninstallStoreError(RuntimeError):
    """Raised when Vendor uninstall state cannot be durably trusted."""


class VendorUninstallBase(DeclarativeBase):
    """Declarative base isolated from earlier additive subsystems."""


class VendorUninstallTransactionRow(VendorUninstallBase):
    """Privacy-minimized durable transaction and exact tool reservation."""

    __tablename__ = "vendor_uninstall_transactions"

    transaction_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    operation_id: Mapped[str] = mapped_column(String(36), unique=True)
    plan_id: Mapped[str] = mapped_column(String(36), index=True)
    preview_id: Mapped[str] = mapped_column(String(36))
    state: Mapped[str] = mapped_column(String(50), index=True)
    identity_digest: Mapped[str] = mapped_column(String(64), index=True)
    vendor_identity_digest: Mapped[str] = mapped_column(String(64), index=True)
    argument_digest: Mapped[str] = mapped_column(String(64))
    executable_file_digest: Mapped[str] = mapped_column(String(64))
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
    process_result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    verification_result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)


class VendorUninstallConfirmationRow(VendorUninstallBase):
    """Durable safe confirmation payload with no raw command or executable path."""

    __tablename__ = "vendor_uninstall_confirmations"

    confirmation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    transaction_id: Mapped[str] = mapped_column(String(36), index=True)
    parent_confirmation_id: Mapped[str | None] = mapped_column(String(36), index=True)
    tier: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(20), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


_TERMINAL_STATES = frozenset(
    {
        VendorUninstallTransactionState.VERIFIED_REMOVED,
        VendorUninstallTransactionState.COMPLETED_UNVERIFIED,
        VendorUninstallTransactionState.STOPPED_MONITORING,
        VendorUninstallTransactionState.USER_CANCELLED,
        VendorUninstallTransactionState.FAILED,
        VendorUninstallTransactionState.INTERRUPTED,
        VendorUninstallTransactionState.BLOCKED,
        VendorUninstallTransactionState.CANCELLED,
    }
)

_ALLOWED_TRANSITIONS: dict[
    VendorUninstallTransactionState, frozenset[VendorUninstallTransactionState]
] = {
    VendorUninstallTransactionState.PREVIEWED: frozenset(
        {
            VendorUninstallTransactionState.AWAITING_PLAN_CONFIRMATION,
            VendorUninstallTransactionState.BLOCKED,
        }
    ),
    VendorUninstallTransactionState.AWAITING_PLAN_CONFIRMATION: frozenset(
        {
            VendorUninstallTransactionState.PLAN_CONFIRMED,
            VendorUninstallTransactionState.CANCELLED,
        }
    ),
    VendorUninstallTransactionState.PLAN_CONFIRMED: frozenset(
        {VendorUninstallTransactionState.VALIDATING, VendorUninstallTransactionState.CANCELLED}
    ),
    VendorUninstallTransactionState.VALIDATING: frozenset(
        {
            VendorUninstallTransactionState.AWAITING_RUNTIME_CONFIRMATION,
            VendorUninstallTransactionState.BLOCKED,
            VendorUninstallTransactionState.FAILED,
        }
    ),
    VendorUninstallTransactionState.AWAITING_RUNTIME_CONFIRMATION: frozenset(
        {VendorUninstallTransactionState.DISPATCHING, VendorUninstallTransactionState.CANCELLED}
    ),
    VendorUninstallTransactionState.DISPATCHING: frozenset(
        {VendorUninstallTransactionState.EXECUTING, VendorUninstallTransactionState.FAILED}
    ),
    VendorUninstallTransactionState.EXECUTING: frozenset(
        {
            VendorUninstallTransactionState.WAITING_FOR_VENDOR_UI,
            VendorUninstallTransactionState.MONITORING,
            VendorUninstallTransactionState.PROCESS_EXITED,
            VendorUninstallTransactionState.STOPPED_MONITORING,
            VendorUninstallTransactionState.FAILED,
        }
    ),
    VendorUninstallTransactionState.WAITING_FOR_VENDOR_UI: frozenset(
        {
            VendorUninstallTransactionState.MONITORING,
            VendorUninstallTransactionState.PROCESS_EXITED,
            VendorUninstallTransactionState.STOPPED_MONITORING,
            VendorUninstallTransactionState.FAILED,
        }
    ),
    VendorUninstallTransactionState.MONITORING: frozenset(
        {
            VendorUninstallTransactionState.PROCESS_EXITED,
            VendorUninstallTransactionState.STOPPED_MONITORING,
            VendorUninstallTransactionState.FAILED,
        }
    ),
    VendorUninstallTransactionState.PROCESS_EXITED: frozenset(
        {VendorUninstallTransactionState.VERIFYING, VendorUninstallTransactionState.FAILED}
    ),
    VendorUninstallTransactionState.VERIFYING: frozenset(_TERMINAL_STATES),
}

_MSI_TERMINAL = frozenset(
    state.value
    for state in MsiUninstallTransactionState
    if state
    in {
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


class VendorUninstallRepository:
    """Persist one exact Vendor lifecycle and its two confirmation capabilities."""

    def __init__(self, database_path: Path) -> None:
        self._engine = create_sqlite_engine(database_path)
        self._sessions = sessionmaker(self._engine, expire_on_commit=False)
        self._initialized = False

    def initialize(self) -> tuple[UUID, ...]:
        """Create tables, expire approvals, and mark dispatched work interrupted."""
        interrupted: list[UUID] = []
        try:
            VendorUninstallBase.metadata.create_all(self._engine)
            current = datetime.now(UTC)
            with self._sessions.begin() as session:
                for row in tuple(session.scalars(select(VendorUninstallTransactionRow))):
                    state = VendorUninstallTransactionState(row.state)
                    if state in _TERMINAL_STATES:
                        continue
                    if state in {
                        VendorUninstallTransactionState.DISPATCHING,
                        VendorUninstallTransactionState.EXECUTING,
                        VendorUninstallTransactionState.WAITING_FOR_VENDOR_UI,
                        VendorUninstallTransactionState.MONITORING,
                        VendorUninstallTransactionState.PROCESS_EXITED,
                        VendorUninstallTransactionState.VERIFYING,
                    }:
                        row.state = VendorUninstallTransactionState.INTERRUPTED.value
                        row.error_code = "application_interrupted"
                        row.error_message = (
                            "Application stopped after Vendor dispatch; refresh state and never "
                            "retry"
                        )
                        interrupted.append(UUID(row.transaction_id))
                    else:
                        row.state = VendorUninstallTransactionState.CANCELLED.value
                        row.error_message = (
                            "Pending confirmation invalidated by application restart"
                        )
                    row.updated_at = current
                for confirmation in tuple(
                    session.scalars(
                        select(VendorUninstallConfirmationRow).where(
                            VendorUninstallConfirmationRow.state.in_(
                                {
                                    VendorUninstallConfirmationState.PENDING.value,
                                    VendorUninstallConfirmationState.APPROVED.value,
                                }
                            )
                        )
                    )
                ):
                    confirmation.state = VendorUninstallConfirmationState.EXPIRED.value
                    confirmation.payload["state"] = VendorUninstallConfirmationState.EXPIRED.value
        except SQLAlchemyError as exc:
            raise VendorUninstallStoreError(
                "Vendor uninstall database initialization failed"
            ) from exc
        self._initialized = True
        return tuple(interrupted)

    def create(self, plan: VendorUninstallPlan, preview: VendorUninstallPreview) -> None:
        """Reserve the sole MSI-or-Vendor uninstall workflow before confirmation."""
        self._require_initialized()
        current = datetime.now(UTC)
        request = _request_for_preview(preview)
        try:
            with self._sessions.begin() as session:
                if (
                    _active_vendor_transaction(session)
                    or _active_msi_transaction(session)
                    or _active_winget_transaction(session)
                    or _active_msix_transaction(session)
                ):
                    raise VendorUninstallStoreError(
                        "Only one MSI, Vendor, or winget uninstall may be active at a time; "
                        "MSIX transactions share the same global slot"
                    )
                session.add(
                    VendorUninstallTransactionRow(
                        transaction_id=str(plan.transaction_id),
                        operation_id=str(plan.operation_id),
                        plan_id=str(plan.plan_id),
                        preview_id=str(preview.preview_id),
                        state=VendorUninstallTransactionState.PREVIEWED.value,
                        identity_digest=plan.identity_digest,
                        vendor_identity_digest=preview.vendor_identity.invariant_digest(),
                        argument_digest=(
                            preview.vendor_identity.argument_assessment.argument_fingerprint
                        ),
                        executable_file_digest=(
                            preview.vendor_identity.executable.file_identity.canonical_digest()
                        ),
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
                        process_result=None,
                        verification_result=None,
                        error_code=None,
                        error_message=None,
                    )
                )
        except VendorUninstallStoreError:
            raise
        except SQLAlchemyError as exc:
            raise VendorUninstallStoreError("Vendor transaction creation failed") from exc

    def has_active_uninstall(self, exclude_vendor_transaction: UUID | None = None) -> bool:
        """Return whether either durable MSI or Vendor workflow is non-terminal."""
        self._require_initialized()
        with self._sessions() as session:
            return (
                _active_vendor_transaction(
                    session,
                    exclude_vendor_transaction,
                )
                or _active_msi_transaction(session)
                or _active_winget_transaction(session)
                or _active_msix_transaction(session)
            )

    def transition(
        self,
        transaction_id: UUID,
        state: VendorUninstallTransactionState,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
        process_result: dict[str, JsonValue] | None = None,
        verification_result: dict[str, JsonValue] | None = None,
    ) -> None:
        """Apply one checked lifecycle transition with minimized result data."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                row = self._transaction(session, transaction_id)
                current = VendorUninstallTransactionState(row.state)
                if state not in _ALLOWED_TRANSITIONS.get(current, frozenset()):
                    raise VendorUninstallStoreError(
                        f"Invalid Vendor transition: {current.value} -> {state.value}"
                    )
                row.state = state.value
                row.updated_at = datetime.now(UTC)
                row.error_code = error_code
                row.error_message = error_message
                if process_result is not None:
                    row.process_result = process_result
                if verification_result is not None:
                    row.verification_result = verification_result
        except VendorUninstallStoreError:
            raise
        except SQLAlchemyError as exc:
            raise VendorUninstallStoreError("Vendor transaction update failed") from exc

    def state(self, transaction_id: UUID) -> VendorUninstallTransactionState:
        """Return one current durable Vendor transaction state."""
        self._require_initialized()
        with self._sessions() as session:
            return VendorUninstallTransactionState(self._transaction(session, transaction_id).state)

    def save_plan_confirmation(self, confirmation: VendorUninstallConfirmation) -> None:
        """Persist the first gate and advance PREVIEWED atomically."""
        self._save_confirmation(
            confirmation,
            expected=VendorUninstallTransactionState.PREVIEWED,
            target=VendorUninstallTransactionState.AWAITING_PLAN_CONFIRMATION,
            preview=None,
        )

    def save_runtime_confirmation(
        self,
        confirmation: VendorUninstallConfirmation,
        preview: VendorUninstallPreview,
    ) -> None:
        """Persist fresh evidence and reserve its exact tool argument digest."""
        self._save_confirmation(
            confirmation,
            expected=VendorUninstallTransactionState.VALIDATING,
            target=VendorUninstallTransactionState.AWAITING_RUNTIME_CONFIRMATION,
            preview=preview,
        )

    def get_confirmation(self, confirmation_id: UUID) -> VendorUninstallConfirmation:
        """Load and validate one durable safe confirmation payload."""
        self._require_initialized()
        with self._sessions() as session:
            row = session.get(VendorUninstallConfirmationRow, str(confirmation_id))
            if row is None:
                raise VendorUninstallStoreError("Unknown Vendor confirmation")
            payload = dict(row.payload)
            payload["state"] = row.state
            return VendorUninstallConfirmation.model_validate(payload)

    def resolve_confirmation(self, confirmation: VendorUninstallConfirmation) -> None:
        """Persist a resolution and its corresponding transaction state."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                row = session.get(
                    VendorUninstallConfirmationRow,
                    str(confirmation.confirmation_id),
                )
                if row is None:
                    raise VendorUninstallStoreError("Unknown Vendor confirmation")
                if row.state not in {
                    VendorUninstallConfirmationState.PENDING.value,
                    VendorUninstallConfirmationState.APPROVED.value,
                }:
                    raise VendorUninstallStoreError("Vendor confirmation is already terminal")
                row.state = confirmation.state.value
                row.payload = confirmation.model_dump(mode="json")
                transaction = self._transaction(session, confirmation.transaction_id)
                current = VendorUninstallTransactionState(transaction.state)
                if confirmation.tier is VendorUninstallConfirmationTier.PLAN:
                    if current is not VendorUninstallTransactionState.AWAITING_PLAN_CONFIRMATION:
                        raise VendorUninstallStoreError(
                            "Vendor plan confirmation transaction changed"
                        )
                    transaction.state = (
                        VendorUninstallTransactionState.PLAN_CONFIRMED.value
                        if confirmation.state is VendorUninstallConfirmationState.APPROVED
                        else VendorUninstallTransactionState.CANCELLED.value
                    )
                    transaction.plan_confirmation_id = str(confirmation.confirmation_id)
                elif (
                    confirmation.state
                    in {
                        VendorUninstallConfirmationState.REJECTED,
                        VendorUninstallConfirmationState.EXPIRED,
                    }
                    and current is VendorUninstallTransactionState.AWAITING_RUNTIME_CONFIRMATION
                ):
                    transaction.state = VendorUninstallTransactionState.CANCELLED.value
                transaction.updated_at = datetime.now(UTC)
        except VendorUninstallStoreError:
            raise
        except SQLAlchemyError as exc:
            raise VendorUninstallStoreError("Vendor confirmation update failed") from exc

    def consume_confirmation_pair(
        self,
        plan_confirmation: VendorUninstallConfirmation,
        runtime_confirmation: VendorUninstallConfirmation,
    ) -> None:
        """Atomically consume both approvals and move to DISPATCHING exactly once."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                plan_row = session.get(
                    VendorUninstallConfirmationRow,
                    str(plan_confirmation.confirmation_id),
                )
                runtime_row = session.get(
                    VendorUninstallConfirmationRow,
                    str(runtime_confirmation.confirmation_id),
                )
                if plan_row is None or runtime_row is None:
                    raise VendorUninstallStoreError("Vendor confirmation pair is incomplete")
                if (
                    plan_row.state != VendorUninstallConfirmationState.APPROVED.value
                    or runtime_row.state != VendorUninstallConfirmationState.APPROVED.value
                    or runtime_row.parent_confirmation_id != plan_row.confirmation_id
                ):
                    raise VendorUninstallStoreError("Vendor confirmation pair is stale or replayed")
                transaction = self._transaction(session, runtime_confirmation.transaction_id)
                if (
                    transaction.state
                    != VendorUninstallTransactionState.AWAITING_RUNTIME_CONFIRMATION.value
                    or transaction.plan_confirmation_id != plan_row.confirmation_id
                    or transaction.preview_id != str(runtime_confirmation.preview_id)
                    or transaction.invariant_digest != runtime_confirmation.invariant_digest
                    or transaction.vendor_identity_digest
                    != runtime_confirmation.vendor_identity_digest
                    or transaction.argument_digest != runtime_confirmation.argument_digest
                    or transaction.executable_file_digest
                    != runtime_confirmation.executable_file_digest
                ):
                    raise VendorUninstallStoreError("Vendor transaction bindings changed")
                plan_row.state = VendorUninstallConfirmationState.CONSUMED.value
                runtime_row.state = VendorUninstallConfirmationState.CONSUMED.value
                plan_row.payload["state"] = VendorUninstallConfirmationState.CONSUMED.value
                runtime_row.payload["state"] = VendorUninstallConfirmationState.CONSUMED.value
                transaction.runtime_confirmation_id = runtime_row.confirmation_id
                transaction.state = VendorUninstallTransactionState.DISPATCHING.value
                transaction.updated_at = datetime.now(UTC)
        except VendorUninstallStoreError:
            raise
        except SQLAlchemyError as exc:
            raise VendorUninstallStoreError("Vendor confirmation consumption failed") from exc

    def close(self) -> None:
        """Release database resources."""
        self._engine.dispose()
        self._initialized = False

    def _save_confirmation(
        self,
        confirmation: VendorUninstallConfirmation,
        *,
        expected: VendorUninstallTransactionState,
        target: VendorUninstallTransactionState,
        preview: VendorUninstallPreview | None,
    ) -> None:
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                transaction = self._transaction(session, confirmation.transaction_id)
                if VendorUninstallTransactionState(transaction.state) is not expected:
                    raise VendorUninstallStoreError(
                        "Vendor transaction state changed before confirmation"
                    )
                if preview is not None:
                    request = _request_for_preview(preview)
                    transaction.preview_id = str(preview.preview_id)
                    transaction.preview_digest = preview.canonical_digest()
                    transaction.invariant_digest = preview.invariant_digest()
                    transaction.vendor_identity_digest = preview.vendor_identity.invariant_digest()
                    transaction.argument_digest = (
                        preview.vendor_identity.argument_assessment.argument_fingerprint
                    )
                    transaction.executable_file_digest = (
                        preview.vendor_identity.executable.file_identity.canonical_digest()
                    )
                    transaction.arguments_digest = arguments_digest(request.model_dump(mode="json"))
                session.add(
                    VendorUninstallConfirmationRow(
                        confirmation_id=str(confirmation.confirmation_id),
                        transaction_id=str(confirmation.transaction_id),
                        parent_confirmation_id=(
                            str(confirmation.parent_confirmation_id)
                            if confirmation.parent_confirmation_id
                            else None
                        ),
                        tier=confirmation.tier.value,
                        state=confirmation.state.value,
                        payload=confirmation.model_dump(mode="json"),
                    )
                )
                transaction.state = target.value
                transaction.updated_at = datetime.now(UTC)
        except VendorUninstallStoreError:
            raise
        except SQLAlchemyError as exc:
            raise VendorUninstallStoreError("Vendor confirmation creation failed") from exc

    def _transaction(
        self,
        session: Session,
        transaction_id: UUID,
    ) -> VendorUninstallTransactionRow:
        row = session.get(VendorUninstallTransactionRow, str(transaction_id))
        if row is None:
            raise VendorUninstallStoreError("Unknown Vendor transaction")
        return row

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise VendorUninstallStoreError("Vendor repository is not initialized")


class VendorUninstallExecutionGuard(WriteExecutionGuard):
    """Require exact consumed durable authorization and atomically mark EXECUTING."""

    def __init__(self, repository: VendorUninstallRepository) -> None:
        self._repository = repository

    def require(
        self,
        authorization: ExecutionAuthorization,
        tool_name: str,
        arguments: Mapping[str, JsonValue],
    ) -> None:
        """Fail closed unless exact reserved arguments own the one-shot dispatch."""
        try:
            self._repository._require_initialized()
            with self._repository._sessions() as session:
                row = self._repository._transaction(session, authorization.transaction_id)
                runtime = (
                    session.get(
                        VendorUninstallConfirmationRow,
                        str(authorization.runtime_confirmation_id),
                    )
                    if authorization.runtime_confirmation_id is not None
                    else None
                )
                actual_digest = arguments_digest(arguments)
                if (
                    row.state != VendorUninstallTransactionState.DISPATCHING.value
                    or row.operation_id != str(authorization.operation_id)
                    or row.plan_id != str(authorization.plan_id)
                    or row.preview_id != str(authorization.preview_id)
                    or row.tool_name != tool_name
                    or row.arguments_digest != authorization.arguments_digest
                    or row.arguments_digest != actual_digest
                    or row.runtime_confirmation_id != str(authorization.runtime_confirmation_id)
                    or runtime is None
                    or runtime.state != VendorUninstallConfirmationState.CONSUMED.value
                ):
                    raise WriteAuthorizationError(
                        "Vendor uninstall lacks exact consumed durable authorization"
                    )
                row.state = VendorUninstallTransactionState.EXECUTING.value
                row.updated_at = datetime.now(UTC)
                session.commit()
        except VendorUninstallStoreError as exc:
            raise WriteAuthorizationError("Vendor authorization store failed") from exc


def _request_for_preview(preview: VendorUninstallPreview) -> VendorUninstallRequest:
    """Build the only typed request whose digest may be reserved for execution."""
    from pc_manager_agent.domain.vendor_uninstall import ValidatedVendorUninstallAction

    return VendorUninstallRequest(
        transaction_id=preview.transaction_id,
        operation_id=preview.operation_id,
        plan_id=preview.plan_id,
        preview_id=preview.preview_id,
        action=ValidatedVendorUninstallAction(
            software_identity_hash=preview.identity_digest,
            vendor_identity=preview.vendor_identity,
            transaction_id=preview.transaction_id,
            validated_at=preview.generated_at,
        ),
    )


def _active_vendor_transaction(
    session: Session,
    exclude_transaction: UUID | None = None,
) -> bool:
    """Return whether any Vendor transaction is not terminal."""
    return any(
        VendorUninstallTransactionState(row.state) not in _TERMINAL_STATES
        for row in session.scalars(select(VendorUninstallTransactionRow))
        if exclude_transaction is None or row.transaction_id != str(exclude_transaction)
    )


def _active_msi_transaction(session: Session) -> bool:
    """Read the additive MSI table when present and identify any non-terminal state."""
    present = session.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='msi_uninstall_transactions'")
    ).scalar_one_or_none()
    if present is None:
        return False
    states = session.execute(text("SELECT state FROM msi_uninstall_transactions")).scalars()
    return any(str(state) not in _MSI_TERMINAL for state in states)


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
