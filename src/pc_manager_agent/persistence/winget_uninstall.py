"""Durable winget transaction, confirmations, and one-shot execution guard."""

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

from pc_manager_agent.confirmation.winget_uninstall import (
    WingetUninstallConfirmation,
    WingetUninstallConfirmationState,
    WingetUninstallConfirmationTier,
)
from pc_manager_agent.domain.winget_uninstall import (
    ValidatedWingetUninstallAction,
    WingetUninstallPlan,
    WingetUninstallPreview,
    WingetUninstallRequest,
    WingetUninstallTransactionState,
)
from pc_manager_agent.persistence.database import create_sqlite_engine
from pc_manager_agent.tools.execution import (
    ExecutionAuthorization,
    WriteExecutionGuard,
    arguments_digest,
)
from pc_manager_agent.tools.registry import WriteAuthorizationError


class WingetUninstallStoreError(RuntimeError):
    """Raised when durable package-removal authorization cannot be trusted."""


class WingetUninstallBase(DeclarativeBase):
    """Declarative base isolated from earlier additive persistence modules."""


class WingetUninstallTransactionRow(WingetUninstallBase):
    """Privacy-minimized transaction and exact tool-argument reservation."""

    __tablename__ = "winget_uninstall_transactions"

    transaction_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    operation_id: Mapped[str] = mapped_column(String(36), unique=True)
    plan_id: Mapped[str] = mapped_column(String(36), index=True)
    preview_id: Mapped[str] = mapped_column(String(36))
    state: Mapped[str] = mapped_column(String(50), index=True)
    package_identity_digest: Mapped[str] = mapped_column(String(64), index=True)
    software_identity_digest: Mapped[str] = mapped_column(String(64), index=True)
    executable_identity_digest: Mapped[str] = mapped_column(String(64))
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


class WingetUninstallConfirmationRow(WingetUninstallBase):
    """Durable digest-only confirmation payload; no command or source URL is stored."""

    __tablename__ = "winget_uninstall_confirmations"

    confirmation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    transaction_id: Mapped[str] = mapped_column(String(36), index=True)
    parent_confirmation_id: Mapped[str | None] = mapped_column(String(36), index=True)
    tier: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(20), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


_TERMINAL_STATES = frozenset(
    {
        WingetUninstallTransactionState.VERIFIED_REMOVED,
        WingetUninstallTransactionState.COMPLETED_UNVERIFIED,
        WingetUninstallTransactionState.REBOOT_REQUIRED,
        WingetUninstallTransactionState.PRIVILEGE_REQUIRED,
        WingetUninstallTransactionState.CANCELLED,
        WingetUninstallTransactionState.FAILED,
        WingetUninstallTransactionState.BLOCKED,
        WingetUninstallTransactionState.INTERRUPTED,
    }
)

_ALLOWED_TRANSITIONS: dict[
    WingetUninstallTransactionState, frozenset[WingetUninstallTransactionState]
] = {
    WingetUninstallTransactionState.PREVIEWED: frozenset(
        {
            WingetUninstallTransactionState.AWAITING_PLAN_CONFIRMATION,
            WingetUninstallTransactionState.BLOCKED,
        }
    ),
    WingetUninstallTransactionState.AWAITING_PLAN_CONFIRMATION: frozenset(
        {
            WingetUninstallTransactionState.PLAN_CONFIRMED,
            WingetUninstallTransactionState.CANCELLED,
        }
    ),
    WingetUninstallTransactionState.PLAN_CONFIRMED: frozenset(
        {WingetUninstallTransactionState.VALIDATING, WingetUninstallTransactionState.CANCELLED}
    ),
    WingetUninstallTransactionState.VALIDATING: frozenset(
        {
            WingetUninstallTransactionState.AWAITING_RUNTIME_CONFIRMATION,
            WingetUninstallTransactionState.BLOCKED,
            WingetUninstallTransactionState.FAILED,
        }
    ),
    WingetUninstallTransactionState.AWAITING_RUNTIME_CONFIRMATION: frozenset(
        {WingetUninstallTransactionState.DISPATCHING, WingetUninstallTransactionState.CANCELLED}
    ),
    WingetUninstallTransactionState.DISPATCHING: frozenset(
        {WingetUninstallTransactionState.EXECUTING, WingetUninstallTransactionState.FAILED}
    ),
    WingetUninstallTransactionState.EXECUTING: frozenset(
        {
            WingetUninstallTransactionState.MONITORING,
            WingetUninstallTransactionState.PROCESS_EXITED,
            WingetUninstallTransactionState.CANCELLED,
            WingetUninstallTransactionState.INTERRUPTED,
            WingetUninstallTransactionState.FAILED,
        }
    ),
    WingetUninstallTransactionState.MONITORING: frozenset(
        {
            WingetUninstallTransactionState.PROCESS_EXITED,
            WingetUninstallTransactionState.INTERRUPTED,
            WingetUninstallTransactionState.FAILED,
        }
    ),
    WingetUninstallTransactionState.PROCESS_EXITED: frozenset(
        {WingetUninstallTransactionState.VERIFYING, WingetUninstallTransactionState.FAILED}
    ),
    WingetUninstallTransactionState.VERIFYING: frozenset(_TERMINAL_STATES),
}


