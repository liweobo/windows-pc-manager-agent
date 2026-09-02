"""Independent SQLite persistence for user-controlled Memory and value-free events."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from sqlalchemy import Boolean, Integer, String, Text, delete, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from pc_manager_agent.domain.memory import (
    MemoryCandidate,
    MemoryConfidence,
    MemoryEntry,
    MemoryEvent,
    MemoryEventAction,
    MemoryQuery,
    MemorySensitivity,
    MemorySourceType,
)
from pc_manager_agent.persistence.database import create_sqlite_engine


class MemoryStoreError(RuntimeError):
    """Raised when Memory storage cannot preserve truthful state."""


class MemoryBase(DeclarativeBase):
    """Independent additive Memory tables."""


class MemoryEntryRow(MemoryBase):
    """One live Memory value; prohibited values never reach this table."""

    __tablename__ = "memory_entries"
    memory_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    logical_digest: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    category: Mapped[str] = mapped_column(String(50), index=True)
    scope: Mapped[str] = mapped_column(String(40), index=True)
    key: Mapped[str] = mapped_column(String(80), index=True)
    value: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(40))
    source_ref: Mapped[str | None] = mapped_column(String(200))
    confidence: Mapped[str] = mapped_column(String(20))
    sensitivity: Mapped[str] = mapped_column(String(20))
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String(40))
    updated_at: Mapped[str] = mapped_column(String(40), index=True)
    expires_at: Mapped[str | None] = mapped_column(String(40), index=True)
    payload_digest: Mapped[str] = mapped_column(String(64))


class MemoryEventRow(MemoryBase):
    """Value-free event history used for local transparency."""

    __tablename__ = "memory_events"
    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    memory_id: Mapped[str | None] = mapped_column(String(36), index=True)
    action: Mapped[str] = mapped_column(String(30), index=True)
    scope: Mapped[str | None] = mapped_column(String(40), index=True)
    key_digest: Mapped[str | None] = mapped_column(String(64))
    reason_code: Mapped[str] = mapped_column(String(80))
    affected_count: Mapped[int] = mapped_column(Integer)
    occurred_at: Mapped[str] = mapped_column(String(40), index=True)


class MemorySettingsRow(MemoryBase):
    """One application-local switch; disabling never deletes entries or audit."""

    __tablename__ = "memory_settings"
    settings_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean)


_MULTI_VALUE_KEYS = {"common_directory_ref", "common_application_ref"}


def _utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _logical_digest(candidate: MemoryCandidate) -> str:
    suffix = candidate.value if candidate.key.value in _MULTI_VALUE_KEYS else ""
    raw = f"{candidate.scope.value}\0{candidate.key.value}\0{suffix}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _payload_digest(entry: MemoryEntry) -> str:
    return hashlib.sha256(entry.model_dump_json().encode()).hexdigest()


def _to_row(entry: MemoryEntry, logical_digest: str) -> MemoryEntryRow:
    return MemoryEntryRow(
        memory_id=str(entry.memory_id),
        logical_digest=logical_digest,
        category=entry.category.value,
        scope=entry.scope.value,
        key=entry.key.value,
        value=entry.value,
        source_type=entry.source_type.value,
        source_ref=entry.source_ref,
        confidence=entry.confidence.value,
        sensitivity=entry.sensitivity.value,
        version=entry.version,
        created_at=_utc_iso(entry.created_at),
        updated_at=_utc_iso(entry.updated_at),
        expires_at=_utc_iso(entry.expires_at) if entry.expires_at else None,
        payload_digest=_payload_digest(entry),
    )


def _decode(row: MemoryEntryRow) -> MemoryEntry:
    entry = MemoryEntry(
        memory_id=UUID(row.memory_id),
        category=row.category,
        scope=row.scope,
        key=row.key,
        value=row.value,
        source_type=MemorySourceType(row.source_type),
        source_ref=row.source_ref,
        confidence=MemoryConfidence(row.confidence),
        sensitivity=MemorySensitivity(row.sensitivity),
        version=row.version,
        created_at=datetime.fromisoformat(row.created_at),
        updated_at=datetime.fromisoformat(row.updated_at),
        expires_at=datetime.fromisoformat(row.expires_at) if row.expires_at else None,
    )
    if _payload_digest(entry) != row.payload_digest:
        raise MemoryStoreError("Memory entry digest mismatch")
    return entry


def _purge_expired(session: Session, now: datetime) -> int:
    """Physically remove expired values and retain only value-free events."""
    rows = tuple(
        session.scalars(
            select(MemoryEntryRow).where(
                MemoryEntryRow.expires_at.is_not(None),
                MemoryEntryRow.expires_at <= _utc_iso(now),
            )
        )
    )
    for row in rows:
        session.delete(row)
        session.add(
            _event_row(
                MemoryEvent(
                    memory_id=UUID(row.memory_id),
                    action=MemoryEventAction.EXPIRED,
                    scope=row.scope,
                    key_digest=hashlib.sha256(row.key.encode()).hexdigest(),
                    reason_code="MEMORY_TTL_EXPIRED",
                    affected_count=1,
                    occurred_at=now,
                )
            )
        )
    return len(rows)


class MemoryRepository:
    """Persist finite preferences while keeping deletion events content-free."""

    def __init__(self, database_path: Path) -> None:
        self._engine = create_sqlite_engine(database_path)
        self._sessions = sessionmaker(self._engine, expire_on_commit=False)

    def initialize(self) -> None:
        """Create tables, verify SQLite, and default persistent Memory to enabled."""
        try:
            with self._engine.connect() as connection:
                if connection.execute(text("PRAGMA quick_check")).scalar_one() != "ok":
                    raise MemoryStoreError("Memory database integrity check failed")
            MemoryBase.metadata.create_all(self._engine)
            with self._sessions.begin() as session:
                if session.get(MemorySettingsRow, 1) is None:
                    session.add(MemorySettingsRow(settings_id=1, enabled=True))
        except SQLAlchemyError as exc:
            raise MemoryStoreError("Memory database initialization failed") from exc

    def is_enabled(self) -> bool:
        """Return the stored Memory switch, failing closed if it is missing."""
        try:
            with self._sessions() as session:
                row = session.get(MemorySettingsRow, 1)
                if row is None:
                    raise MemoryStoreError("Memory settings are missing")
                return row.enabled
        except SQLAlchemyError as exc:
            raise MemoryStoreError("Memory settings could not be read") from exc

    def set_enabled(self, enabled: bool, *, now: datetime) -> None:
        """Update only the Memory switch and append a value-free event."""
        action = MemoryEventAction.ENABLED if enabled else MemoryEventAction.DISABLED
        try:
            with self._sessions.begin() as session:
                row = session.get(MemorySettingsRow, 1)
                if row is None:
                    raise MemoryStoreError("Memory settings are missing")
                row.enabled = enabled
                session.add(
                    _event_row(
                        MemoryEvent(action=action, reason_code=action.value, occurred_at=now)
                    )
                )
        except SQLAlchemyError as exc:
            raise MemoryStoreError("Memory settings could not be updated") from exc

    def upsert(self, candidate: MemoryCandidate, *, now: datetime) -> MemoryEntry:
        """Create or version one validated logical key without silent duplicate rows."""
        logical = _logical_digest(candidate)
        try:
            with self._sessions.begin() as session:
                _purge_expired(session, now)
                row = session.scalar(
                    select(MemoryEntryRow).where(MemoryEntryRow.logical_digest == logical)
                )
                existing = _decode(row) if row is not None else None
                expires_at = (
                    now + timedelta(seconds=candidate.proposed_ttl_seconds)
                    if candidate.proposed_ttl_seconds is not None
                    else None
                )
                entry = MemoryEntry(
                    memory_id=existing.memory_id if existing else candidate.candidate_id,
                    category=candidate.category,
                    scope=candidate.scope,
                    key=candidate.key,
                    value=candidate.value.strip(),
                    source_type=candidate.source_type,
                    source_ref=candidate.source_ref,
                    confidence=candidate.confidence,
                    sensitivity=candidate.sensitivity,
                    version=existing.version + 1 if existing else 1,
                    created_at=existing.created_at if existing else now,
                    updated_at=now,
                    expires_at=expires_at,
                )
                replacement = _to_row(entry, logical)
                if row is None:
                    session.add(replacement)
                else:
                    for name in (
                        "category",
                        "scope",
                        "key",
                        "value",
                        "source_type",
                        "source_ref",
                        "confidence",
                        "sensitivity",
                        "version",
                        "updated_at",
                        "expires_at",
                        "payload_digest",
                    ):
                        setattr(row, name, getattr(replacement, name))
                action = MemoryEventAction.UPDATED if existing else MemoryEventAction.CREATED
                session.add(
                    _event_row(
                        MemoryEvent(
                            memory_id=entry.memory_id,
                            action=action,
                            scope=entry.scope,
                            key_digest=hashlib.sha256(entry.key.value.encode()).hexdigest(),
                            reason_code="EXPLICIT_USER_PREFERENCE",
                            affected_count=1,
                            occurred_at=now,
                        )
                    )
                )
                return entry
        except SQLAlchemyError as exc:
            raise MemoryStoreError("Memory entry could not be saved") from exc

    def query(self, query: MemoryQuery, *, now: datetime) -> tuple[MemoryEntry, ...]:
        """Return live entries from exact scopes with a strict row limit."""
        if not self.is_enabled():
            return ()
        try:
            with self._sessions.begin() as session:
                _purge_expired(session, now)
                statement = select(MemoryEntryRow).where(
                    MemoryEntryRow.scope.in_(scope.value for scope in query.scopes)
                )
                if query.categories:
                    statement = statement.where(
                        MemoryEntryRow.category.in_(category.value for category in query.categories)
                    )
                rows = session.scalars(
                    statement.order_by(MemoryEntryRow.updated_at.desc()).limit(query.limit)
                )
                return tuple(
                    entry
                    for entry in (_decode(row) for row in rows)
                    if entry.expires_at is None or entry.expires_at > now
                )
        except SQLAlchemyError as exc:
            raise MemoryStoreError("Memory query failed") from exc

    def list_all(self, *, now: datetime, limit: int = 500) -> tuple[MemoryEntry, ...]:
        """Return live entries for the user-facing management page only."""
        bounded = max(1, min(limit, 500))
        try:
            with self._sessions.begin() as session:
                _purge_expired(session, now)
                rows = session.scalars(
                    select(MemoryEntryRow).order_by(MemoryEntryRow.updated_at.desc()).limit(bounded)
                )
                return tuple(
                    entry
                    for entry in (_decode(row) for row in rows)
                    if entry.expires_at is None or entry.expires_at > now
                )
        except SQLAlchemyError as exc:
            raise MemoryStoreError("Memory listing failed") from exc

    def delete(self, memory_id: UUID, *, now: datetime) -> bool:
        """Physically delete one value and retain only a value-free deletion event."""
        try:
            with self._sessions.begin() as session:
                row = session.get(MemoryEntryRow, str(memory_id))
                if row is None:
                    return False
                scope = row.scope
                key_digest = hashlib.sha256(row.key.encode()).hexdigest()
                session.delete(row)
                session.add(
                    _event_row(
                        MemoryEvent(
                            memory_id=memory_id,
                            action=MemoryEventAction.DELETED,
                            scope=scope,
                            key_digest=key_digest,
                            reason_code="USER_REQUESTED_DELETE",
                            affected_count=1,
                            occurred_at=now,
                        )
                    )
                )
                return True
        except SQLAlchemyError as exc:
            raise MemoryStoreError("Memory entry could not be deleted") from exc

    def clear(self, scope: str | None, *, now: datetime) -> int:
        """Physically delete a scope or all values without retaining deleted content."""
        try:
            with self._sessions.begin() as session:
                id_statement = select(MemoryEntryRow.memory_id)
                statement = delete(MemoryEntryRow)
                if scope is not None:
                    id_statement = id_statement.where(MemoryEntryRow.scope == scope)
                    statement = statement.where(MemoryEntryRow.scope == scope)
                count = len(tuple(session.scalars(id_statement)))
                session.execute(statement)
                action = (
                    MemoryEventAction.CLEARED_SCOPE
                    if scope is not None
                    else MemoryEventAction.CLEARED_ALL
                )
                session.add(
                    _event_row(
                        MemoryEvent(
                            action=action,
                            scope=scope,
                            reason_code="USER_REQUESTED_CLEAR",
                            affected_count=count,
                            occurred_at=now,
                        )
                    )
                )
                return count
        except SQLAlchemyError as exc:
            raise MemoryStoreError("Memory entries could not be cleared") from exc

    def close(self) -> None:
        """Release database connections without deleting preferences."""
        self._engine.dispose()


def _event_row(event: MemoryEvent) -> MemoryEventRow:
    return MemoryEventRow(
        event_id=str(event.event_id),
        memory_id=str(event.memory_id) if event.memory_id else None,
        action=event.action.value,
        scope=event.scope.value if event.scope else None,
        key_digest=event.key_digest,
        reason_code=event.reason_code,
        affected_count=event.affected_count,
        occurred_at=_utc_iso(event.occurred_at),
    )
