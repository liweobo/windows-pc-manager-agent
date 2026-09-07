"""SQLite-backed single-use browser confirmations."""

from __future__ import annotations

import threading
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import DateTime, String, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from pc_manager_agent.domain.browser_plans import (
    BrowserConfirmationRecord,
    BrowserConfirmationState,
)
from pc_manager_agent.persistence.database import create_sqlite_engine
from pc_manager_agent.tools.execution import ExecutionAuthorization, arguments_digest


class BrowserAuthorityError(RuntimeError):
    """Raised when durable browser authority is unavailable, stale, or already used."""


class _Base(DeclarativeBase):
    pass


browser_metadata = _Base.metadata


class _BrowserConfirmationRow(_Base):
    __tablename__ = "browser_confirmations"

    confirmation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(36), index=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    page_id: Mapped[str | None] = mapped_column(String(36))
    navigation_id: Mapped[str | None] = mapped_column(String(36))
    plan_digest: Mapped[str] = mapped_column(String(64))
    action_digest: Mapped[str] = mapped_column(String(64))
    origin: Mapped[str] = mapped_column(String(512))
    state: Mapped[str] = mapped_column(String(20), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class BrowserConfirmationRepository:
    """Persist and atomically consume exact browser approvals."""

    def __init__(self, database_path: Path) -> None:
        self._engine = create_sqlite_engine(database_path)
        self._initialized = False

    def initialize(self) -> None:
        """Create schema and invalidate unfinished approvals from an earlier process."""
        try:
            _Base.metadata.create_all(self._engine)
            with self._engine.begin() as connection:
                connection.execute(
                    update(_BrowserConfirmationRow)
                    .where(
                        _BrowserConfirmationRow.state.in_(
                            [
                                BrowserConfirmationState.PENDING.value,
                                BrowserConfirmationState.APPROVED.value,
                            ]
                        )
                    )
                    .values(state=BrowserConfirmationState.INVALIDATED.value)
                )
        except SQLAlchemyError as exc:
            raise BrowserAuthorityError("BROWSER_CONFIRMATION_STORAGE_UNAVAILABLE") from exc
        self._initialized = True

    def create(self, record: BrowserConfirmationRecord) -> None:
        """Store one pending record; duplicate identifiers fail closed."""
        self._require_initialized()
        row = _BrowserConfirmationRow(
            confirmation_id=str(record.confirmation_id),
            plan_id=str(record.plan_id),
            session_id=str(record.session_id),
            page_id=str(record.page_id) if record.page_id else None,
            navigation_id=str(record.navigation_id) if record.navigation_id else None,
            plan_digest=record.plan_digest,
            action_digest=record.action_digest,
            origin=record.origin,
            state=record.state.value,
            created_at=record.created_at,
            expires_at=record.expires_at,
        )
        try:
            with Session(self._engine) as session, session.begin():
                session.add(row)
        except SQLAlchemyError as exc:
            raise BrowserAuthorityError("BROWSER_CONFIRMATION_CREATE_FAILED") from exc

    def resolve(self, confirmation_id: UUID, *, approved: bool, now: datetime) -> None:
        """Transition exactly PENDING to APPROVED or REJECTED before expiry."""
        self._require_initialized()
        target = (
            BrowserConfirmationState.APPROVED if approved else BrowserConfirmationState.REJECTED
        )
        try:
            with self._engine.begin() as connection:
                result = connection.execute(
                    update(_BrowserConfirmationRow)
                    .where(
                        _BrowserConfirmationRow.confirmation_id == str(confirmation_id),
                        _BrowserConfirmationRow.state == BrowserConfirmationState.PENDING.value,
                        _BrowserConfirmationRow.expires_at > now,
                    )
                    .values(state=target.value)
                )
                if result.rowcount != 1:
                    raise BrowserAuthorityError("BROWSER_CONFIRMATION_NOT_PENDING_OR_EXPIRED")
        except SQLAlchemyError as exc:
            raise BrowserAuthorityError("BROWSER_CONFIRMATION_RESOLVE_FAILED") from exc

    def consume(
        self,
        confirmation_id: UUID,
        *,
        plan_digest: str,
        action_digest: str,
        session_id: UUID,
        page_id: UUID | None,
        navigation_id: UUID | None,
        origin: str,
        now: datetime,
    ) -> None:
        """Atomically consume one exact approved authority token."""
        self._require_initialized()
        try:
            with self._engine.begin() as connection:
                result = connection.execute(
                    update(_BrowserConfirmationRow)
                    .where(
                        _BrowserConfirmationRow.confirmation_id == str(confirmation_id),
                        _BrowserConfirmationRow.session_id == str(session_id),
                        _BrowserConfirmationRow.page_id
                        == (str(page_id) if page_id is not None else None),
                        _BrowserConfirmationRow.navigation_id
                        == (str(navigation_id) if navigation_id is not None else None),
                        _BrowserConfirmationRow.plan_digest == plan_digest,
                        _BrowserConfirmationRow.action_digest == action_digest,
                        _BrowserConfirmationRow.origin == origin,
                        _BrowserConfirmationRow.state == BrowserConfirmationState.APPROVED.value,
                        _BrowserConfirmationRow.expires_at > now,
                    )
                    .values(state=BrowserConfirmationState.CONSUMED.value)
                )
                if result.rowcount != 1:
                    raise BrowserAuthorityError("BROWSER_CONFIRMATION_BINDING_MISMATCH")
        except SQLAlchemyError as exc:
            raise BrowserAuthorityError("BROWSER_CONFIRMATION_CONSUME_FAILED") from exc

    def invalidate_session(self, session_id: UUID) -> int:
        """Invalidate pending/approved records after navigation or user takeover."""
        self._require_initialized()
        try:
            with self._engine.begin() as connection:
                result = connection.execute(
                    update(_BrowserConfirmationRow)
                    .where(
                        _BrowserConfirmationRow.session_id == str(session_id),
                        _BrowserConfirmationRow.state.in_(
                            [
                                BrowserConfirmationState.PENDING.value,
                                BrowserConfirmationState.APPROVED.value,
                            ]
                        ),
                    )
                    .values(state=BrowserConfirmationState.INVALIDATED.value)
                )
                return int(result.rowcount or 0)
        except SQLAlchemyError as exc:
            raise BrowserAuthorityError("BROWSER_CONFIRMATION_INVALIDATE_FAILED") from exc

    def state(self, confirmation_id: UUID) -> BrowserConfirmationState:
        """Return the durable state for UI display and tests."""
        self._require_initialized()
        try:
            with Session(self._engine) as session:
                value = session.scalar(
                    select(_BrowserConfirmationRow.state).where(
                        _BrowserConfirmationRow.confirmation_id == str(confirmation_id)
                    )
                )
        except SQLAlchemyError as exc:
            raise BrowserAuthorityError("BROWSER_CONFIRMATION_QUERY_FAILED") from exc
        if value is None:
            raise BrowserAuthorityError("BROWSER_CONFIRMATION_NOT_FOUND")
        return BrowserConfirmationState(value)

    def close(self) -> None:
        """Dispose database connections."""
        self._engine.dispose()
        self._initialized = False

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise BrowserAuthorityError("BROWSER_CONFIRMATION_STORAGE_UNAVAILABLE")


class BrowserWriteExecutionGuard:
    """One-process bridge from consumed durable approval to one registry write call."""

    def __init__(self) -> None:
        self._authorizations: dict[UUID, ExecutionAuthorization] = {}
        self._lock = threading.Lock()

    def issue(
        self,
        *,
        plan_id: UUID,
        operation_id: UUID,
        preview_id: UUID,
        tool_name: str,
        arguments: Mapping[str, object],
    ) -> ExecutionAuthorization:
        """Create an opaque single-use capability after durable confirmation consumption."""
        authorization = ExecutionAuthorization(
            transaction_id=plan_id,
            operation_id=operation_id,
            plan_id=plan_id,
            preview_id=preview_id,
            tool_name=tool_name,
            arguments_digest=arguments_digest(arguments),
        )
        with self._lock:
            self._authorizations[operation_id] = authorization
        return authorization

    def require(
        self,
        authorization: ExecutionAuthorization,
        tool_name: str,
        arguments: Mapping[str, JsonValue],
    ) -> None:
        """Atomically consume only the exact registered tool and argument digest."""
        with self._lock:
            expected = self._authorizations.pop(authorization.operation_id, None)
        if expected is None or expected != authorization:
            raise BrowserAuthorityError("BROWSER_WRITE_AUTHORITY_MISSING_OR_REPLAYED")
        if tool_name != authorization.tool_name:
            raise BrowserAuthorityError("BROWSER_WRITE_TOOL_BINDING_MISMATCH")
        if arguments_digest(arguments) != authorization.arguments_digest:
            raise BrowserAuthorityError("BROWSER_WRITE_ARGUMENT_BINDING_MISMATCH")
