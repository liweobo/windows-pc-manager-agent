"""Additive Office journal with atomic one-time confirmations and restart invalidation."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from uuid import UUID, uuid4

from sqlalchemy import Column, MetaData, String, Table, Text, func, insert, select, update
from sqlalchemy.exc import SQLAlchemyError

from pc_manager_agent.domain.office_documents import OfficeError
from pc_manager_agent.domain.office_transactions import (
    OfficeDocumentBackup,
    OfficeTransaction,
    OfficeTransactionState,
)
from pc_manager_agent.persistence.database import create_sqlite_engine

office_metadata = MetaData()
office_records = Table(
    "office_records",
    office_metadata,
    Column("id", String(36), primary_key=True),
    Column("kind", String(30), nullable=False),
    Column("body", Text, nullable=False),
    Column("digest", String(64), nullable=False),
)
office_consents = Table(
    "office_consents",
    office_metadata,
    Column("id", String(36), primary_key=True),
    Column("binding", String(64), nullable=False),
    Column("purpose", String(30), nullable=False),
    Column("instance", String(36), nullable=False),
    Column("state", String(30), nullable=False),
    Column("expires", String(50), nullable=False),
)


class OfficeRepository:
    """Persist metadata, checksums and consumed authority without document bodies."""

    def __init__(self, path: Path) -> None:
        self._engine = create_sqlite_engine(path)
        self._records = office_records
        self._consents = office_consents
        self._instance = str(uuid4())
        self._lock = RLock()
        office_metadata.create_all(self._engine)
        self._interrupt_previous()

    def put_backup(self, backup: OfficeDocumentBackup) -> None:
        """Persist immutable backup metadata only after binary readback verification."""
        self._insert(backup.backup_id, "backup", backup.model_dump_json())

    def backup(self, backup_id: UUID) -> OfficeDocumentBackup:
        """Load and checksum-validate one exact binary-backup record."""
        return OfficeDocumentBackup.model_validate_json(self._get(backup_id, "backup"))

    def put_transaction(self, transaction: OfficeTransaction) -> None:
        """Write the exact metadata Preview before allowing confirmation."""
        self._insert(transaction.transaction_id, "transaction", transaction.model_dump_json())

    def transaction(self, transaction_id: UUID) -> OfficeTransaction:
        """Read a checksummed transaction without treating terminal state as verification."""
        return OfficeTransaction.model_validate_json(self._get(transaction_id, "transaction"))

    def recent(self, limit: int = 50) -> tuple[OfficeTransaction, ...]:
        """Return bounded journal history without content or automatically resumed authority."""
        with self._engine.connect() as connection:
            ids = tuple(
                connection.scalars(
                    select(self._records.c.id)
                    .where(self._records.c.kind == "transaction")
                    .order_by(func.json_extract(self._records.c.body, "$.created_at").desc())
                    .limit(min(200, max(1, limit)))
                )
            )
        return tuple(
            sorted(
                (self.transaction(UUID(item)) for item in ids),
                key=lambda item: item.created_at,
                reverse=True,
            )
        )

    def change(self, previous: OfficeTransaction, updated: OfficeTransaction) -> None:
        """CAS update; concurrent callers cannot overwrite a newer transaction revision."""
        if previous.transaction_id != updated.transaction_id:
            raise OfficeError("TRANSACTION_ID_CHANGED")
        old = previous.model_dump_json()
        new = updated.model_dump_json()
        try:
            with self._lock, self._engine.begin() as connection:
                result = connection.execute(
                    update(self._records)
                    .where(
                        self._records.c.id == str(previous.transaction_id),
                        self._records.c.digest == hashlib.sha256(old.encode()).hexdigest(),
                    )
                    .values(body=new, digest=hashlib.sha256(new.encode()).hexdigest())
                )
                if result.rowcount != 1:
                    raise OfficeError("TRANSACTION_CONCURRENT_CHANGE")
        except SQLAlchemyError as exc:
            raise OfficeError("OFFICE_STORAGE_UNAVAILABLE") from exc

    def request(self, binding: str, purpose: str, expires: datetime) -> UUID:
        """Create one pending purpose-bound approval in the current process instance."""
        if expires.tzinfo is None:
            raise OfficeError("UTC_CLOCK_REQUIRED")
        confirmation_id = uuid4()
        with self._lock, self._engine.begin() as connection:
            connection.execute(
                insert(self._consents).values(
                    id=str(confirmation_id),
                    binding=binding,
                    purpose=purpose,
                    instance=self._instance,
                    state="PENDING",
                    expires=expires.astimezone(UTC).isoformat(),
                )
            )
        return confirmation_id

    def resolve(
        self, confirmation_id: UUID, binding: str, purpose: str, approved: bool, now: datetime
    ) -> None:
        """Resolve a pending exact approval; expired or changed requests fail closed."""
        self._transition_consent(
            confirmation_id,
            binding,
            purpose,
            "PENDING",
            "APPROVED" if approved else "REJECTED",
            now,
        )

    def consume(self, confirmation_id: UUID, binding: str, purpose: str, now: datetime) -> None:
        """Atomically consume once; concurrent execution and replay fail closed."""
        self._transition_consent(confirmation_id, binding, purpose, "APPROVED", "CONSUMED", now)

    def begin_write(
        self,
        transaction: OfficeTransaction,
        approvals: tuple[tuple[UUID, str, str], ...],
        now: datetime,
    ) -> OfficeTransaction:
        """Consume every user approval and journal the write atomically, or do neither."""
        if now.tzinfo is None or not approvals:
            raise OfficeError("OFFICE_AUTHORITY_REQUIRED")
        if transaction.state is not OfficeTransactionState.PREVIEWED:
            raise OfficeError("TRANSACTION_NOT_PREVIEWED")
        updated = transaction.model_copy(
            update={
                "state": OfficeTransactionState.CONFIRMED,
                "confirmation_ids": tuple(item[0] for item in approvals),
            }
        )
        old_body, new_body = transaction.model_dump_json(), updated.model_dump_json()
        try:
            with self._lock, self._engine.begin() as connection:
                for identifier, binding, purpose in approvals:
                    result = connection.execute(
                        update(self._consents)
                        .where(
                            self._consents.c.id == str(identifier),
                            self._consents.c.binding == binding,
                            self._consents.c.purpose == purpose,
                            self._consents.c.instance == self._instance,
                            self._consents.c.state == "APPROVED",
                            self._consents.c.expires > now.astimezone(UTC).isoformat(),
                        )
                        .values(state="CONSUMED")
                    )
                    if result.rowcount != 1:
                        raise OfficeError("CONFIRMATION_MISSING_CHANGED_EXPIRED_OR_USED")
                result = connection.execute(
                    update(self._records)
                    .where(
                        self._records.c.id == str(transaction.transaction_id),
                        self._records.c.kind == "transaction",
                        self._records.c.digest == hashlib.sha256(old_body.encode()).hexdigest(),
                    )
                    .values(body=new_body, digest=hashlib.sha256(new_body.encode()).hexdigest())
                )
                if result.rowcount != 1:
                    raise OfficeError("TRANSACTION_CONCURRENT_CHANGE")
        except SQLAlchemyError as exc:
            raise OfficeError("OFFICE_STORAGE_UNAVAILABLE") from exc
        return updated

    def _transition_consent(
        self,
        confirmation_id: UUID,
        binding: str,
        purpose: str,
        before: str,
        after: str,
        now: datetime,
    ) -> None:
        if now.tzinfo is None:
            raise OfficeError("UTC_CLOCK_REQUIRED")
        try:
            with self._lock, self._engine.begin() as connection:
                result = connection.execute(
                    update(self._consents)
                    .where(
                        self._consents.c.id == str(confirmation_id),
                        self._consents.c.binding == binding,
                        self._consents.c.purpose == purpose,
                        self._consents.c.instance == self._instance,
                        self._consents.c.state == before,
                        self._consents.c.expires > now.astimezone(UTC).isoformat(),
                    )
                    .values(state=after)
                )
                if result.rowcount != 1:
                    raise OfficeError("CONFIRMATION_MISSING_CHANGED_EXPIRED_OR_USED")
        except SQLAlchemyError as exc:
            raise OfficeError("OFFICE_STORAGE_UNAVAILABLE") from exc

    def _insert(self, record_id: UUID, kind: str, body: str) -> None:
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    insert(self._records).values(
                        id=str(record_id),
                        kind=kind,
                        body=body,
                        digest=hashlib.sha256(body.encode()).hexdigest(),
                    )
                )
        except SQLAlchemyError as exc:
            raise OfficeError("OFFICE_STORAGE_UNAVAILABLE") from exc

    def _get(self, record_id: UUID, kind: str) -> str:
        try:
            with self._engine.connect() as connection:
                row = (
                    connection.execute(
                        select(self._records).where(
                            self._records.c.id == str(record_id),
                            self._records.c.kind == kind,
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
        except SQLAlchemyError as exc:
            raise OfficeError("OFFICE_STORAGE_UNAVAILABLE") from exc
        if row is None:
            raise OfficeError("OFFICE_RECORD_MISSING")
        body = str(row["body"])
        if hashlib.sha256(body.encode()).hexdigest() != row["digest"]:
            raise OfficeError("OFFICE_RECORD_CORRUPT")
        return body

    def _interrupt_previous(self) -> None:
        """Invalidate all old consents and mark unfinished writes INTERRUPTED, never resume."""
        with self._engine.begin() as connection:
            connection.execute(
                update(self._consents)
                .where(self._consents.c.state.in_(("PENDING", "APPROVED")))
                .values(state="INTERRUPTED")
            )
        # Restart invalidation is not a history UI query: visit EVERY record,
        # including unfinished work beyond the first 200 display entries.
        with self._engine.connect() as connection:
            identifiers = tuple(
                connection.scalars(
                    select(self._records.c.id).where(self._records.c.kind == "transaction")
                )
            )
        for identifier in identifiers:
            transaction = self.transaction(UUID(identifier))
            if transaction.state in {
                OfficeTransactionState.PREVIEWED,
                OfficeTransactionState.CONFIRMED,
                OfficeTransactionState.WRITING_TEMP,
                OfficeTransactionState.VERIFYING_TEMP,
                OfficeTransactionState.COMMITTING,
            }:
                self.change(
                    transaction,
                    transaction.model_copy(
                        update={
                            "state": OfficeTransactionState.INTERRUPTED,
                        }
                    ),
                )

    def close(self) -> None:
        """Release SQLite handles without deleting history or recovery material."""
        self._engine.dispose()
