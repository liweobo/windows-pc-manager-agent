"""Encrypted backup vault and durable authorization journal for startup actions."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

from pydantic import JsonValue
from sqlalchemy import JSON, DateTime, LargeBinary, String, Text, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from pc_manager_agent.domain.startup_actions import (
    DisabledStartupRecord,
    StartupActionPlan,
    StartupActionPreview,
    StartupActionTransaction,
    StartupBackupPayload,
    StartupBackupReference,
    StartupErrorCode,
    StartupObservation,
    StartupTransactionState,
)
from pc_manager_agent.persistence.database import create_sqlite_engine
from pc_manager_agent.tools.execution import ExecutionAuthorization, arguments_digest


class StartupStoreError(RuntimeError):
    """Raised when startup backup or authorization persistence fails closed."""


class BackupProtector(Protocol):
    """Current-user encryption boundary for exact startup restore material."""

    def protect(self, plaintext: bytes) -> bytes:
        """Encrypt bytes for the current application user."""
        ...

    def unprotect(self, ciphertext: bytes) -> bytes:
        """Decrypt bytes for the same application user or raise."""
        ...


class StartupBase(DeclarativeBase):
    """Declarative base isolated from audit and earlier-stage tables."""


class StartupBackupRow(StartupBase):
    """Encrypted exact restore payload; audit tables never reference its bytes."""

    __tablename__ = "startup_backups"

    backup_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    identity_digest: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    encrypted_payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified: Mapped[bool] = mapped_column(nullable=False)


class StartupTransactionRow(StartupBase):
    """One startup action and its exact authorization bindings."""

    __tablename__ = "startup_transactions"

    transaction_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    operation_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    plan_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    preview_id: Mapped[str] = mapped_column(String(36), nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    target_name: Mapped[str] = mapped_column(String(500), nullable=False)
    identity_digest: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    plan_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    preview_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    backup_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    backup_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    arguments_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_confirmation_id: Mapped[str | None] = mapped_column(String(36))
    runtime_confirmation_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, JsonValue] | None] = mapped_column(JSON)


class StartupConfirmationRow(StartupBase):
    """Non-secret one-time confirmation evidence."""

    __tablename__ = "startup_confirmations"

    confirmation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    parent_confirmation_id: Mapped[str | None] = mapped_column(String(36))
    transaction_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    tier: Mapped[str] = mapped_column(String(20), nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    plan_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    preview_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    identity_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    backup_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DisabledStartupRow(StartupBase):
    """Agent-disabled entry index; exact restore material remains only in the vault."""

    __tablename__ = "startup_disabled_objects"

    original_transaction_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    backup_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    backup_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    identity: Mapped[dict[str, JsonValue]] = mapped_column(JSON, nullable=False)
    display_name: Mapped[str] = mapped_column(String(500), nullable=False)
    original_observation: Mapped[dict[str, JsonValue]] = mapped_column(JSON, nullable=False)
    disabled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    restored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


_TRANSITIONS: dict[StartupTransactionState, frozenset[StartupTransactionState]] = {
    StartupTransactionState.BACKUP_CREATED: frozenset(
        {StartupTransactionState.PREVIEWED, StartupTransactionState.BLOCKED}
    ),
    StartupTransactionState.PREVIEWED: frozenset(
        {StartupTransactionState.AWAITING_CONFIRMATION, StartupTransactionState.BLOCKED}
    ),
    StartupTransactionState.AWAITING_CONFIRMATION: frozenset(
        {
            StartupTransactionState.AWAITING_RUNTIME_CONFIRMATION,
            StartupTransactionState.CANCELLED,
        }
    ),
    StartupTransactionState.AWAITING_RUNTIME_CONFIRMATION: frozenset(
        {
            StartupTransactionState.CONFIRMED,
            StartupTransactionState.CANCELLED,
            StartupTransactionState.BLOCKED,
            StartupTransactionState.FAILED,
        }
    ),
    StartupTransactionState.CONFIRMED: frozenset({StartupTransactionState.VALIDATING}),
    StartupTransactionState.VALIDATING: frozenset(
        {
            StartupTransactionState.EXECUTING,
            StartupTransactionState.BLOCKED,
            StartupTransactionState.FAILED,
        }
    ),
    StartupTransactionState.EXECUTING: frozenset(
        {
            StartupTransactionState.VERIFYING,
            StartupTransactionState.FAILED,
            StartupTransactionState.ROLLING_BACK,
        }
    ),
    StartupTransactionState.VERIFYING: frozenset(
        {
            StartupTransactionState.COMPLETED,
            StartupTransactionState.FAILED,
            StartupTransactionState.ROLLING_BACK,
        }
    ),
    StartupTransactionState.ROLLING_BACK: frozenset(
        {StartupTransactionState.ROLLED_BACK, StartupTransactionState.ROLLBACK_FAILED}
    ),
}


class StartupBackupVault:
    """Persist exact payloads encrypted for the current Windows user and verify reads."""

    def __init__(self, database_path: Path, protector: BackupProtector) -> None:
        self._engine = create_sqlite_engine(database_path)
        self._sessions = sessionmaker(self._engine, expire_on_commit=False)
        self._protector = protector
        self._initialized = False

    def initialize(self) -> None:
        """Create the additive encrypted-backup table and verify database access."""
        try:
            StartupBase.metadata.create_all(self._engine)
            with self._engine.begin() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError as exc:
            raise StartupStoreError("Startup backup database initialization failed") from exc
        self._initialized = True

    def store(
        self,
        payload: StartupBackupPayload,
        *,
        backup_id: UUID | None = None,
    ) -> StartupBackupReference:
        """Encrypt, commit, immediately decrypt, and digest-check exact restore data."""
        self._require_initialized()
        plaintext = payload.model_dump_json().encode()
        digest = hashlib.sha256(plaintext).hexdigest()
        encrypted = self._protector.protect(plaintext)
        reference = StartupBackupReference(
            backup_id=backup_id or uuid4(),
            identity_digest=payload.original_identity.canonical_digest(),
            payload_digest=digest,
            source=payload.source,
            verified=False,
        )
        row = StartupBackupRow(
            backup_id=str(reference.backup_id),
            identity_digest=reference.identity_digest,
            payload_digest=digest,
            source=payload.source.value,
            encrypted_payload=encrypted,
            created_at=reference.created_at,
            verified=False,
        )
        try:
            with self._sessions.begin() as session:
                session.add(row)
        except SQLAlchemyError as exc:
            raise StartupStoreError("Startup backup persistence failed") from exc
        restored = self._load(
            reference.backup_id,
            expected_digest=digest,
            require_verified=False,
        )
        if restored.canonical_digest() != payload.canonical_digest():
            raise StartupStoreError("Startup backup verification failed")
        try:
            with self._sessions.begin() as session:
                stored = session.get(StartupBackupRow, str(reference.backup_id))
                if stored is None:
                    raise StartupStoreError("Startup backup disappeared during verification")
                stored.verified = True
        except SQLAlchemyError as exc:
            raise StartupStoreError("Startup backup verification flag failed") from exc
        return reference.model_copy(update={"verified": True})

    def load(self, backup_id: UUID, *, expected_digest: str) -> StartupBackupPayload:
        """Decrypt and verify one exact payload before it can reach a write tool."""
        return self._load(backup_id, expected_digest=expected_digest, require_verified=True)

    def _load(
        self,
        backup_id: UUID,
        *,
        expected_digest: str,
        require_verified: bool,
    ) -> StartupBackupPayload:
        self._require_initialized()
        try:
            with self._sessions() as session:
                row = session.get(StartupBackupRow, str(backup_id))
                if row is None:
                    raise StartupStoreError("Startup backup was not found")
                encrypted = bytes(row.encrypted_payload)
                stored_digest = row.payload_digest
                verified = row.verified
        except SQLAlchemyError as exc:
            raise StartupStoreError("Startup backup lookup failed") from exc
        if stored_digest != expected_digest:
            raise StartupStoreError("Startup backup digest binding changed")
        if require_verified and not verified:
            raise StartupStoreError("Startup backup has not completed verification")
        try:
            plaintext = self._protector.unprotect(encrypted)
            if hashlib.sha256(plaintext).hexdigest() != expected_digest:
                raise StartupStoreError("Startup backup content is corrupt")
            return StartupBackupPayload.model_validate_json(plaintext)
        except (ValueError, UnicodeError) as exc:
            raise StartupStoreError("Startup backup could not be decrypted or validated") from exc

    def close(self) -> None:
        """Dispose vault connections and invalidate later reads/writes."""
        self._engine.dispose()
        self._initialized = False

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise StartupStoreError("Startup backup vault is not initialized")


class StartupActionRepository:
    """Persist Preview, confirmation bindings, state, results, and disabled index."""

    def __init__(self, database_path: Path) -> None:
        self._engine = create_sqlite_engine(database_path)
        self._sessions = sessionmaker(self._engine, expire_on_commit=False)
        self._initialized = False

    def initialize(self, *, reconcile_active: bool = True) -> tuple[UUID, ...]:
        """Create tables and optionally reconcile work owned by a prior process run."""
        try:
            StartupBase.metadata.create_all(self._engine)
            with self._engine.begin() as connection:
                connection.execute(text("SELECT 1"))
            interrupted: list[UUID] = []
            active = {
                StartupTransactionState.VALIDATING.value,
                StartupTransactionState.EXECUTING.value,
                StartupTransactionState.VERIFYING.value,
                StartupTransactionState.ROLLING_BACK.value,
            }
            pending = {
                StartupTransactionState.PREVIEWED.value,
                StartupTransactionState.AWAITING_CONFIRMATION.value,
                StartupTransactionState.AWAITING_RUNTIME_CONFIRMATION.value,
                StartupTransactionState.CONFIRMED.value,
            }
            now = datetime.now(UTC)
            with self._sessions.begin() as session:
                rows = (
                    tuple(
                        session.scalars(
                            select(StartupTransactionRow).where(
                                StartupTransactionRow.state.in_(active | pending)
                            )
                        )
                    )
                    if reconcile_active
                    else ()
                )
                for row in rows:
                    if row.state in active:
                        row.state = StartupTransactionState.INTERRUPTED.value
                        interrupted.append(UUID(row.transaction_id))
                        row.error_message = (
                            "Application stopped during a startup mutation; inspect current state"
                        )
                    else:
                        row.state = StartupTransactionState.CANCELLED.value
                        row.error_message = "In-memory confirmation was invalidated by restart"
                    row.updated_at = now
        except SQLAlchemyError as exc:
            raise StartupStoreError("Startup action database initialization failed") from exc
        self._initialized = True
        return tuple(interrupted)

    def create(
        self,
        plan: StartupActionPlan,
        preview: StartupActionPreview,
        tool_name: str,
        argument_payload: Mapping[str, object],
    ) -> StartupActionTransaction:
        """Reserve exact tool arguments and Preview before issuing confirmation."""
        self._require_initialized()
        now = datetime.now(UTC)
        value = StartupActionTransaction(
            transaction_id=plan.transaction_id,
            operation_id=plan.operation_id,
            plan_id=plan.plan_id,
            preview_id=preview.preview_id,
            action=plan.action,
            target_name=plan.target_name,
            identity_digest=plan.target_identity.canonical_digest(),
            state=StartupTransactionState.BACKUP_CREATED,
            plan_digest=plan.canonical_digest(),
            preview_digest=preview.canonical_digest(),
            backup_id=plan.backup_id,
            backup_digest=plan.backup_digest,
            created_at=now,
            updated_at=now,
        )
        row = StartupTransactionRow(
            transaction_id=str(value.transaction_id),
            operation_id=str(value.operation_id),
            plan_id=str(value.plan_id),
            preview_id=str(value.preview_id),
            action=value.action.value,
            target_name=value.target_name,
            identity_digest=value.identity_digest,
            state=value.state.value,
            plan_digest=value.plan_digest,
            preview_digest=value.preview_digest,
            backup_id=str(value.backup_id),
            backup_digest=value.backup_digest,
            tool_name=tool_name,
            arguments_digest=arguments_digest(argument_payload),
            created_at=now,
            updated_at=now,
        )
        try:
            with self._sessions.begin() as session:
                session.add(row)
        except SQLAlchemyError as exc:
            raise StartupStoreError("Startup transaction creation failed") from exc
        return value

    def transition(
        self,
        transaction_id: UUID,
        state: StartupTransactionState,
        *,
        error_code: StartupErrorCode | None = None,
        error_message: str | None = None,
        result: dict[str, JsonValue] | None = None,
    ) -> StartupActionTransaction:
        """Apply one checked transaction-state transition."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                row = session.get(StartupTransactionRow, str(transaction_id))
                if row is None:
                    raise StartupStoreError("Unknown startup transaction")
                current = StartupTransactionState(row.state)
                if state not in _TRANSITIONS.get(current, frozenset()):
                    raise StartupStoreError(
                        f"Invalid startup transition: {current.value} -> {state.value}"
                    )
                row.state = state.value
                row.updated_at = datetime.now(UTC)
                row.error_code = error_code.value if error_code else None
                row.error_message = error_message
                row.result = result
        except SQLAlchemyError as exc:
            raise StartupStoreError("Startup transaction transition failed") from exc
        return self.get(transaction_id)

    def bind_runtime_preview(
        self,
        transaction_id: UUID,
        preview: StartupActionPreview,
    ) -> None:
        """Replace confirmation binding only with a freshly revalidated same-object Preview."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                row = session.get(StartupTransactionRow, str(transaction_id))
                if (
                    row is None
                    or row.state != StartupTransactionState.AWAITING_RUNTIME_CONFIRMATION.value
                    or row.plan_id != str(preview.plan_id)
                    or row.identity_digest != preview.observation.identity.canonical_digest()
                    or row.backup_digest != preview.backup_digest
                ):
                    raise StartupStoreError("Runtime Preview cannot replace stale bindings")
                row.preview_id = str(preview.preview_id)
                row.preview_digest = preview.canonical_digest()
                row.updated_at = datetime.now(UTC)
        except SQLAlchemyError as exc:
            raise StartupStoreError("Startup runtime Preview binding failed") from exc

    def record_confirmation(self, value: object) -> None:
        """Persist a startup confirmation model without importing its exact class."""
        self._require_initialized()
        confirmation = cast(Any, value)
        fields = (
            "confirmation_id",
            "parent_confirmation_id",
            "transaction_id",
            "tier",
            "action",
            "plan_digest",
            "preview_digest",
            "identity_digest",
            "backup_digest",
            "state",
            "confirmed_at",
            "expires_at",
        )
        if not all(hasattr(confirmation, field) for field in fields):
            raise StartupStoreError("Invalid startup confirmation evidence")
        try:
            with self._sessions.begin() as session:
                key = str(confirmation.confirmation_id)
                row = session.get(StartupConfirmationRow, key)
                if row is None:
                    row = StartupConfirmationRow(
                        confirmation_id=key,
                        parent_confirmation_id=(
                            str(confirmation.parent_confirmation_id)
                            if confirmation.parent_confirmation_id
                            else None
                        ),
                        transaction_id=str(confirmation.transaction_id),
                        tier=confirmation.tier.value,
                        action=confirmation.action.value,
                        plan_digest=confirmation.plan_digest,
                        preview_digest=confirmation.preview_digest,
                        identity_digest=confirmation.identity_digest,
                        backup_digest=confirmation.backup_digest,
                        state=confirmation.state.value,
                        confirmed_at=confirmation.confirmed_at,
                        expires_at=confirmation.expires_at,
                    )
                    session.add(row)
                else:
                    row.state = confirmation.state.value
                    row.confirmed_at = confirmation.confirmed_at
        except SQLAlchemyError as exc:
            raise StartupStoreError("Startup confirmation persistence failed") from exc

    def bind_confirmation(
        self,
        transaction_id: UUID,
        confirmation_id: UUID,
        *,
        runtime: bool,
    ) -> None:
        """Bind one resolved confirmation ID to its durable transaction."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                row = session.get(StartupTransactionRow, str(transaction_id))
                if row is None:
                    raise StartupStoreError("Unknown startup transaction")
                if runtime:
                    row.runtime_confirmation_id = str(confirmation_id)
                else:
                    row.plan_confirmation_id = str(confirmation_id)
        except SQLAlchemyError as exc:
            raise StartupStoreError("Startup confirmation binding failed") from exc

    def consume_confirmation_pair(self, runtime_confirmation_id: UUID) -> None:
        """Atomically consume a same-action, same-backup, parent-linked pair exactly once."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                runtime = session.get(StartupConfirmationRow, str(runtime_confirmation_id))
                plan = (
                    session.get(StartupConfirmationRow, runtime.parent_confirmation_id)
                    if runtime is not None and runtime.parent_confirmation_id
                    else None
                )
                if (
                    runtime is None
                    or plan is None
                    or runtime.state != "APPROVED"
                    or plan.state != "APPROVED"
                    or runtime.tier != "RUNTIME"
                    or plan.tier != "PLAN"
                    or runtime.transaction_id != plan.transaction_id
                    or runtime.action != plan.action
                    or runtime.identity_digest != plan.identity_digest
                    or runtime.backup_digest != plan.backup_digest
                ):
                    raise StartupStoreError("Startup confirmation pair is stale or mismatched")
                runtime.state = "CONSUMED"
                plan.state = "CONSUMED"
        except SQLAlchemyError as exc:
            raise StartupStoreError("Startup confirmation consumption failed") from exc

    def record_disabled(
        self,
        transaction_id: UUID,
        preview: StartupActionPreview,
    ) -> DisabledStartupRecord:
        """Index an Agent-disabled object after the mutation has verified successfully."""
        value = DisabledStartupRecord(
            original_transaction_id=transaction_id,
            backup_id=preview.backup_id,
            backup_digest=preview.backup_digest,
            identity=preview.observation.identity,
            display_name=preview.observation.display_name,
            original_observation=preview.observation,
            disabled_at=datetime.now(UTC),
        )
        row = DisabledStartupRow(
            original_transaction_id=str(transaction_id),
            backup_id=str(value.backup_id),
            backup_digest=value.backup_digest,
            identity=value.identity.model_dump(mode="json"),
            display_name=value.display_name,
            original_observation=value.original_observation.model_dump(mode="json"),
            disabled_at=value.disabled_at,
        )
        try:
            with self._sessions.begin() as session:
                session.add(row)
        except SQLAlchemyError as exc:
            raise StartupStoreError("Disabled startup index persistence failed") from exc
        return value

    def record_machine_disabled(
        self,
        transaction_id: UUID,
        backup_id: UUID,
        backup_digest: str,
        observation: StartupObservation,
    ) -> DisabledStartupRecord:
        """Index one verified Stage 4X3 HKLM Run disable without creating R2 authority."""
        self._require_initialized()
        if observation.identity.source.value != "HKLM_RUN" or observation.scope != "ALL_USERS":
            raise StartupStoreError("Only an exact machine Run observation may be indexed")
        value = DisabledStartupRecord(
            original_transaction_id=transaction_id,
            backup_id=backup_id,
            backup_digest=backup_digest,
            identity=observation.identity,
            display_name=observation.display_name,
            original_observation=observation,
            disabled_at=datetime.now(UTC),
        )
        row = DisabledStartupRow(
            original_transaction_id=str(transaction_id),
            backup_id=str(value.backup_id),
            backup_digest=value.backup_digest,
            identity=value.identity.model_dump(mode="json"),
            display_name=value.display_name,
            original_observation=value.original_observation.model_dump(mode="json"),
            disabled_at=value.disabled_at,
        )
        try:
            with self._sessions.begin() as session:
                session.add(row)
        except SQLAlchemyError as exc:
            raise StartupStoreError("Machine startup disabled index persistence failed") from exc
        return value

    def mark_restored(self, backup_id: UUID) -> None:
        """Mark an Agent-disabled object restored without removing recovery history."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                row = session.scalar(
                    select(DisabledStartupRow).where(DisabledStartupRow.backup_id == str(backup_id))
                )
                if row is None or row.restored_at is not None:
                    raise StartupStoreError("Startup disabled record is unavailable")
                row.restored_at = datetime.now(UTC)
        except SQLAlchemyError as exc:
            raise StartupStoreError("Startup restore-index update failed") from exc

    def get_disabled(self, backup_id: UUID) -> DisabledStartupRecord:
        """Return one still-disabled Agent-owned record."""
        self._require_initialized()
        try:
            with self._sessions() as session:
                row = session.scalar(
                    select(DisabledStartupRow).where(
                        DisabledStartupRow.backup_id == str(backup_id),
                        DisabledStartupRow.restored_at.is_(None),
                    )
                )
        except SQLAlchemyError as exc:
            raise StartupStoreError("Disabled startup lookup failed") from exc
        if row is None:
            raise StartupStoreError("Startup disabled record is unavailable")
        return _disabled_from_row(row)

    def list_disabled(self, limit: int = 500) -> tuple[DisabledStartupRecord, ...]:
        """List restorable Agent-disabled entries, newest first and strictly bounded."""
        self._require_initialized()
        statement = (
            select(DisabledStartupRow)
            .where(DisabledStartupRow.restored_at.is_(None))
            .order_by(DisabledStartupRow.disabled_at.desc())
            .limit(max(1, min(limit, 500)))
        )
        try:
            with self._sessions() as session:
                rows = tuple(session.scalars(statement))
        except SQLAlchemyError as exc:
            raise StartupStoreError("Disabled startup history failed") from exc
        return tuple(_disabled_from_row(row) for row in rows)

    def get(self, transaction_id: UUID) -> StartupActionTransaction:
        """Return one durable startup action transaction."""
        self._require_initialized()
        try:
            with self._sessions() as session:
                row = session.get(StartupTransactionRow, str(transaction_id))
        except SQLAlchemyError as exc:
            raise StartupStoreError("Startup transaction lookup failed") from exc
        if row is None:
            raise StartupStoreError("Unknown startup transaction")
        return _transaction_from_row(row)

    def require_execution_authorization(
        self,
        authorization: ExecutionAuthorization,
        tool_name: str,
        argument_payload: Mapping[str, JsonValue],
    ) -> None:
        """Validate exact transaction, arguments, backup, and consumed confirmation pair."""
        self._require_initialized()
        try:
            with self._sessions() as session:
                row = session.get(StartupTransactionRow, str(authorization.transaction_id))
                runtime = (
                    session.get(StartupConfirmationRow, row.runtime_confirmation_id)
                    if row is not None and row.runtime_confirmation_id
                    else None
                )
                plan = (
                    session.get(StartupConfirmationRow, row.plan_confirmation_id)
                    if row is not None and row.plan_confirmation_id
                    else None
                )
                if (
                    row is None
                    or row.state != StartupTransactionState.EXECUTING.value
                    or row.operation_id != str(authorization.operation_id)
                    or row.plan_id != str(authorization.plan_id)
                    or row.preview_id != str(authorization.preview_id)
                    or row.tool_name != tool_name
                    or row.arguments_digest != authorization.arguments_digest
                    or row.arguments_digest != arguments_digest(argument_payload)
                    or runtime is None
                    or plan is None
                    or runtime.state != "CONSUMED"
                    or plan.state != "CONSUMED"
                    or runtime.parent_confirmation_id != plan.confirmation_id
                    or runtime.identity_digest != row.identity_digest
                    or runtime.backup_digest != row.backup_digest
                    or runtime.preview_digest != row.preview_digest
                ):
                    raise StartupStoreError(
                        "Startup write capability lacks exact consumed authorization"
                    )
        except SQLAlchemyError as exc:
            raise StartupStoreError("Startup authorization lookup failed") from exc

    def close(self) -> None:
        """Dispose database connections and invalidate later actions."""
        self._engine.dispose()
        self._initialized = False

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise StartupStoreError("Startup action repository is not initialized")


