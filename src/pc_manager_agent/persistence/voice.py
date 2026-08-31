"""Additive metadata-only SQLite voice journal with compare-and-swap and single-use disclosure."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import Column, Float, Integer, MetaData, String, Table, insert, select, update
from sqlalchemy.exc import SQLAlchemyError

from pc_manager_agent.domain.voice import FrozenVoiceModel, VoiceError, VoiceState
from pc_manager_agent.persistence.database import create_sqlite_engine


class VoiceSessionRecord(FrozenVoiceModel):
    """Only metadata survives a session; bodies are deliberately absent from this schema."""

    reference: UUID
    instance: UUID
    state: VoiceState
    revision: int
    started_at: float
    updated_at: float
    request_ref: str | None = None
    content_digest: str | None = None


class VoiceTranscriptConsumptionStore:
    """Atomic at-most-once request allocation. A crash may lose an input, never replay it."""

    def __init__(self, path: Path, instance: UUID) -> None:
        self.instance = instance
        self._engine = create_sqlite_engine(path)
        metadata = MetaData()
        self._sessions = Table(
            "voice_sessions",
            metadata,
            Column("reference", String(36), primary_key=True),
            Column("instance", String(36), nullable=False),
            Column("state", String(40), nullable=False),
            Column("revision", Integer, nullable=False),
            Column("started_at", Float, nullable=False),
            Column("updated_at", Float, nullable=False),
            Column("request_ref", String(36), unique=True),
            Column("content_digest", String(64)),
        )
        self._consents = Table(
            "voice_disclosures",
            metadata,
            Column("reference", String(36), primary_key=True),
            Column("instance", String(36), nullable=False),
            Column("owner_ref", String(36), nullable=False),
            Column("purpose", String(10), nullable=False),
            Column("digest", String(64), nullable=False),
            Column("expires_at", Float, nullable=False),
            Column("state", String(20), nullable=False),
        )
        try:
            metadata.create_all(self._engine)
            with self._engine.begin() as connection:
                # Never revive pending input or outbound authority after startup.
                connection.execute(
                    update(self._sessions)
                    .where(
                        self._sessions.c.state.not_in(
                            ["COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"]
                        )
                    )
                    .values(state="INTERRUPTED", updated_at=datetime.now(UTC).timestamp())
                )
                connection.execute(
                    update(self._consents)
                    .where(self._consents.c.state == "PENDING")
                    .values(state="INVALIDATED")
                )
        except SQLAlchemyError as exc:
            self._engine.dispose()
            raise VoiceError("VOICE_STORAGE_UNAVAILABLE") from exc

    def create(self, reference: UUID, now: float) -> VoiceSessionRecord:
        """Persist the start before any microphone is activated."""
        record = VoiceSessionRecord(
            reference=reference,
            instance=self.instance,
            state=VoiceState.REQUESTING_PERMISSION,
            revision=0,
            started_at=now,
            updated_at=now,
        )
        values = record.model_dump(mode="json")
        try:
            with self._engine.begin() as connection:
                connection.execute(insert(self._sessions).values(**values))
        except SQLAlchemyError as exc:
            raise VoiceError("VOICE_STORAGE_UNAVAILABLE") from exc
        return record

    def transition(
        self,
        previous: VoiceSessionRecord,
        state: VoiceState,
        now: float,
        *,
        digest: str | None = None,
        request_ref: UUID | None = None,
    ) -> VoiceSessionRecord:
        """CAS a session revision; a second final callback or submit cannot consume it again."""
        updated = previous.model_copy(
            update={
                "state": state,
                "revision": previous.revision + 1,
                "updated_at": now,
                "content_digest": digest if digest is not None else previous.content_digest,
                "request_ref": str(request_ref) if request_ref else previous.request_ref,
            }
        )
        try:
            with self._engine.begin() as connection:
                statement = update(self._sessions).where(
                    self._sessions.c.reference == str(previous.reference),
                    self._sessions.c.instance == str(self.instance),
                    self._sessions.c.revision == previous.revision,
                    self._sessions.c.state == previous.state.value,
                )
                if request_ref is not None:
                    statement = statement.where(self._sessions.c.request_ref.is_(None))
                result = connection.execute(statement.values(**updated.model_dump(mode="json")))
                if result.rowcount != 1:
                    raise VoiceError("VOICE_SESSION_STALE_OR_CONSUMED")
        except SQLAlchemyError as exc:
            raise VoiceError("VOICE_STORAGE_UNAVAILABLE") from exc
        return updated

    def offer(self, reference: UUID, owner: UUID, purpose: str, digest: str, expiry: float) -> None:
        """Persist a digest-only proposal; no PCM, text, destination or secret is stored."""
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    insert(self._consents).values(
                        reference=str(reference),
                        instance=str(self.instance),
                        owner_ref=str(owner),
                        purpose=purpose,
                        digest=digest,
                        expires_at=expiry,
                        state="PENDING",
                    )
                )
        except SQLAlchemyError as exc:
            raise VoiceError("VOICE_STORAGE_UNAVAILABLE") from exc

    def consume(
        self,
        reference: UUID,
        owner: UUID,
        purpose: str,
        digest: str,
        now: float,
        *,
        approved: bool,
    ) -> None:
        """Atomically approve-and-consume one exact unexpired disclosure, or reject it."""
        try:
            with self._engine.begin() as connection:
                result = connection.execute(
                    update(self._consents)
                    .where(
                        self._consents.c.reference == str(reference),
                        self._consents.c.owner_ref == str(owner),
                        self._consents.c.instance == str(self.instance),
                        self._consents.c.purpose == purpose,
                        self._consents.c.digest == digest,
                        self._consents.c.expires_at > now,
                        self._consents.c.state == "PENDING",
                    )
                    .values(state="CONSUMED" if approved else "REJECTED")
                )
                if result.rowcount != 1:
                    raise VoiceError("VOICE_DISCLOSURE_STALE_OR_CONSUMED")
        except SQLAlchemyError as exc:
            raise VoiceError("VOICE_STORAGE_UNAVAILABLE") from exc
        if not approved:
            raise VoiceError("VOICE_DISCLOSURE_REJECTED")

    def recent(self, limit: int = 50) -> tuple[VoiceSessionRecord, ...]:
        """Return bounded input history without transcript or audio bodies."""
        try:
            with self._engine.connect() as connection:
                rows = connection.execute(
                    select(self._sessions)
                    .order_by(self._sessions.c.started_at.desc())
                    .limit(max(1, min(limit, 100)))
                ).mappings()
                return tuple(VoiceSessionRecord.model_validate(dict(row)) for row in rows)
        except SQLAlchemyError as exc:
            raise VoiceError("VOICE_STORAGE_UNAVAILABLE") from exc

    def close(self) -> None:
        """Release SQLite connections after voice workers finish."""
        self._engine.dispose()
