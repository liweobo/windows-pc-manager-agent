"""Encrypted backups and durable authorization journal for service startup writes."""

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

from pc_manager_agent.domain.service_actions import (
    ServiceStableIdentity,
    ServiceStartupConfiguration,
    ServiceState,
)
from pc_manager_agent.domain.service_startup_actions import (
    ServiceStartupActionPlan,
    ServiceStartupActionPreview,
    ServiceStartupActionTransaction,
    ServiceStartupBackupPayload,
    ServiceStartupBackupReference,
    ServiceStartupChangeRecord,
    ServiceStartupErrorCode,
    ServiceStartupTransactionState,
)
from pc_manager_agent.persistence.database import create_sqlite_engine
from pc_manager_agent.tools.execution import ExecutionAuthorization, arguments_digest


class ServiceStartupStoreError(RuntimeError):
    """Raised when backup, transaction, or authorization persistence fails closed."""


class ServiceStartupBackupProtector(Protocol):
    """Current-user encryption boundary for startup configuration backup payloads."""

    def protect(self, plaintext: bytes) -> bytes:
        """Encrypt bytes for the current application user."""
        ...

    def unprotect(self, ciphertext: bytes) -> bytes:
        """Decrypt bytes for the same application user or raise."""
        ...


class ServiceStartupBase(DeclarativeBase):
    """Declarative base isolated from audit and other feature tables."""


class ServiceStartupBackupRow(ServiceStartupBase):
    """Encrypted exact service configuration backup."""

    __tablename__ = "service_startup_backups"

    backup_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    identity_digest: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    encrypted_payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified: Mapped[bool] = mapped_column(nullable=False)


class ServiceStartupTransactionRow(ServiceStartupBase):
    """One write transaction and all exact authorization bindings."""

    __tablename__ = "service_startup_transactions"

    transaction_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    operation_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    plan_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    preview_id: Mapped[str] = mapped_column(String(36), nullable=False)
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    service_name: Mapped[str] = mapped_column(String(256), nullable=False)
    identity_digest: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_configuration_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    target_configuration_digest: Mapped[str] = mapped_column(String(64), nullable=False)
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