class StartupExecutionGuard:
    """Tool-registry guard backed by a durable startup transaction."""

    def __init__(self, repository: StartupActionRepository) -> None:
        self._repository = repository

    def require(
        self,
        authorization: ExecutionAuthorization,
        tool_name: str,
        arguments: Mapping[str, JsonValue],
    ) -> None:
        """Delegate exact write-capability validation to the startup journal."""
        self._repository.require_execution_authorization(authorization, tool_name, arguments)


def _transaction_from_row(row: StartupTransactionRow) -> StartupActionTransaction:
    return StartupActionTransaction(
        transaction_id=row.transaction_id,
        operation_id=row.operation_id,
        plan_id=row.plan_id,
        preview_id=row.preview_id,
        action=row.action,
        target_name=row.target_name,
        identity_digest=row.identity_digest,
        state=row.state,
        plan_digest=row.plan_digest,
        preview_digest=row.preview_digest,
        backup_id=row.backup_id,
        backup_digest=row.backup_digest,
        plan_confirmation_id=row.plan_confirmation_id,
        runtime_confirmation_id=row.runtime_confirmation_id,
        created_at=_as_utc(row.created_at),
        updated_at=_as_utc(row.updated_at),
        error_code=row.error_code,
        error_message=row.error_message,
        result=row.result,
    )


def _disabled_from_row(row: DisabledStartupRow) -> DisabledStartupRecord:
    return DisabledStartupRecord(
        original_transaction_id=row.original_transaction_id,
        backup_id=row.backup_id,
        backup_digest=row.backup_digest,
        identity=row.identity,
        display_name=row.display_name,
        original_observation=StartupObservation.model_validate(row.original_observation),
        disabled_at=_as_utc(row.disabled_at),
        restored_at=_as_utc(row.restored_at) if row.restored_at else None,
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