class WingetUninstallRepository:
    """Persist one active MSI-or-Vendor-or-winget workflow and exact confirmations."""

    def __init__(self, database_path: Path) -> None:
        self._engine = create_sqlite_engine(database_path)
        self._sessions = sessionmaker(self._engine, expire_on_commit=False)
        self._initialized = False

    def initialize(self) -> tuple[UUID, ...]:
        """Create additive tables, expire gates, and mark abandoned work interrupted."""
        interrupted: list[UUID] = []
        try:
            WingetUninstallBase.metadata.create_all(self._engine)
            current = datetime.now(UTC)
            with self._sessions.begin() as session:
                for transaction_row in session.scalars(select(WingetUninstallTransactionRow)):
                    state = WingetUninstallTransactionState(transaction_row.state)
                    if state not in _TERMINAL_STATES:
                        interrupted.append(UUID(transaction_row.transaction_id))
                        transaction_row.state = WingetUninstallTransactionState.INTERRUPTED.value
                        transaction_row.error_code = "application_restart"
                        transaction_row.error_message = (
                            "Active winget removal was interrupted; it will not retry."
                        )
                        transaction_row.updated_at = current
                for confirmation_row in session.scalars(select(WingetUninstallConfirmationRow)):
                    if confirmation_row.state in {
                        WingetUninstallConfirmationState.PENDING.value,
                        WingetUninstallConfirmationState.APPROVED.value,
                    }:
                        payload = dict(confirmation_row.payload)
                        payload["state"] = WingetUninstallConfirmationState.EXPIRED.value
                        confirmation_row.payload = payload
                        confirmation_row.state = WingetUninstallConfirmationState.EXPIRED.value
        except SQLAlchemyError as exc:
            raise WingetUninstallStoreError("winget repository initialization failed") from exc
        self._initialized = True
        return tuple(interrupted)

    def create(self, plan: WingetUninstallPlan, preview: WingetUninstallPreview) -> None:
        """Reserve the sole uninstall workflow before issuing any confirmation."""
        self._require_initialized()
        executable = preview.availability.executable
        if executable is None:
            raise WingetUninstallStoreError("trusted winget executable is absent")
        request = _request_for_preview(preview)
        current = datetime.now(UTC)
        try:
            with self._sessions.begin() as session:
                if _any_active_uninstall(session):
                    raise WingetUninstallStoreError(
                        "Only one MSI, Vendor, or winget uninstall may be active"
                    )
                session.add(
                    WingetUninstallTransactionRow(
                        transaction_id=str(plan.transaction_id),
                        operation_id=str(plan.operation_id),
                        plan_id=str(plan.plan_id),
                        preview_id=str(preview.preview_id),
                        state=WingetUninstallTransactionState.PREVIEWED.value,
                        package_identity_digest=plan.package_identity_digest,
                        software_identity_digest=plan.software_identity_digest,
                        executable_identity_digest=executable.invariant_digest(),
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
        except WingetUninstallStoreError:
            raise
        except SQLAlchemyError as exc:
            raise WingetUninstallStoreError("winget transaction creation failed") from exc

    def has_active_uninstall(self, exclude_winget_transaction: UUID | None = None) -> bool:
        """Return whether an MSI, Vendor, or other winget workflow is active."""
        self._require_initialized()
        with self._sessions() as session:
            return _any_active_uninstall(session, exclude_winget_transaction)

    def transition(
        self,
        transaction_id: UUID,
        state: WingetUninstallTransactionState,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
        process_result: dict[str, JsonValue] | None = None,
        verification_result: dict[str, JsonValue] | None = None,
    ) -> None:
        """Apply one checked state transition with minimized durable evidence."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                row = self._transaction(session, transaction_id)
                current = WingetUninstallTransactionState(row.state)
                if state not in _ALLOWED_TRANSITIONS.get(current, frozenset()):
                    raise WingetUninstallStoreError(
                        f"Invalid winget transition: {current.value} -> {state.value}"
                    )
                row.state = state.value
                row.updated_at = datetime.now(UTC)
                row.error_code = error_code
                row.error_message = error_message
                if process_result is not None:
                    row.process_result = process_result
                if verification_result is not None:
                    row.verification_result = verification_result
        except WingetUninstallStoreError:
            raise
        except SQLAlchemyError as exc:
            raise WingetUninstallStoreError("winget transaction update failed") from exc

    def state(self, transaction_id: UUID) -> WingetUninstallTransactionState:
        """Return the current durable transaction state."""
        self._require_initialized()
        with self._sessions() as session:
            return WingetUninstallTransactionState(self._transaction(session, transaction_id).state)

    def save_plan_confirmation(self, confirmation: WingetUninstallConfirmation) -> None:
        """Persist the plan gate and advance PREVIEWED atomically."""
        self._save_confirmation(
            confirmation,
            expected=WingetUninstallTransactionState.PREVIEWED,
            target=WingetUninstallTransactionState.AWAITING_PLAN_CONFIRMATION,
            preview=None,
        )

    def save_runtime_confirmation(
        self,
        confirmation: WingetUninstallConfirmation,
        preview: WingetUninstallPreview,
    ) -> None:
        """Persist fresh Preview bindings and the immediate gate atomically."""
        self._save_confirmation(
            confirmation,
            expected=WingetUninstallTransactionState.VALIDATING,
            target=WingetUninstallTransactionState.AWAITING_RUNTIME_CONFIRMATION,
            preview=preview,
        )

    def get_confirmation(self, confirmation_id: UUID) -> WingetUninstallConfirmation:
        """Load and validate one confirmation payload."""
        self._require_initialized()
        with self._sessions() as session:
            row = session.get(WingetUninstallConfirmationRow, str(confirmation_id))
            if row is None:
                raise WingetUninstallStoreError("unknown winget confirmation")
            payload = dict(row.payload)
            payload["state"] = row.state
            return WingetUninstallConfirmation.model_validate(payload)

    def resolve_confirmation(self, confirmation: WingetUninstallConfirmation) -> None:
        """Persist a resolution and its corresponding transaction state."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                row = session.get(
                    WingetUninstallConfirmationRow,
                    str(confirmation.confirmation_id),
                )
                if row is None:
                    raise WingetUninstallStoreError("unknown winget confirmation")
                if row.state not in {
                    WingetUninstallConfirmationState.PENDING.value,
                    WingetUninstallConfirmationState.APPROVED.value,
                }:
                    raise WingetUninstallStoreError("winget confirmation is terminal")
                row.state = confirmation.state.value
                row.payload = confirmation.model_dump(mode="json")
                transaction = self._transaction(session, confirmation.transaction_id)
                current = WingetUninstallTransactionState(transaction.state)
                if confirmation.tier is WingetUninstallConfirmationTier.PLAN:
                    if current is not WingetUninstallTransactionState.AWAITING_PLAN_CONFIRMATION:
                        raise WingetUninstallStoreError("winget plan transaction changed")
                    transaction.state = (
                        WingetUninstallTransactionState.PLAN_CONFIRMED.value
                        if confirmation.state is WingetUninstallConfirmationState.APPROVED
                        else WingetUninstallTransactionState.CANCELLED.value
                    )
                    transaction.plan_confirmation_id = str(confirmation.confirmation_id)
                elif (
                    confirmation.state
                    in {
                        WingetUninstallConfirmationState.REJECTED,
                        WingetUninstallConfirmationState.EXPIRED,
                    }
                    and current is WingetUninstallTransactionState.AWAITING_RUNTIME_CONFIRMATION
                ):
                    transaction.state = WingetUninstallTransactionState.CANCELLED.value
                transaction.updated_at = datetime.now(UTC)
        except WingetUninstallStoreError:
            raise
        except SQLAlchemyError as exc:
            raise WingetUninstallStoreError("winget confirmation update failed") from exc

    def consume_confirmation_pair(
        self,
        plan_confirmation: WingetUninstallConfirmation,
        runtime_confirmation: WingetUninstallConfirmation,
    ) -> None:
        """Consume both approvals and move to DISPATCHING exactly once."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                plan_row = session.get(
                    WingetUninstallConfirmationRow,
                    str(plan_confirmation.confirmation_id),
                )
                runtime_row = session.get(
                    WingetUninstallConfirmationRow,
                    str(runtime_confirmation.confirmation_id),
                )
                if plan_row is None or runtime_row is None:
                    raise WingetUninstallStoreError("winget confirmation pair is incomplete")
                if (
                    plan_row.state != WingetUninstallConfirmationState.APPROVED.value
                    or runtime_row.state != WingetUninstallConfirmationState.APPROVED.value
                    or runtime_row.parent_confirmation_id != plan_row.confirmation_id
                ):
                    raise WingetUninstallStoreError("winget confirmation pair is stale or replayed")
                transaction = self._transaction(session, runtime_confirmation.transaction_id)
                if (
                    transaction.state
                    != WingetUninstallTransactionState.AWAITING_RUNTIME_CONFIRMATION.value
                    or transaction.plan_confirmation_id != plan_row.confirmation_id
                    or transaction.preview_id != str(runtime_confirmation.preview_id)
                    or transaction.invariant_digest != runtime_confirmation.invariant_digest
                    or transaction.package_identity_digest
                    != runtime_confirmation.package_identity_digest
                    or transaction.software_identity_digest
                    != runtime_confirmation.software_identity_digest
                    or transaction.executable_identity_digest
                    != runtime_confirmation.executable_identity_digest
                ):
                    raise WingetUninstallStoreError("winget transaction bindings changed")
                plan_payload = dict(plan_row.payload)
                runtime_payload = dict(runtime_row.payload)
                plan_payload["state"] = WingetUninstallConfirmationState.CONSUMED.value
                runtime_payload["state"] = WingetUninstallConfirmationState.CONSUMED.value
                plan_row.payload = plan_payload
                runtime_row.payload = runtime_payload
                plan_row.state = WingetUninstallConfirmationState.CONSUMED.value
                runtime_row.state = WingetUninstallConfirmationState.CONSUMED.value
                transaction.runtime_confirmation_id = runtime_row.confirmation_id
                transaction.state = WingetUninstallTransactionState.DISPATCHING.value
                transaction.updated_at = datetime.now(UTC)
        except WingetUninstallStoreError:
            raise
        except SQLAlchemyError as exc:
            raise WingetUninstallStoreError("winget confirmation consumption failed") from exc

    def close(self) -> None:
        """Release database resources."""
        self._engine.dispose()
        self._initialized = False

    def _save_confirmation(
        self,
        confirmation: WingetUninstallConfirmation,
        *,
        expected: WingetUninstallTransactionState,
        target: WingetUninstallTransactionState,
        preview: WingetUninstallPreview | None,
    ) -> None:
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                transaction = self._transaction(session, confirmation.transaction_id)
                if WingetUninstallTransactionState(transaction.state) is not expected:
                    raise WingetUninstallStoreError(
                        "winget transaction changed before confirmation"
                    )
                if preview is not None:
                    request = _request_for_preview(preview)
                    executable = preview.availability.executable
                    if executable is None:
                        raise WingetUninstallStoreError("trusted winget executable is absent")
                    transaction.preview_id = str(preview.preview_id)
                    transaction.preview_digest = preview.canonical_digest()
                    transaction.invariant_digest = preview.invariant_digest()
                    transaction.executable_identity_digest = executable.invariant_digest()
                    transaction.arguments_digest = arguments_digest(request.model_dump(mode="json"))
                session.add(
                    WingetUninstallConfirmationRow(
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
        except WingetUninstallStoreError:
            raise
        except SQLAlchemyError as exc:
            raise WingetUninstallStoreError("winget confirmation creation failed") from exc

    def _transaction(
        self,
        session: Session,
        transaction_id: UUID,
    ) -> WingetUninstallTransactionRow:
        row = session.get(WingetUninstallTransactionRow, str(transaction_id))
        if row is None:
            raise WingetUninstallStoreError("unknown winget transaction")
        return row

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise WingetUninstallStoreError("winget repository is not initialized")


class WingetUninstallExecutionGuard(WriteExecutionGuard):
    """Require consumed durable authorization and atomically mark EXECUTING."""

    def __init__(self, repository: WingetUninstallRepository) -> None:
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
                        WingetUninstallConfirmationRow,
                        str(authorization.runtime_confirmation_id),
                    )
                    if authorization.runtime_confirmation_id is not None
                    else None
                )
                actual_digest = arguments_digest(arguments)
                if (
                    row.state != WingetUninstallTransactionState.DISPATCHING.value
                    or row.operation_id != str(authorization.operation_id)
                    or row.plan_id != str(authorization.plan_id)
                    or row.preview_id != str(authorization.preview_id)
                    or row.tool_name != tool_name
                    or row.arguments_digest != authorization.arguments_digest
                    or row.arguments_digest != actual_digest
                    or row.runtime_confirmation_id != str(authorization.runtime_confirmation_id)
                    or runtime is None
                    or runtime.state != WingetUninstallConfirmationState.CONSUMED.value
                ):
                    raise WriteAuthorizationError(
                        "winget uninstall lacks exact consumed durable authorization"
                    )
                row.state = WingetUninstallTransactionState.EXECUTING.value
                row.updated_at = datetime.now(UTC)
                session.commit()
        except WingetUninstallStoreError as exc:
            raise WriteAuthorizationError("winget authorization store failed") from exc


def _request_for_preview(preview: WingetUninstallPreview) -> WingetUninstallRequest:
    """Build the only typed request whose digest may be reserved."""
    executable = preview.availability.executable
    if executable is None:
        raise WingetUninstallStoreError("trusted winget executable is absent")
    return WingetUninstallRequest(
        transaction_id=preview.transaction_id,
        operation_id=preview.operation_id,
        plan_id=preview.plan_id,
        preview_id=preview.preview_id,
        action=ValidatedWingetUninstallAction(
            transaction_id=preview.transaction_id,
            package_identity=preview.package.identity,
            software_identity_digest=preview.software.identity.canonical_digest(),
            executable_identity=executable,
            validated_at=preview.generated_at,
        ),
    )


def _any_active_uninstall(
    session: Session,
    exclude_winget_transaction: UUID | None = None,
) -> bool:
    """Enforce one global MSI-or-Vendor-or-winget uninstall transaction."""
    if any(
        WingetUninstallTransactionState(row.state) not in _TERMINAL_STATES
        for row in session.scalars(select(WingetUninstallTransactionRow))
        if exclude_winget_transaction is None
        or row.transaction_id != str(exclude_winget_transaction)
    ):
        return True
    terminal_by_table = {
        "msi_uninstall_transactions": {
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
        "vendor_uninstall_transactions": {
            "verified_removed",
            "completed_unverified",
            "stopped_monitoring",
            "user_cancelled",
            "failed",
            "interrupted",
            "blocked",
            "cancelled",
        },
        "msix_uninstall_transactions": {
            "verified_removed",
            "completed_unverified",
            "access_denied",
            "cancelled",
            "failed",
            "blocked",
            "interrupted",
        },
    }
    for table_name, terminal in terminal_by_table.items():
        present = session.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:table_name"),
            {"table_name": table_name},
        ).scalar_one_or_none()
        if present is None:
            continue
        statements = {
            "msi_uninstall_transactions": text("SELECT state FROM msi_uninstall_transactions"),
            "vendor_uninstall_transactions": text(
                "SELECT state FROM vendor_uninstall_transactions"
            ),
            "msix_uninstall_transactions": text("SELECT state FROM msix_uninstall_transactions"),
        }
        statement = statements[table_name]
        states = session.execute(statement).scalars()
        if any(str(state) not in terminal for state in states):
            return True
    return False
