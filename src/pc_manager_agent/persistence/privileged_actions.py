"""Durable Stage 4X1 authorization, confirmation, and replay storage."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import JSON, DateTime, String, UniqueConstraint, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from pc_manager_agent.confirmation.privileged_actions import (
    PrivilegedActionConfirmation,
    PrivilegedConfirmationState,
    PrivilegedConfirmationTier,
)
from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.privileged_actions import (
    PrivilegedActionEnvelope,
    PrivilegedActionPlan,
    PrivilegedActionPreview,
    PrivilegedActionRequest,
    PrivilegedActionType,
    PrivilegedReplayState,
    PrivilegedTransactionState,
    PrivilegeRequirement,
)
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.persistence.database import create_sqlite_engine


class PrivilegedActionStoreError(RuntimeError):
    """Raised when durable authorization state cannot be trusted."""


class PrivilegedReplayError(PrivilegedActionStoreError):
    """Raised when a request is stale, concurrent, or already consumed."""


class PrivilegedActionBase(DeclarativeBase):
    """Isolated declarative base for privileged protocol tables."""


class PrivilegedTransactionRow(PrivilegedActionBase):
    """One exact preparation plan and current fresh Preview."""

    __tablename__ = "privileged_action_transactions"

    plan_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    plan_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    preview_id: Mapped[str] = mapped_column(String(36), nullable=False)
    preview_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    preview_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    action_type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    target_identity_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    object_summary_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)
    privilege_requirement: Mapped[str] = mapped_column(String(60), nullable=False)
    state: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    plan_confirmation_id: Mapped[str | None] = mapped_column(String(36))
    runtime_confirmation_id: Mapped[str | None] = mapped_column(String(36))
    request_id: Mapped[str | None] = mapped_column(String(36), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PrivilegedConfirmationRow(PrivilegedActionBase):
    """Durable exact plan or immediate confirmation."""

    __tablename__ = "privileged_action_confirmations"

    confirmation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    parent_confirmation_id: Mapped[str | None] = mapped_column(String(36), index=True)
    tier: Mapped[str] = mapped_column(String(20), nullable=False)
    action_type: Mapped[str] = mapped_column(String(80), nullable=False)
    plan_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    preview_id: Mapped[str] = mapped_column(String(36), nullable=False)
    plan_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    preview_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    target_identity_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    object_summary_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)
    privilege_requirement: Mapped[str] = mapped_column(String(60), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False, index=True)


class PrivilegedRequestRow(PrivilegedActionBase):
    """Replay record storing only safe request identity and binding evidence."""

    __tablename__ = "privileged_action_requests"
    __table_args__ = (
        UniqueConstraint("request_digest", name="uq_privileged_request_digest"),
        UniqueConstraint("nonce_fingerprint", name="uq_privileged_nonce_fingerprint"),
    )

    request_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    preview_id: Mapped[str] = mapped_column(String(36), nullable=False)
    plan_confirmation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    runtime_confirmation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    nonce_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    action_type: Mapped[str] = mapped_column(String(80), nullable=False)
    target_identity_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)
    privilege_requirement: Mapped[str] = mapped_column(String(60), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    result_code: Mapped[str | None] = mapped_column(String(100))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PrivilegedAuthorizationSnapshot(FrozenModel):
    """Durable plan, Preview, and confirmation evidence read by the Broker."""

    transaction_state: PrivilegedTransactionState
    replay_state: PrivilegedReplayState
    plan: PrivilegedActionPlan
    preview: PrivilegedActionPreview
    plan_confirmation: PrivilegedActionConfirmation
    runtime_confirmation: PrivilegedActionConfirmation
    stored_request_digest: str
    nonce_fingerprint: str


class PrivilegedActionRepository:
    """Fail-closed SQLite repository with atomic confirmation/request consumption."""

    def __init__(self, database_path: Path) -> None:
        self._engine = create_sqlite_engine(database_path)
        self._sessions = sessionmaker(self._engine, expire_on_commit=False)
        self._initialized = False

    def initialize(self) -> tuple[UUID, ...]:
        """Create tables and permanently interrupt every previously active request."""
        interrupted: list[UUID] = []
        try:
            PrivilegedActionBase.metadata.create_all(self._engine)
            now = datetime.now(UTC)
            with self._sessions.begin() as session:
                active = {
                    PrivilegedTransactionState.SIGNED.value,
                    PrivilegedTransactionState.VALIDATING.value,
                    PrivilegedTransactionState.CONSUMING.value,
                    PrivilegedTransactionState.EXECUTING.value,
                    PrivilegedTransactionState.VERIFYING.value,
                }
                rows = tuple(
                    session.scalars(
                        select(PrivilegedTransactionRow).where(
                            PrivilegedTransactionRow.state.in_(active)
                        )
                    )
                )
                for row in rows:
                    interrupted.append(UUID(row.plan_id))
                    row.state = PrivilegedTransactionState.INTERRUPTED.value
                    row.updated_at = now
                    if row.request_id is not None:
                        request = session.get(PrivilegedRequestRow, row.request_id)
                        if request is not None:
                            request.state = PrivilegedReplayState.CONSUMED.value
                            request.result_code = "INTERRUPTED_ON_RESTART"
                            request.updated_at = now
            self._initialized = True
        except (SQLAlchemyError, ValueError) as exc:
            raise PrivilegedActionStoreError(
                "Privileged authorization database initialization failed"
            ) from exc
        return tuple(interrupted)

    def create(
        self,
        plan: PrivilegedActionPlan,
        preview: PrivilegedActionPreview,
    ) -> None:
        """Persist the reviewed plan and initial Preview before requesting approval."""
        self._require_initialized()
        if preview.plan_id != plan.plan_id or preview.plan_hash != plan.canonical_digest():
            raise PrivilegedActionStoreError("Privileged plan and Preview do not match")
        now = datetime.now(UTC)
        try:
            with self._sessions.begin() as session:
                session.add(
                    PrivilegedTransactionRow(
                        plan_id=str(plan.plan_id),
                        plan_hash=plan.canonical_digest(),
                        plan_json=plan.model_dump(mode="json"),
                        preview_id=str(preview.preview_id),
                        preview_hash=preview.canonical_digest(),
                        preview_json=preview.model_dump(mode="json"),
                        action_type=plan.action_type.value,
                        payload_digest=plan.payload_digest,
                        target_identity_hash=plan.target_identity_hash,
                        object_summary_digest=plan.object_summary_digest,
                        risk_level=plan.risk_level.value,
                        privilege_requirement=plan.privilege_requirement.value,
                        state=PrivilegedTransactionState.AWAITING_PLAN_CONFIRMATION.value,
                        created_at=now,
                        updated_at=now,
                    )
                )
        except IntegrityError as exc:
            raise PrivilegedActionStoreError("Privileged plan already exists") from exc
        except SQLAlchemyError as exc:
            raise PrivilegedActionStoreError("Privileged plan persistence failed") from exc

    def save_confirmation(self, confirmation: PrivilegedActionConfirmation) -> None:
        """Insert one confirmation only in its exact expected transaction state."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                transaction = self._transaction(session, confirmation.plan_id)
                expected = (
                    PrivilegedTransactionState.AWAITING_PLAN_CONFIRMATION
                    if confirmation.tier is PrivilegedConfirmationTier.PLAN
                    else PrivilegedTransactionState.AWAITING_RUNTIME_CONFIRMATION
                )
                if transaction.state != expected.value:
                    raise PrivilegedActionStoreError(
                        "Privileged confirmation transaction state changed"
                    )
                session.add(_confirmation_to_row(confirmation))
        except PrivilegedActionStoreError:
            raise
        except IntegrityError as exc:
            raise PrivilegedActionStoreError("Privileged confirmation already exists") from exc
        except SQLAlchemyError as exc:
            raise PrivilegedActionStoreError("Privileged confirmation persistence failed") from exc

    def get_confirmation(self, confirmation_id: UUID) -> PrivilegedActionConfirmation:
        """Load one durable confirmation model."""
        self._require_initialized()
        try:
            with self._sessions() as session:
                row = session.get(PrivilegedConfirmationRow, str(confirmation_id))
                if row is None:
                    raise PrivilegedActionStoreError("Unknown privileged confirmation")
                return _confirmation_from_row(row)
        except PrivilegedActionStoreError:
            raise
        except (SQLAlchemyError, ValidationError, ValueError) as exc:
            raise PrivilegedActionStoreError("Privileged confirmation read failed") from exc

    def update_confirmation(self, confirmation: PrivilegedActionConfirmation) -> None:
        """Persist one legal state transition and advance the transaction."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                row = session.get(PrivilegedConfirmationRow, str(confirmation.confirmation_id))
                if row is None:
                    raise PrivilegedActionStoreError("Unknown privileged confirmation")
                current = PrivilegedConfirmationState(row.state)
                allowed = {
                    PrivilegedConfirmationState.PENDING: {
                        PrivilegedConfirmationState.APPROVED,
                        PrivilegedConfirmationState.REJECTED,
                        PrivilegedConfirmationState.EXPIRED,
                    },
                    PrivilegedConfirmationState.APPROVED: {
                        PrivilegedConfirmationState.EXPIRED,
                    },
                }
                if confirmation.state not in allowed.get(current, set()):
                    raise PrivilegedActionStoreError("Privileged confirmation transition invalid")
                row.state = confirmation.state.value
                row.confirmed_at = confirmation.confirmed_at
                transaction = self._transaction(session, confirmation.plan_id)
                if confirmation.state is PrivilegedConfirmationState.APPROVED:
                    if confirmation.tier is PrivilegedConfirmationTier.PLAN:
                        if (
                            transaction.state
                            != PrivilegedTransactionState.AWAITING_PLAN_CONFIRMATION.value
                        ):
                            raise PrivilegedActionStoreError("Plan confirmation state changed")
                        transaction.plan_confirmation_id = row.confirmation_id
                        transaction.state = PrivilegedTransactionState.PLAN_CONFIRMED.value
                    else:
                        if (
                            transaction.state
                            != PrivilegedTransactionState.AWAITING_RUNTIME_CONFIRMATION.value
                        ):
                            raise PrivilegedActionStoreError("Runtime confirmation state changed")
                        transaction.runtime_confirmation_id = row.confirmation_id
                        transaction.state = PrivilegedTransactionState.AUTHORIZED.value
                elif confirmation.state in {
                    PrivilegedConfirmationState.REJECTED,
                    PrivilegedConfirmationState.EXPIRED,
                }:
                    transaction.state = (
                        PrivilegedTransactionState.REJECTED.value
                        if confirmation.state is PrivilegedConfirmationState.REJECTED
                        else PrivilegedTransactionState.EXPIRED.value
                    )
                transaction.updated_at = datetime.now(UTC)
        except PrivilegedActionStoreError:
            raise
        except (SQLAlchemyError, ValueError) as exc:
            raise PrivilegedActionStoreError("Privileged confirmation update failed") from exc

    def bind_runtime_preview(
        self,
        plan: PrivilegedActionPlan,
        preview: PrivilegedActionPreview,
    ) -> None:
        """Persist fresh runtime evidence after plan approval and before its child gate."""
        self._require_initialized()
        if preview.plan_id != plan.plan_id or preview.plan_hash != plan.canonical_digest():
            raise PrivilegedActionStoreError("Runtime privileged Preview is stale")
        try:
            with self._sessions.begin() as session:
                row = self._transaction(session, plan.plan_id)
                if (
                    row.state != PrivilegedTransactionState.PLAN_CONFIRMED.value
                    or row.plan_hash != plan.canonical_digest()
                    or row.target_identity_hash != preview.target_identity_hash
                ):
                    raise PrivilegedActionStoreError("Privileged plan approval is stale")
                row.preview_id = str(preview.preview_id)
                row.preview_hash = preview.canonical_digest()
                row.preview_json = preview.model_dump(mode="json")
                row.state = PrivilegedTransactionState.AWAITING_RUNTIME_CONFIRMATION.value
                row.updated_at = datetime.now(UTC)
        except PrivilegedActionStoreError:
            raise
        except SQLAlchemyError as exc:
            raise PrivilegedActionStoreError("Runtime Preview persistence failed") from exc

    def register_request(self, envelope: PrivilegedActionEnvelope) -> None:
        """Persist a signed request before any Broker validation or Mock execution."""
        self._require_initialized()
        request = envelope.request
        now = datetime.now(UTC)
        try:
            with self._sessions.begin() as session:
                row = self._transaction(session, request.plan_id)
                if (
                    row.state != PrivilegedTransactionState.AUTHORIZED.value
                    or row.plan_hash != request.plan_hash
                    or row.preview_id != str(request.preview_id)
                    or row.preview_hash != request.preview_hash
                    or row.plan_confirmation_id != str(request.plan_confirmation_id)
                    or row.runtime_confirmation_id != str(request.confirmation_id)
                    or row.action_type != request.action_type.value
                    or row.target_identity_hash != request.target_identity_hash
                    or row.payload_digest != request.payload_digest
                    or row.risk_level != request.risk_level.value
                    or row.privilege_requirement != request.privilege_requirement.value
                ):
                    raise PrivilegedActionStoreError("Signed request bindings are stale")
                session.add(
                    PrivilegedRequestRow(
                        request_id=str(request.request_id),
                        plan_id=str(request.plan_id),
                        preview_id=str(request.preview_id),
                        plan_confirmation_id=str(request.plan_confirmation_id),
                        runtime_confirmation_id=str(request.confirmation_id),
                        request_digest=envelope.request_digest,
                        nonce_fingerprint=nonce_fingerprint(request.nonce),
                        action_type=request.action_type.value,
                        target_identity_hash=request.target_identity_hash,
                        payload_digest=request.payload_digest,
                        risk_level=request.risk_level.value,
                        privilege_requirement=request.privilege_requirement.value,
                        created_at=request.created_at,
                        expires_at=request.expires_at,
                        state=PrivilegedReplayState.CREATED.value,
                        updated_at=now,
                    )
                )
                row.request_id = str(request.request_id)
                row.state = PrivilegedTransactionState.SIGNED.value
                row.updated_at = now
        except PrivilegedActionStoreError:
            raise
        except IntegrityError as exc:
            raise PrivilegedReplayError("Request ID, digest, or nonce was already used") from exc
        except SQLAlchemyError as exc:
            raise PrivilegedActionStoreError("Signed request persistence failed") from exc

    def snapshot(self, request: PrivilegedActionRequest) -> PrivilegedAuthorizationSnapshot:
        """Read all durable bindings without granting or consuming execution authority."""
        self._require_initialized()
        try:
            with self._sessions() as session:
                request_row = session.get(PrivilegedRequestRow, str(request.request_id))
                if request_row is None:
                    raise PrivilegedReplayError("Unknown privileged request")
                transaction = self._transaction(session, request.plan_id)
                plan_row = session.get(PrivilegedConfirmationRow, request_row.plan_confirmation_id)
                runtime_row = session.get(
                    PrivilegedConfirmationRow, request_row.runtime_confirmation_id
                )
                if plan_row is None or runtime_row is None:
                    raise PrivilegedActionStoreError("Confirmation evidence is incomplete")
                return PrivilegedAuthorizationSnapshot(
                    transaction_state=PrivilegedTransactionState(transaction.state),
                    replay_state=PrivilegedReplayState(request_row.state),
                    plan=PrivilegedActionPlan.model_validate(transaction.plan_json),
                    preview=PrivilegedActionPreview.model_validate(transaction.preview_json),
                    plan_confirmation=_confirmation_from_row(plan_row),
                    runtime_confirmation=_confirmation_from_row(runtime_row),
                    stored_request_digest=request_row.request_digest,
                    nonce_fingerprint=request_row.nonce_fingerprint,
                )
        except PrivilegedActionStoreError:
            raise
        except (SQLAlchemyError, ValidationError, ValueError) as exc:
            raise PrivilegedActionStoreError("Privileged authorization read failed") from exc

    def consume(self, request: PrivilegedActionRequest, *, now: datetime) -> None:
        """Atomically reserve one request and consume its exact two confirmations."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                claimed = cast(
                    CursorResult[Any],
                    session.execute(
                        update(PrivilegedRequestRow)
                        .where(
                            PrivilegedRequestRow.request_id == str(request.request_id),
                            PrivilegedRequestRow.state == PrivilegedReplayState.CREATED.value,
                        )
                        .values(
                            state=PrivilegedReplayState.CONSUMING.value,
                            updated_at=now,
                        )
                    ),
                )
                if claimed.rowcount != 1:
                    raise PrivilegedReplayError("Privileged request is stale or replayed")
                request_row = session.get(PrivilegedRequestRow, str(request.request_id))
                transaction = self._transaction(session, request.plan_id)
                plan_row = session.get(PrivilegedConfirmationRow, str(request.plan_confirmation_id))
                runtime_row = session.get(PrivilegedConfirmationRow, str(request.confirmation_id))
                if request_row is None or plan_row is None or runtime_row is None:
                    raise PrivilegedActionStoreError("Authorization rows disappeared")
                if (
                    now >= _as_utc(request_row.expires_at)
                    or transaction.state != PrivilegedTransactionState.SIGNED.value
                    or transaction.request_id != request_row.request_id
                    or transaction.plan_confirmation_id != plan_row.confirmation_id
                    or transaction.runtime_confirmation_id != runtime_row.confirmation_id
                    or plan_row.state != PrivilegedConfirmationState.APPROVED.value
                    or runtime_row.state != PrivilegedConfirmationState.APPROVED.value
                    or runtime_row.parent_confirmation_id != plan_row.confirmation_id
                    or now >= _as_utc(plan_row.expires_at)
                    or now >= _as_utc(runtime_row.expires_at)
                    or request_row.nonce_fingerprint != nonce_fingerprint(request.nonce)
                ):
                    raise PrivilegedReplayError("Authorization expired, changed, or was consumed")
                plan_row.state = PrivilegedConfirmationState.CONSUMED.value
                runtime_row.state = PrivilegedConfirmationState.CONSUMED.value
                transaction.state = PrivilegedTransactionState.CONSUMING.value
                transaction.updated_at = now
        except PrivilegedActionStoreError:
            raise
        except SQLAlchemyError as exc:
            raise PrivilegedActionStoreError("Atomic privileged consumption failed") from exc

    def reject_unconsumed(
        self,
        request_id: UUID,
        *,
        result_code: str,
        expired: bool = False,
    ) -> None:
        """Permanently reject one authentic stale request without consuming confirmations."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                request = session.get(PrivilegedRequestRow, str(request_id))
                if request is None:
                    raise PrivilegedActionStoreError("Unknown privileged request")
                if request.state != PrivilegedReplayState.CREATED.value:
                    raise PrivilegedReplayError("Privileged request is already terminal")
                transaction = self._transaction(session, UUID(request.plan_id))
                request.state = (
                    PrivilegedReplayState.EXPIRED.value
                    if expired
                    else PrivilegedReplayState.REJECTED.value
                )
                request.result_code = result_code
                request.updated_at = datetime.now(UTC)
                transaction.state = (
                    PrivilegedTransactionState.EXPIRED.value
                    if expired
                    else PrivilegedTransactionState.REJECTED.value
                )
                transaction.updated_at = datetime.now(UTC)
        except PrivilegedActionStoreError:
            raise
        except (SQLAlchemyError, ValueError) as exc:
            raise PrivilegedActionStoreError("Privileged request rejection failed") from exc

    def transition(
        self,
        request_id: UUID,
        transaction_state: PrivilegedTransactionState,
        replay_state: PrivilegedReplayState,
        *,
        result_code: str,
    ) -> None:
        """Advance a consumed request without ever reopening it for reuse."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                request = session.get(PrivilegedRequestRow, str(request_id))
                if request is None:
                    raise PrivilegedActionStoreError("Unknown privileged request")
                transaction = self._transaction(session, UUID(request.plan_id))
                if request.state not in {
                    PrivilegedReplayState.CONSUMING.value,
                    PrivilegedReplayState.CONSUMED.value,
                }:
                    raise PrivilegedReplayError("Unconsumed request cannot enter execution state")
                request.state = replay_state.value
                request.result_code = result_code
                request.updated_at = datetime.now(UTC)
                transaction.state = transaction_state.value
                transaction.updated_at = datetime.now(UTC)
        except PrivilegedActionStoreError:
            raise
        except (SQLAlchemyError, ValueError) as exc:
            raise PrivilegedActionStoreError("Privileged transaction transition failed") from exc

    def close(self) -> None:
        """Release SQLite resources."""
        self._engine.dispose()
        self._initialized = False

    def _transaction(self, session: Session, plan_id: UUID) -> PrivilegedTransactionRow:
        row = session.get(PrivilegedTransactionRow, str(plan_id))
        if row is None:
            raise PrivilegedActionStoreError("Unknown privileged action plan")
        return row

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise PrivilegedActionStoreError("Privileged repository is not initialized")