class ServiceStartupConfirmationRow(ServiceStartupBase):
    """Persisted non-secret evidence for a one-time confirmation."""

    __tablename__ = "service_startup_confirmations"

    confirmation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    parent_confirmation_id: Mapped[str | None] = mapped_column(String(36))
    transaction_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    tier: Mapped[str] = mapped_column(String(20), nullable=False)
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    plan_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    preview_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    identity_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    source_configuration_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    target_configuration_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    state_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    impact_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    permission_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    backup_id: Mapped[str] = mapped_column(String(36), nullable=False)
    backup_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ServiceStartupChangeRow(ServiceStartupBase):
    """Index of verified Agent-owned changes that may be conflict-checked and restored."""

    __tablename__ = "service_startup_changes"

    original_transaction_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    backup_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True, index=True)
    backup_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    stable_identity: Mapped[dict[str, JsonValue]] = mapped_column(JSON, nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    original_configuration: Mapped[dict[str, JsonValue]] = mapped_column(JSON, nullable=False)
    written_configuration: Mapped[dict[str, JsonValue]] = mapped_column(JSON, nullable=False)
    original_runtime_state: Mapped[str] = mapped_column(String(30), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    restored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


_TRANSITIONS: dict[ServiceStartupTransactionState, frozenset[ServiceStartupTransactionState]] = {
    ServiceStartupTransactionState.BACKUP_CREATED: frozenset(
        {ServiceStartupTransactionState.PREVIEWED, ServiceStartupTransactionState.BLOCKED}
    ),
    ServiceStartupTransactionState.PREVIEWED: frozenset(
        {
            ServiceStartupTransactionState.AWAITING_CONFIRMATION,
            ServiceStartupTransactionState.BLOCKED,
        }
    ),
    ServiceStartupTransactionState.AWAITING_CONFIRMATION: frozenset(
        {
            ServiceStartupTransactionState.AWAITING_RUNTIME_CONFIRMATION,
            ServiceStartupTransactionState.CANCELLED,
        }
    ),
    ServiceStartupTransactionState.AWAITING_RUNTIME_CONFIRMATION: frozenset(
        {
            ServiceStartupTransactionState.CONFIRMED,
            ServiceStartupTransactionState.CANCELLED,
            ServiceStartupTransactionState.BLOCKED,
            ServiceStartupTransactionState.FAILED,
        }
    ),
    ServiceStartupTransactionState.CONFIRMED: frozenset(
        {ServiceStartupTransactionState.VALIDATING}
    ),
    ServiceStartupTransactionState.VALIDATING: frozenset(
        {
            ServiceStartupTransactionState.EXECUTING,
            ServiceStartupTransactionState.BLOCKED,
            ServiceStartupTransactionState.FAILED,
        }
    ),
    ServiceStartupTransactionState.EXECUTING: frozenset(
        {
            ServiceStartupTransactionState.VERIFYING,
            ServiceStartupTransactionState.FAILED,
        }
    ),
    ServiceStartupTransactionState.VERIFYING: frozenset(
        {
            ServiceStartupTransactionState.COMPLETED,
            ServiceStartupTransactionState.FAILED,
        }
    ),
}


class ServiceStartupBackupVault:
    """Encrypt, persist, reread, and verify startup configuration backup payloads."""

    def __init__(self, database_path: Path, protector: ServiceStartupBackupProtector) -> None:
        self._engine = create_sqlite_engine(database_path)
        self._sessions = sessionmaker(self._engine, expire_on_commit=False)
        self._protector = protector
        self._initialized = False

    def initialize(self) -> None:
        """Create additive backup storage and fail if SQLite cannot be verified."""
        try:
            ServiceStartupBase.metadata.create_all(self._engine)
            with self._engine.begin() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError as exc:
            raise ServiceStartupStoreError(
                "Service startup backup database initialization failed"
            ) from exc
        self._initialized = True

    def store(
        self,
        payload: ServiceStartupBackupPayload,
        *,
        backup_id: UUID | None = None,
    ) -> ServiceStartupBackupReference:
        """Encrypt, commit, decrypt, validate, and mark one backup verified."""
        self._require_initialized()
        plaintext = payload.model_dump_json().encode()
        digest = hashlib.sha256(plaintext).hexdigest()
        encrypted = self._protector.protect(plaintext)
        reference = ServiceStartupBackupReference(
            backup_id=backup_id or uuid4(),
            identity_digest=payload.stable_identity.canonical_digest(),
            payload_digest=digest,
            verified=False,
        )
        row = ServiceStartupBackupRow(
            backup_id=str(reference.backup_id),
            identity_digest=reference.identity_digest,
            payload_digest=digest,
            encrypted_payload=encrypted,
            created_at=reference.created_at,
            verified=False,
        )
        try:
            with self._sessions.begin() as session:
                session.add(row)
        except SQLAlchemyError as exc:
            raise ServiceStartupStoreError("Service startup backup persistence failed") from exc
        restored = self._load(
            reference.backup_id,
            expected_digest=digest,
            require_verified=False,
        )
        if restored.canonical_digest() != payload.canonical_digest():
            raise ServiceStartupStoreError("Service startup backup verification failed")
        try:
            with self._sessions.begin() as session:
                stored = session.get(ServiceStartupBackupRow, str(reference.backup_id))
                if stored is None:
                    raise ServiceStartupStoreError("Service startup backup disappeared")
                stored.verified = True
        except SQLAlchemyError as exc:
            raise ServiceStartupStoreError(
                "Service startup backup verification flag failed"
            ) from exc
        return reference.model_copy(update={"verified": True})

    def load(self, backup_id: UUID, *, expected_digest: str) -> ServiceStartupBackupPayload:
        """Decrypt and verify one backup before it can authorize restore or execution."""
        return self._load(backup_id, expected_digest=expected_digest, require_verified=True)

    def _load(
        self,
        backup_id: UUID,
        *,
        expected_digest: str,
        require_verified: bool,
    ) -> ServiceStartupBackupPayload:
        self._require_initialized()
        try:
            with self._sessions() as session:
                row = session.get(ServiceStartupBackupRow, str(backup_id))
                if row is None:
                    raise ServiceStartupStoreError("Service startup backup was not found")
                encrypted = bytes(row.encrypted_payload)
                stored_digest = row.payload_digest
                verified = row.verified
        except SQLAlchemyError as exc:
            raise ServiceStartupStoreError("Service startup backup lookup failed") from exc
        if stored_digest != expected_digest:
            raise ServiceStartupStoreError("Service startup backup digest binding changed")
        if require_verified and not verified:
            raise ServiceStartupStoreError("Service startup backup is not verified")
        try:
            plaintext = self._protector.unprotect(encrypted)
            if hashlib.sha256(plaintext).hexdigest() != expected_digest:
                raise ServiceStartupStoreError("Service startup backup content is corrupt")
            return ServiceStartupBackupPayload.model_validate_json(plaintext)
        except (ValueError, UnicodeError) as exc:
            raise ServiceStartupStoreError(
                "Service startup backup could not be decrypted or validated"
            ) from exc

    def close(self) -> None:
        """Dispose database connections and invalidate later backup operations."""
        self._engine.dispose()
        self._initialized = False

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise ServiceStartupStoreError("Service startup backup vault is not initialized")


class ServiceStartupActionRepository:
    """Persist Preview, confirmations, authorization, results, and restore history."""

    def __init__(self, database_path: Path) -> None:
        self._engine = create_sqlite_engine(database_path)
        self._sessions = sessionmaker(self._engine, expire_on_commit=False)
        self._initialized = False

    def initialize(self, *, reconcile_active: bool = True) -> tuple[UUID, ...]:
        """Create tables and optionally reconcile writes owned by a prior process run."""
        try:
            ServiceStartupBase.metadata.create_all(self._engine)
            with self._engine.begin() as connection:
                connection.execute(text("SELECT 1"))
            active = {
                ServiceStartupTransactionState.VALIDATING.value,
                ServiceStartupTransactionState.EXECUTING.value,
                ServiceStartupTransactionState.VERIFYING.value,
            }
            pending = {
                ServiceStartupTransactionState.PREVIEWED.value,
                ServiceStartupTransactionState.AWAITING_CONFIRMATION.value,
                ServiceStartupTransactionState.AWAITING_RUNTIME_CONFIRMATION.value,
                ServiceStartupTransactionState.CONFIRMED.value,
            }
            interrupted: list[UUID] = []
            now = datetime.now(UTC)
            with self._sessions.begin() as session:
                rows = (
                    tuple(
                        session.scalars(
                            select(ServiceStartupTransactionRow).where(
                                ServiceStartupTransactionRow.state.in_(active | pending)
                            )
                        )
                    )
                    if reconcile_active
                    else ()
                )
                for row in rows:
                    if row.state in active:
                        row.state = ServiceStartupTransactionState.INTERRUPTED.value
                        interrupted.append(UUID(row.transaction_id))
                        row.error_message = (
                            "Application stopped during service configuration; reread current state"
                        )
                    else:
                        row.state = ServiceStartupTransactionState.CANCELLED.value
                        row.error_message = "In-memory confirmation was invalidated by restart"
                    row.updated_at = now
        except SQLAlchemyError as exc:
            raise ServiceStartupStoreError(
                "Service startup action database initialization failed"
            ) from exc
        self._initialized = True
        return tuple(interrupted)

    def create(
        self,
        plan: ServiceStartupActionPlan,
        preview: ServiceStartupActionPreview,
        tool_name: str,
        argument_payload: Mapping[str, object],
    ) -> ServiceStartupActionTransaction:
        """Reserve exact tool arguments and Preview before confirmation is issued."""
        self._require_initialized()
        now = datetime.now(UTC)
        value = ServiceStartupActionTransaction(
            transaction_id=plan.transaction_id,
            operation_id=plan.operation_id,
            plan_id=plan.plan_id,
            preview_id=preview.preview_id,
            action=plan.action,
            service_name=plan.target_identity.service_name,
            identity_digest=plan.target_identity.canonical_digest(),
            source_configuration_digest=plan.source_configuration.canonical_digest(),
            target_configuration_digest=plan.target_configuration.canonical_digest(),
            state=ServiceStartupTransactionState.BACKUP_CREATED,
            plan_digest=plan.canonical_digest(),
            preview_digest=preview.canonical_digest(),
            backup_id=plan.backup_id,
            backup_digest=plan.backup_digest,
            created_at=now,
            updated_at=now,
        )
        row = ServiceStartupTransactionRow(
            transaction_id=str(value.transaction_id),
            operation_id=str(value.operation_id),
            plan_id=str(value.plan_id),
            preview_id=str(value.preview_id),
            action=value.action.value,
            service_name=value.service_name,
            identity_digest=value.identity_digest,
            source_configuration_digest=value.source_configuration_digest,
            target_configuration_digest=value.target_configuration_digest,
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
            raise ServiceStartupStoreError("Service startup transaction creation failed") from exc
        return value

    def transition(
        self,
        transaction_id: UUID,
        state: ServiceStartupTransactionState,
        *,
        error_code: ServiceStartupErrorCode | None = None,
        error_message: str | None = None,
        result: dict[str, JsonValue] | None = None,
    ) -> ServiceStartupActionTransaction:
        """Apply one checked transaction-state transition."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                row = session.get(ServiceStartupTransactionRow, str(transaction_id))
                if row is None:
                    raise ServiceStartupStoreError("Unknown service startup transaction")
                current = ServiceStartupTransactionState(row.state)
                if state not in _TRANSITIONS.get(current, frozenset()):
                    raise ServiceStartupStoreError(
                        f"Invalid service startup transition: {current.value} -> {state.value}"
                    )
                row.state = state.value
                row.updated_at = datetime.now(UTC)
                row.error_code = error_code.value if error_code else None
                row.error_message = error_message
                row.result = result
        except SQLAlchemyError as exc:
            raise ServiceStartupStoreError("Service startup transaction transition failed") from exc
        return self.get(transaction_id)

    def bind_runtime_preview(
        self,
        transaction_id: UUID,
        preview: ServiceStartupActionPreview,
    ) -> None:
        """Replace bindings only with a freshly revalidated same-object Preview."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                row = session.get(ServiceStartupTransactionRow, str(transaction_id))
                if (
                    row is None
                    or row.state
                    != ServiceStartupTransactionState.AWAITING_RUNTIME_CONFIRMATION.value
                    or row.plan_id != str(preview.plan_id)
                    or row.identity_digest != preview.observation.identity.canonical_digest()
                    or row.backup_digest != preview.backup_digest
                ):
                    raise ServiceStartupStoreError("Runtime Preview cannot replace bindings")
                row.preview_id = str(preview.preview_id)
                row.preview_digest = preview.canonical_digest()
                row.updated_at = datetime.now(UTC)
        except SQLAlchemyError as exc:
            raise ServiceStartupStoreError("Runtime Preview binding failed") from exc

    def record_confirmation(self, value: object) -> None:
        """Persist a confirmation model without coupling to its implementation class."""
        self._require_initialized()
        confirmation = cast(Any, value)
        required = (
            "confirmation_id",
            "parent_confirmation_id",
            "transaction_id",
            "tier",
            "action",
            "plan_digest",
            "preview_digest",
            "identity_digest",
            "source_configuration_digest",
            "target_configuration_digest",
            "state_digest",
            "impact_digest",
            "permission_digest",
            "backup_id",
            "backup_digest",
            "state",
            "confirmed_at",
            "expires_at",
        )
        if not all(hasattr(confirmation, field) for field in required):
            raise ServiceStartupStoreError("Invalid service startup confirmation evidence")
        try:
            with self._sessions.begin() as session:
                key = str(confirmation.confirmation_id)
                row = session.get(ServiceStartupConfirmationRow, key)
                if row is None:
                    row = ServiceStartupConfirmationRow(
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
                        source_configuration_digest=confirmation.source_configuration_digest,
                        target_configuration_digest=confirmation.target_configuration_digest,
                        state_digest=confirmation.state_digest,
                        impact_digest=confirmation.impact_digest,
                        permission_digest=confirmation.permission_digest,
                        backup_id=str(confirmation.backup_id),
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
            raise ServiceStartupStoreError("Confirmation persistence failed") from exc

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
                row = session.get(ServiceStartupTransactionRow, str(transaction_id))
                if row is None:
                    raise ServiceStartupStoreError("Unknown service startup transaction")
                if runtime:
                    row.runtime_confirmation_id = str(confirmation_id)
                else:
                    row.plan_confirmation_id = str(confirmation_id)
        except SQLAlchemyError as exc:
            raise ServiceStartupStoreError("Confirmation binding failed") from exc

    def consume_confirmation_pair(self, runtime_confirmation_id: UUID) -> None:
        """Atomically consume a matching parent/child confirmation pair exactly once."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                runtime = session.get(ServiceStartupConfirmationRow, str(runtime_confirmation_id))
                plan = (
                    session.get(
                        ServiceStartupConfirmationRow,
                        runtime.parent_confirmation_id,
                    )
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
                    or runtime.source_configuration_digest != plan.source_configuration_digest
                    or runtime.target_configuration_digest != plan.target_configuration_digest
                    or runtime.backup_id != plan.backup_id
                    or runtime.backup_digest != plan.backup_digest
                ):
                    raise ServiceStartupStoreError("Confirmation pair is stale or mismatched")
                runtime.state = "CONSUMED"
                plan.state = "CONSUMED"
        except SQLAlchemyError as exc:
            raise ServiceStartupStoreError("Confirmation consumption failed") from exc

    def record_change(
        self,
        plan: ServiceStartupActionPlan,
        preview: ServiceStartupActionPreview,
        *,
        restored_source_backup_id: UUID | None = None,
    ) -> ServiceStartupChangeRecord:
        """Atomically index a verified write and optionally close its restore source."""
        value = ServiceStartupChangeRecord(
            original_transaction_id=plan.transaction_id,
            backup_id=plan.backup_id,
            backup_digest=plan.backup_digest,
            stable_identity=plan.target_identity,
            display_name=plan.display_name,
            original_configuration=plan.source_configuration,
            written_configuration=plan.target_configuration,
            original_runtime_state=preview.observation.state,
            changed_at=datetime.now(UTC),
        )
        row = ServiceStartupChangeRow(
            original_transaction_id=str(value.original_transaction_id),
            backup_id=str(value.backup_id),
            backup_digest=value.backup_digest,
            stable_identity=value.stable_identity.model_dump(mode="json"),
            display_name=value.display_name,
            original_configuration=value.original_configuration.model_dump(mode="json"),
            written_configuration=value.written_configuration.model_dump(mode="json"),
            original_runtime_state=value.original_runtime_state.value,
            changed_at=value.changed_at,
        )
        try:
            with self._sessions.begin() as session:
                if restored_source_backup_id is not None:
                    source = session.scalar(
                        select(ServiceStartupChangeRow).where(
                            ServiceStartupChangeRow.backup_id == str(restored_source_backup_id)
                        )
                    )
                    if source is None or source.restored_at is not None:
                        raise ServiceStartupStoreError(
                            "Service startup restore source is unavailable"
                        )
                    source.restored_at = datetime.now(UTC)
                session.add(row)
        except SQLAlchemyError as exc:
            raise ServiceStartupStoreError("Service startup change history failed") from exc
        return value

    def record_privileged_change(
        self,
        *,
        transaction_id: UUID,
        backup_id: UUID,
        backup_digest: str,
        stable_identity: ServiceStableIdentity,
        display_name: str,
        original_configuration: ServiceStartupConfiguration,
        written_configuration: ServiceStartupConfiguration,
        original_runtime_state: ServiceState,
    ) -> ServiceStartupChangeRecord:
        """Index a verified Broker write without inventing an ordinary-user transaction."""
        self._require_initialized()
        value = ServiceStartupChangeRecord(
            original_transaction_id=transaction_id,
            backup_id=backup_id,
            backup_digest=backup_digest,
            stable_identity=stable_identity,
            display_name=display_name,
            original_configuration=original_configuration,
            written_configuration=written_configuration,
            original_runtime_state=original_runtime_state,
            changed_at=datetime.now(UTC),
        )
        try:
            with self._sessions.begin() as session:
                session.add(
                    ServiceStartupChangeRow(
                        original_transaction_id=str(value.original_transaction_id),
                        backup_id=str(value.backup_id),
                        backup_digest=value.backup_digest,
                        stable_identity=value.stable_identity.model_dump(mode="json"),
                        display_name=value.display_name,
                        original_configuration=value.original_configuration.model_dump(mode="json"),
                        written_configuration=value.written_configuration.model_dump(mode="json"),
                        original_runtime_state=value.original_runtime_state.value,
                        changed_at=value.changed_at,
                    )
                )
        except SQLAlchemyError as exc:
            raise ServiceStartupStoreError(
                "Privileged service startup change history failed"
            ) from exc
        return value

    def mark_restored(self, backup_id: UUID) -> None:
        """Mark the source change restored while retaining its audit history."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                row = session.scalar(
                    select(ServiceStartupChangeRow).where(
                        ServiceStartupChangeRow.backup_id == str(backup_id)
                    )
                )
                if row is None or row.restored_at is not None:
                    raise ServiceStartupStoreError("Service startup change is unavailable")
                row.restored_at = datetime.now(UTC)
        except SQLAlchemyError as exc:
            raise ServiceStartupStoreError("Service startup restore history failed") from exc

    def get_change(self, backup_id: UUID) -> ServiceStartupChangeRecord:
        """Return one unrestored Agent-owned change by backup ID."""
        self._require_initialized()
        try:
            with self._sessions() as session:
                row = session.scalar(
                    select(ServiceStartupChangeRow).where(
                        ServiceStartupChangeRow.backup_id == str(backup_id),
                        ServiceStartupChangeRow.restored_at.is_(None),
                    )
                )
        except SQLAlchemyError as exc:
            raise ServiceStartupStoreError("Service startup change lookup failed") from exc
        if row is None:
            raise ServiceStartupStoreError("Service startup change is unavailable")
        return _change_from_row(row)

    def list_changes(self, limit: int = 500) -> tuple[ServiceStartupChangeRecord, ...]:
        """List restorable Agent-owned changes newest first and strictly bounded."""
        self._require_initialized()
        statement = (
            select(ServiceStartupChangeRow)
            .where(ServiceStartupChangeRow.restored_at.is_(None))
            .order_by(ServiceStartupChangeRow.changed_at.desc())
            .limit(max(1, min(limit, 500)))
        )
        try:
            with self._sessions() as session:
                rows = tuple(session.scalars(statement))
        except SQLAlchemyError as exc:
            raise ServiceStartupStoreError("Service startup change history failed") from exc
        return tuple(_change_from_row(row) for row in rows)

    def get(self, transaction_id: UUID) -> ServiceStartupActionTransaction:
        """Return one durable service startup transaction."""
        self._require_initialized()
        try:
            with self._sessions() as session:
                row = session.get(ServiceStartupTransactionRow, str(transaction_id))
        except SQLAlchemyError as exc:
            raise ServiceStartupStoreError("Service startup transaction lookup failed") from exc
        if row is None:
            raise ServiceStartupStoreError("Unknown service startup transaction")
        return _transaction_from_row(row)

    def require_execution_authorization(
        self,
        authorization: ExecutionAuthorization,
        tool_name: str,
        argument_payload: Mapping[str, JsonValue],
    ) -> None:
        """Validate exact transaction, arguments, and consumed confirmation capability."""
        self._require_initialized()
        try:
            with self._sessions() as session:
                row = session.get(
                    ServiceStartupTransactionRow,
                    str(authorization.transaction_id),
                )
                runtime = (
                    session.get(ServiceStartupConfirmationRow, row.runtime_confirmation_id)
                    if row is not None and row.runtime_confirmation_id
                    else None
                )
                plan = (
                    session.get(ServiceStartupConfirmationRow, row.plan_confirmation_id)
                    if row is not None and row.plan_confirmation_id
                    else None
                )
                if (
                    row is None
                    or row.state != ServiceStartupTransactionState.EXECUTING.value
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
                    or runtime.source_configuration_digest != row.source_configuration_digest
                    or runtime.target_configuration_digest != row.target_configuration_digest
                    or runtime.preview_digest != row.preview_digest
                    or runtime.backup_id != row.backup_id
                    or runtime.backup_digest != row.backup_digest
                ):
                    raise ServiceStartupStoreError(
                        "Service startup write lacks exact consumed authorization"
                    )
        except SQLAlchemyError as exc:
            raise ServiceStartupStoreError("Service startup authorization lookup failed") from exc

    def close(self) -> None:
        """Dispose database connections and invalidate later actions."""
        self._engine.dispose()
        self._initialized = False

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise ServiceStartupStoreError("Service startup repository is not initialized")


class ServiceStartupExecutionGuard:
    """Tool-registry guard backed by a durable service startup transaction."""

    def __init__(self, repository: ServiceStartupActionRepository) -> None:
        self._repository = repository

    def require(
        self,
        authorization: ExecutionAuthorization,
        tool_name: str,
        arguments: Mapping[str, JsonValue],
    ) -> None:
        """Delegate exact write-capability validation to the durable journal."""
        self._repository.require_execution_authorization(authorization, tool_name, arguments)


def _transaction_from_row(
    row: ServiceStartupTransactionRow,
) -> ServiceStartupActionTransaction:
    return ServiceStartupActionTransaction(
        transaction_id=row.transaction_id,
        operation_id=row.operation_id,
        plan_id=row.plan_id,
        preview_id=row.preview_id,
        action=row.action,
        service_name=row.service_name,
        identity_digest=row.identity_digest,
        source_configuration_digest=row.source_configuration_digest,
        target_configuration_digest=row.target_configuration_digest,
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


def _change_from_row(row: ServiceStartupChangeRow) -> ServiceStartupChangeRecord:
    return ServiceStartupChangeRecord(
        original_transaction_id=row.original_transaction_id,
        backup_id=row.backup_id,
        backup_digest=row.backup_digest,
        stable_identity=ServiceStableIdentity.model_validate(row.stable_identity),
        display_name=row.display_name,
        original_configuration=ServiceStartupConfiguration.model_validate(
            row.original_configuration
        ),
        written_configuration=ServiceStartupConfiguration.model_validate(row.written_configuration),
        original_runtime_state=ServiceState(row.original_runtime_state),
        changed_at=_as_utc(row.changed_at),
        restored_at=_as_utc(row.restored_at) if row.restored_at else None,
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
