"""Content-free task journal with fail-closed restart interruption."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import Integer, String, Text, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from pc_manager_agent.domain.task_outcomes import RootTaskStatus, TaskJournalEntry
from pc_manager_agent.persistence.database import create_sqlite_engine


class TaskJournalError(RuntimeError):
    """Raised when task status cannot be stored or verified."""


class TaskJournalBase(DeclarativeBase):
    """Independent Stage 5D task-summary table."""


class TaskJournalRow(TaskJournalBase):
    """Digest-protected task metadata with no prompt, context, or Memory values."""

    __tablename__ = "agent_task_journal"
    task_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[str] = mapped_column(String(40), index=True)
    payload: Mapped[str] = mapped_column(Text)
    payload_digest: Mapped[str] = mapped_column(String(64))


def _encode(entry: TaskJournalEntry) -> tuple[str, str]:
    payload = entry.model_dump_json()
    return payload, hashlib.sha256(payload.encode()).hexdigest()


def _decode(row: TaskJournalRow) -> TaskJournalEntry:
    if hashlib.sha256(row.payload.encode()).hexdigest() != row.payload_digest:
        raise TaskJournalError("Task journal digest mismatch")
    try:
        entry = TaskJournalEntry.model_validate_json(row.payload)
    except ValidationError as exc:
        raise TaskJournalError("Task journal schema mismatch") from exc
    if (str(entry.task_id), entry.status.value, entry.revision) != (
        row.task_id,
        row.status,
        row.revision,
    ):
        raise TaskJournalError("Task journal columns disagree")
    return entry


class TaskJournalRepository:
    """Persist only task progress metadata and never replay active work."""

    def __init__(self, database_path: Path) -> None:
        self._engine = create_sqlite_engine(database_path)
        self._sessions = sessionmaker(self._engine, expire_on_commit=False)

    def initialize(self) -> tuple[UUID, ...]:
        """Verify storage and mark every active task interrupted on restart."""
        interrupted: list[UUID] = []
        active = {
            RootTaskStatus.CREATED.value,
            RootTaskStatus.RUNNING.value,
            RootTaskStatus.WAITING_CONFIRMATION.value,
        }
        try:
            with self._engine.connect() as connection:
                if connection.execute(text("PRAGMA quick_check")).scalar_one() != "ok":
                    raise TaskJournalError("Task journal integrity check failed")
            TaskJournalBase.metadata.create_all(self._engine)
            with self._sessions.begin() as session:
                rows = session.scalars(
                    select(TaskJournalRow).where(TaskJournalRow.status.in_(active))
                )
                for row in rows:
                    previous = _decode(row)
                    changed = previous.model_copy(
                        update={
                            "status": RootTaskStatus.INTERRUPTED,
                            "revision": previous.revision + 1,
                            "updated_at": datetime.now(UTC),
                        }
                    )
                    row.payload, row.payload_digest = _encode(changed)
                    row.status = changed.status.value
                    row.revision = changed.revision
                    row.updated_at = changed.updated_at.isoformat()
                    interrupted.append(changed.task_id)
        except SQLAlchemyError as exc:
            raise TaskJournalError("Task journal initialization failed") from exc
        return tuple(interrupted)

    def create(self, entry: TaskJournalEntry) -> None:
        """Create one unique root task without overwriting history."""
        if entry.revision != 1:
            raise TaskJournalError("New task journal entries require revision one")
        payload, digest = _encode(entry)
        try:
            with self._sessions.begin() as session:
                session.add(
                    TaskJournalRow(
                        task_id=str(entry.task_id),
                        status=entry.status.value,
                        revision=entry.revision,
                        updated_at=entry.updated_at.isoformat(),
                        payload=payload,
                        payload_digest=digest,
                    )
                )
        except SQLAlchemyError as exc:
            raise TaskJournalError("Task journal entry could not be created") from exc

    def get(self, task_id: UUID) -> TaskJournalEntry:
        """Load and verify one task summary."""
        try:
            with self._sessions() as session:
                row = session.get(TaskJournalRow, str(task_id))
                if row is None:
                    raise TaskJournalError("Unknown task")
                return _decode(row)
        except SQLAlchemyError as exc:
            raise TaskJournalError("Task journal entry could not be read") from exc

    def save(self, entry: TaskJournalEntry, *, expected_revision: int) -> None:
        """Compare-and-swap one state transition to prevent lost cancellation."""
        if entry.revision != expected_revision + 1:
            raise TaskJournalError("Task journal revision is not consecutive")
        payload, digest = _encode(entry)
        try:
            with self._sessions.begin() as session:
                changed = session.execute(
                    update(TaskJournalRow)
                    .where(
                        TaskJournalRow.task_id == str(entry.task_id),
                        TaskJournalRow.revision == expected_revision,
                    )
                    .values(
                        status=entry.status.value,
                        revision=entry.revision,
                        updated_at=entry.updated_at.isoformat(),
                        payload=payload,
                        payload_digest=digest,
                    )
                    .returning(TaskJournalRow.task_id)
                )
                if changed.scalar_one_or_none() != str(entry.task_id):
                    raise TaskJournalError("Task journal changed concurrently")
        except SQLAlchemyError as exc:
            raise TaskJournalError("Task journal entry could not be updated") from exc

    def list_recent(self, limit: int = 100) -> tuple[TaskJournalEntry, ...]:
        """Return recent content-free task summaries with a strict limit."""
        bounded = max(1, min(limit, 500))
        try:
            with self._sessions() as session:
                rows = session.scalars(
                    select(TaskJournalRow).order_by(TaskJournalRow.updated_at.desc()).limit(bounded)
                )
                return tuple(_decode(row) for row in rows)
        except SQLAlchemyError as exc:
            raise TaskJournalError("Task journal listing failed") from exc

    def close(self) -> None:
        """Release database connections without deleting task history."""
        self._engine.dispose()