class PrivilegedRequestReplayStore:
    """Narrow replay API backed by durable SQLite state."""

    def __init__(self, repository: PrivilegedActionRepository) -> None:
        self._repository = repository

    def inspect(self, request: PrivilegedActionRequest) -> PrivilegedAuthorizationSnapshot:
        """Return current replay and authorization evidence without consuming it."""
        return self._repository.snapshot(request)

    def consume(self, request: PrivilegedActionRequest, *, now: datetime) -> None:
        """Atomically reserve the request and both confirmations."""
        self._repository.consume(request, now=now)


def nonce_fingerprint(nonce: str) -> str:
    """Hash a cryptographic nonce before persistence or audit."""
    return hashlib.sha256(nonce.encode("ascii")).hexdigest()


def _confirmation_to_row(value: PrivilegedActionConfirmation) -> PrivilegedConfirmationRow:
    return PrivilegedConfirmationRow(
        confirmation_id=str(value.confirmation_id),
        parent_confirmation_id=(
            str(value.parent_confirmation_id) if value.parent_confirmation_id else None
        ),
        tier=value.tier.value,
        action_type=value.action_type.value,
        plan_id=str(value.plan_id),
        preview_id=str(value.preview_id),
        plan_hash=value.plan_hash,
        preview_hash=value.preview_hash,
        payload_digest=value.payload_digest,
        target_identity_hash=value.target_identity_hash,
        object_summary_digest=value.object_summary_digest,
        risk_level=value.risk_level.value,
        privilege_requirement=value.privilege_requirement.value,
        requested_at=value.requested_at,
        confirmed_at=value.confirmed_at,
        expires_at=value.expires_at,
        state=value.state.value,
    )


def _confirmation_from_row(row: PrivilegedConfirmationRow) -> PrivilegedActionConfirmation:
    return PrivilegedActionConfirmation(
        confirmation_id=UUID(row.confirmation_id),
        parent_confirmation_id=(
            UUID(row.parent_confirmation_id) if row.parent_confirmation_id else None
        ),
        tier=PrivilegedConfirmationTier(row.tier),
        action_type=PrivilegedActionType(row.action_type),
        plan_id=UUID(row.plan_id),
        preview_id=UUID(row.preview_id),
        plan_hash=row.plan_hash,
        preview_hash=row.preview_hash,
        payload_digest=row.payload_digest,
        target_identity_hash=row.target_identity_hash,
        object_summary_digest=row.object_summary_digest,
        risk_level=RiskLevel(row.risk_level),
        privilege_requirement=PrivilegeRequirement(row.privilege_requirement),
        requested_at=_as_utc(row.requested_at),
        confirmed_at=_as_utc(row.confirmed_at) if row.confirmed_at else None,
        expires_at=_as_utc(row.expires_at),
        state=PrivilegedConfirmationState(row.state),
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
