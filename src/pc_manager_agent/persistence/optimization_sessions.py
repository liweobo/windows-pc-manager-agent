"""Optimistic-concurrency journal of non-authoritative optimization review sessions."""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import Integer, String, Text, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from pc_manager_agent.domain.optimization_actions import (
    OptimizationSession,
    OptimizationSessionStatus,
    RecommendationActionState,
)
from pc_manager_agent.persistence.database import create_sqlite_engine


class OptimizationSessionStoreError(RuntimeError):
    """Raised when the local journal cannot safely preserve workflow truth."""


class OptimizationSessionBase(DeclarativeBase):
    """Independent additive tables; existing business authorization is untouched."""


class OptimizationSessionRow(OptimizationSessionBase):
    """Only IDs, enums, aggregate observations and checked domain outcome references."""

    __tablename__ = "optimization_review_sessions"
    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), index=True)
    payload: Mapped[str] = mapped_column(Text)
    digest: Mapped[str] = mapped_column(String(64))


def _encode(session: OptimizationSession) -> tuple[str, str]:
    validated = OptimizationSession.model_validate_json(session.model_dump_json())
    payload = validated.model_dump_json()
    return payload, hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _decode(row: OptimizationSessionRow) -> OptimizationSession:
    if hashlib.sha256(row.payload.encode("utf-8")).hexdigest() != row.digest:
        raise OptimizationSessionStoreError("Optimization journal digest mismatch")
    try:
        session = OptimizationSession.model_validate_json(row.payload)
    except ValidationError as exc:
        raise OptimizationSessionStoreError("Optimization journal schema mismatch") from exc
    if (str(session.session_id), session.revision, session.status.value) != (
        row.session_id,
        row.revision,
        row.status,
    ):
        raise OptimizationSessionStoreError("Optimization journal columns disagree")
    return session


class OptimizationSessionRepository:
    """Keep additive session summaries; never store, consume or replay domain authority."""

    def __init__(self, database_path: Path) -> None:
        self._engine = create_sqlite_engine(database_path)
        self._sessions = sessionmaker(self._engine, expire_on_commit=False)

    def initialize(self) -> tuple[UUID, ...]:
        """Check storage and mark unfinished review sessions stale without resuming work."""
        interrupted: list[UUID] = []
        try:
            with self._engine.connect() as connection:
                if connection.execute(text("PRAGMA quick_check")).scalar_one() != "ok":
                    raise OptimizationSessionStoreError("Optimization journal integrity failed")
            OptimizationSessionBase.metadata.create_all(self._engine)
            with self._sessions.begin() as db:
                for row in db.scalars(
                    select(OptimizationSessionRow).where(
                        OptimizationSessionRow.status.in_(
                            ("CREATED", "IN_PROGRESS", "PARTIALLY_APPLIED")
                        )
                    )
                ):
                    previous = _decode(row)
                    items = tuple(
                        item.model_copy(update={"state": RecommendationActionState.STALE})
                        if item.state
                        in {
                            RecommendationActionState.PENDING,
                            RecommendationActionState.REVIEWED,
                            RecommendationActionState.ROUTED,
                        }
                        else item
                        for item in previous.items
                    )
                    changed = previous.model_copy(
                        update={
                            "revision": previous.revision + 1,
                            "status": OptimizationSessionStatus.STALE,
                            "items": items,
                        }
                    )
                    row.payload, row.digest = _encode(changed)
                    row.revision, row.status = changed.revision, changed.status.value
                    interrupted.append(changed.session_id)
        except SQLAlchemyError as exc:
            raise OptimizationSessionStoreError("Optimization journal unavailable") from exc
        return tuple(interrupted)

    def create(self, session: OptimizationSession) -> None:
        """Create a unique review container; duplicates never overwrite history."""
        if session.revision != 1:
            raise OptimizationSessionStoreError("New sessions require revision one")
        payload, digest = _encode(session)
        try:
            with self._sessions.begin() as db:
                db.add(
                    OptimizationSessionRow(
                        session_id=str(session.session_id),
                        revision=1,
                        status=session.status.value,
                        payload=payload,
                        digest=digest,
                    )
                )
        except SQLAlchemyError as exc:
            raise OptimizationSessionStoreError(
                "Optimization session could not be created"
            ) from exc

    def get(self, session_id: UUID) -> OptimizationSession:
        """Load and validate one summary rather than accepting caller-supplied state."""
        try:
            with self._sessions() as db:
                row = db.get(OptimizationSessionRow, str(session_id))
                if row is None:
                    raise OptimizationSessionStoreError("Unknown optimization session")
                return _decode(row)
        except SQLAlchemyError as exc:
            raise OptimizationSessionStoreError("Optimization session could not be read") from exc

    def save(self, session: OptimizationSession, *, expected_revision: int) -> None:
        """Compare-and-swap prevents duplicate preparation and overwritten cancellation."""
        if session.revision != expected_revision + 1:
            raise OptimizationSessionStoreError("Optimization session revision is not consecutive")
        payload, digest = _encode(session)
        try:
            with self._sessions.begin() as db:
                changed = db.execute(
                    update(OptimizationSessionRow)
                    .where(
                        OptimizationSessionRow.session_id == str(session.session_id),
                        OptimizationSessionRow.revision == expected_revision,
                    )
                    .values(
                        revision=session.revision,
                        status=session.status.value,
                        payload=payload,
                        digest=digest,
                    )
                    .returning(OptimizationSessionRow.session_id)
                )
                if changed.scalar_one_or_none() != str(session.session_id):
                    raise OptimizationSessionStoreError("Optimization session changed concurrently")
        except SQLAlchemyError as exc:
            raise OptimizationSessionStoreError(
                "Optimization session could not be updated"
            ) from exc

    def close(self) -> None:
        """Release journal connections without deleting history."""
        self._engine.dispose()
