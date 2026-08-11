"""Paged SQLite result store for bounded-memory read-only analysis sessions."""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    and_,
    delete,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from pc_manager_agent.domain.file_analysis import (
    AnalysisMatchMode,
    AnalysisType,
    CategorySummary,
    InactiveAssessment,
    InactiveConfidence,
    StoredFileRecord,
)
from pc_manager_agent.domain.reports import (
    FileCategory,
    FileMetadata,
    ScanBatch,
    ScanIssue,
    ScanSummary,
)
from pc_manager_agent.persistence.database import create_sqlite_engine


class AnalysisResultStoreError(RuntimeError):
    """Raised when the local result index cannot be trusted."""


class AnalysisBase(DeclarativeBase):
    """Declarative base isolated from audit and authorization tables."""


class AnalysisSessionRow(AnalysisBase):
    """Aggregate state for one analysis session."""

    __tablename__ = "analysis_sessions"

    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    root: Mapped[str] = mapped_column(String(2_048), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    files_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    directories_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    issue_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class FileRecordRow(AnalysisBase):
    """Metadata and analysis annotations for one discovered regular file."""

    __tablename__ = "analysis_files"
    __table_args__ = (UniqueConstraint("session_id", "path", name="uq_analysis_session_path"),)

    record_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_sessions.session_id", ondelete="CASCADE"), index=True
    )
    path: Mapped[str] = mapped_column(String(4_096), nullable=False)
    name: Mapped[str] = mapped_column(String(1_024), nullable=False)
    extension: Mapped[str] = mapped_column(String(128), nullable=False)
    media_type: Mapped[str | None] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    modified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scan_root: Mapped[str] = mapped_column(String(2_048), nullable=False)
    hidden: Mapped[bool] = mapped_column(Boolean, nullable=False)
    read_only: Mapped[bool] = mapped_column(Boolean, nullable=False)
    system: Mapped[bool] = mapped_column(Boolean, nullable=False)
    offline: Mapped[bool] = mapped_column(Boolean, nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    file_id: Mapped[str | None] = mapped_column(String(64))
    device_id: Mapped[str | None] = mapped_column(String(64))
    windows_attributes: Mapped[int | None] = mapped_column(BigInteger)
    is_large: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    inactive_confidence: Mapped[str | None] = mapped_column(String(16), index=True)
    inactive_evidence: Mapped[list[str] | None] = mapped_column(JSON)
    inactive_threshold_days: Mapped[int | None] = mapped_column(Integer)
    duplicate_group_id: Mapped[str | None] = mapped_column(String(128), index=True)
    matches_plan: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)


class AnalysisIssueRow(AnalysisBase):
    """One recoverable or skipped scanner issue."""

    __tablename__ = "analysis_issues"

    issue_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_sessions.session_id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    message: Mapped[str] = mapped_column(String(1_000), nullable=False)
    path: Mapped[str | None] = mapped_column(String(4_096))


class AnalysisResultRepository:
    """Persist bounded batches and expose keyset-paged deterministic queries."""

    def __init__(self, database_path: Path) -> None:
        self._engine = create_sqlite_engine(database_path)
        self._sessions = sessionmaker(self._engine, expire_on_commit=False)
        self._initialized = False

    def initialize(self) -> None:
        """Create result tables and remove incomplete rows left by a prior crash."""
        try:
            AnalysisBase.metadata.create_all(self._engine)
            with self._sessions.begin() as session:
                stale_ids = select(AnalysisSessionRow.session_id).where(
                    AnalysisSessionRow.status == "RUNNING"
                )
                session.execute(
                    delete(AnalysisSessionRow).where(AnalysisSessionRow.session_id.in_(stale_ids))
                )
        except SQLAlchemyError as exc:
            raise AnalysisResultStoreError(
                "Analysis result database initialization failed"
            ) from exc
        self._initialized = True

    def create_session(self, session_id: UUID, roots: Sequence[Path]) -> None:
        """Create one empty RUNNING session before scanner execution."""
        self._require_initialized()
        if not roots:
            raise ValueError("At least one analysis root is required")
        row = AnalysisSessionRow(
            session_id=str(session_id),
            root=json.dumps([str(root) for root in roots], ensure_ascii=False),
            started_at=datetime.now(UTC),
            status="RUNNING",
            files_seen=0,
            directories_seen=0,
            total_size_bytes=0,
            issue_count=0,
            duration_ms=0,
        )
        try:
            with self._sessions.begin() as session:
                session.add(row)
        except SQLAlchemyError as exc:
            raise AnalysisResultStoreError("Analysis session creation failed") from exc

    def store_batch(self, batch: ScanBatch) -> None:
        """Append one scanner batch without retaining it in application memory."""
        self._require_initialized()
        rows = [self._metadata_to_row(batch.session_id, metadata) for metadata in batch.files]
        if not rows:
            return
        try:
            with self._sessions.begin() as session:
                session.add_all(rows)
        except SQLAlchemyError as exc:
            raise AnalysisResultStoreError("Analysis metadata batch write failed") from exc

    def store_issues(self, session_id: UUID, issues: Sequence[ScanIssue]) -> None:
        """Append bounded scanner issues to the local analysis session."""
        self._require_initialized()
        rows = [
            AnalysisIssueRow(
                session_id=str(session_id),
                code=issue.code,
                message=issue.message[:1_000],
                path=str(issue.path) if issue.path is not None else None,
            )
            for issue in issues
        ]
        if not rows:
            return
        try:
            with self._sessions.begin() as session:
                session.add_all(rows)
        except SQLAlchemyError as exc:
            raise AnalysisResultStoreError("Analysis issue batch write failed") from exc

    def complete_scan(self, session_id: UUID, summary: ScanSummary) -> None:
        """Persist scanner totals and terminal status."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                row = session.get(AnalysisSessionRow, str(session_id))
                if row is None:
                    raise AnalysisResultStoreError("Unknown analysis session")
                row.completed_at = datetime.now(UTC)
                row.status = summary.status.value
                row.files_seen = summary.files_seen
                row.directories_seen = summary.directories_seen
                row.total_size_bytes = summary.total_size_bytes
                row.issue_count = summary.issues
                row.duration_ms = summary.duration_ms
        except SQLAlchemyError as exc:
            raise AnalysisResultStoreError("Analysis session completion failed") from exc

    def iter_records(
        self,
        session_id: UUID,
        *,
        batch_size: int = 500,
    ) -> Iterator[tuple[StoredFileRecord, ...]]:
        """Yield keyset-paged records so analyzers have bounded memory usage."""
        self._require_initialized()
        last_id = 0
        while True:
            statement = (
                select(FileRecordRow)
                .where(
                    FileRecordRow.session_id == str(session_id),
                    FileRecordRow.record_id > last_id,
                )
                .order_by(FileRecordRow.record_id)
                .limit(max(1, min(batch_size, 2_000)))
            )
            try:
                with self._sessions() as session:
                    rows = tuple(session.scalars(statement))
            except SQLAlchemyError as exc:
                raise AnalysisResultStoreError("Analysis record iteration failed") from exc
            if not rows:
                return
            models = tuple(self._row_to_record(row) for row in rows)
            yield models
            last_id = models[-1].record_id

    def duplicate_sizes(self, session_id: UUID) -> tuple[int, ...]:
        """Return only sizes shared by at least two non-empty files."""
        statement = (
            select(FileRecordRow.size_bytes)
            .where(FileRecordRow.session_id == str(session_id), FileRecordRow.size_bytes > 0)
            .group_by(FileRecordRow.size_bytes)
            .having(func.count(FileRecordRow.record_id) > 1)
            .order_by(FileRecordRow.size_bytes)
        )
        try:
            with self._sessions() as session:
                return tuple(session.scalars(statement))
        except SQLAlchemyError as exc:
            raise AnalysisResultStoreError("Duplicate-size query failed") from exc

    def record_count(self, session_id: UUID) -> int:
        """Return the number of metadata rows stored for one session."""
        statement = select(func.count(FileRecordRow.record_id)).where(
            FileRecordRow.session_id == str(session_id)
        )
        try:
            with self._sessions() as session:
                return int(session.scalar(statement) or 0)
        except SQLAlchemyError as exc:
            raise AnalysisResultStoreError("Analysis record count failed") from exc

    def records_by_size(self, session_id: UUID, size_bytes: int) -> tuple[StoredFileRecord, ...]:
        """Return one same-size candidate group for staged hashing."""
        statement = select(FileRecordRow).where(
            FileRecordRow.session_id == str(session_id),
            FileRecordRow.size_bytes == size_bytes,
        )
        try:
            with self._sessions() as session:
                return tuple(self._row_to_record(row) for row in session.scalars(statement))
        except SQLAlchemyError as exc:
            raise AnalysisResultStoreError("Duplicate candidates query failed") from exc

    def mark_large(self, record_ids: Sequence[int]) -> None:
        """Mark validated large-file candidates in one bounded update."""
        self._mark_boolean(record_ids, "is_large")

    def mark_inactive(self, record_id: int, assessment: InactiveAssessment) -> None:
        """Store one cautious inactive-file assessment."""
        statement = (
            update(FileRecordRow)
            .where(FileRecordRow.record_id == record_id)
            .values(
                inactive_confidence=assessment.confidence.value,
                inactive_evidence=list(assessment.evidence),
                inactive_threshold_days=assessment.threshold_days,
            )
        )
        self._execute_update(statement, "Inactive assessment update failed")

    def mark_duplicate(self, record_ids: Sequence[int], group_id: str) -> None:
        """Associate verified-equal files with a neutral group identifier."""
        if not record_ids:
            return
        statement = (
            update(FileRecordRow)
            .where(FileRecordRow.record_id.in_(record_ids))
            .values(duplicate_group_id=group_id)
        )
        self._execute_update(statement, "Duplicate group update failed")

    def finalize_matches(
        self,
        session_id: UUID,
        analyses: Sequence[AnalysisType],
        match_mode: AnalysisMatchMode,
    ) -> None:
        """Compute final candidate membership from the confirmed analysis plan."""
        conditions = []
        if AnalysisType.LARGE_FILES in analyses:
            conditions.append(FileRecordRow.is_large.is_(True))
        if AnalysisType.INACTIVE_FILES in analyses:
            conditions.append(FileRecordRow.inactive_confidence.is_not(None))
        if AnalysisType.DUPLICATES in analyses:
            conditions.append(FileRecordRow.duplicate_group_id.is_not(None))
        if not conditions:
            raise ValueError("At least one analysis condition is required")
        predicate = and_(*conditions) if match_mode is AnalysisMatchMode.ALL else or_(*conditions)
        reset = (
            update(FileRecordRow)
            .where(FileRecordRow.session_id == str(session_id))
            .values(matches_plan=False)
        )
        mark = (
            update(FileRecordRow)
            .where(FileRecordRow.session_id == str(session_id), predicate)
            .values(matches_plan=True)
        )
        try:
            with self._sessions.begin() as session:
                session.execute(reset)
                session.execute(mark)
        except SQLAlchemyError as exc:
            raise AnalysisResultStoreError("Final candidate calculation failed") from exc

    def page_candidates(
        self,
        session_id: UUID,
        *,
        offset: int = 0,
        limit: int = 200,
        category: FileCategory | None = None,
        search: str = "",
        minimum_size_bytes: int = 0,
        sort_by: str = "size_bytes",
        descending: bool = True,
    ) -> tuple[StoredFileRecord, ...]:
        """Return one validated, sortable page of final matching candidates."""
        columns = {
            "name": FileRecordRow.name,
            "path": FileRecordRow.path,
            "size_bytes": FileRecordRow.size_bytes,
            "modified_at": FileRecordRow.modified_at,
            "accessed_at": FileRecordRow.accessed_at,
            "category": FileRecordRow.category,
        }
        try:
            sort_column = columns[sort_by]
        except KeyError as exc:
            raise ValueError(f"Unsupported candidate sort column: {sort_by}") from exc
        conditions = [
            FileRecordRow.session_id == str(session_id),
            FileRecordRow.matches_plan.is_(True),
            FileRecordRow.size_bytes >= max(0, minimum_size_bytes),
        ]
        if category is not None:
            conditions.append(FileRecordRow.category == category.value)
        normalized_search = search.strip()
        if normalized_search:
            escaped = (
                normalized_search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            conditions.append(
                or_(
                    FileRecordRow.name.ilike(f"%{escaped}%", escape="\\"),
                    FileRecordRow.path.ilike(f"%{escaped}%", escape="\\"),
                )
            )
        order = sort_column.desc() if descending else sort_column.asc()
        statement = (
            select(FileRecordRow)
            .where(*conditions)
            .order_by(order, FileRecordRow.record_id)
            .offset(max(0, offset))
            .limit(max(1, min(limit, 1_000)))
        )
        try:
            with self._sessions() as session:
                return tuple(self._row_to_record(row) for row in session.scalars(statement))
        except SQLAlchemyError as exc:
            raise AnalysisResultStoreError("Candidate page query failed") from exc

    def matching_totals(self, session_id: UUID) -> tuple[int, int]:
        """Return final candidate count and total bytes."""
        statement = select(
            func.count(FileRecordRow.record_id),
            func.coalesce(func.sum(FileRecordRow.size_bytes), 0),
        ).where(FileRecordRow.session_id == str(session_id), FileRecordRow.matches_plan.is_(True))
        try:
            with self._sessions() as session:
                count, total = session.execute(statement).one()
                return int(count), int(total)
        except SQLAlchemyError as exc:
            raise AnalysisResultStoreError("Candidate totals query failed") from exc

    def iter_matching(
        self,
        session_id: UUID,
        *,
        batch_size: int = 500,
    ) -> Iterator[tuple[StoredFileRecord, ...]]:
        """Yield every final candidate in bounded keyset pages for export."""
        self._require_initialized()
        last_id = 0
        bounded = max(1, min(batch_size, 2_000))
        while True:
            statement = (
                select(FileRecordRow)
                .where(
                    FileRecordRow.session_id == str(session_id),
                    FileRecordRow.matches_plan.is_(True),
                    FileRecordRow.record_id > last_id,
                )
                .order_by(FileRecordRow.record_id)
                .limit(bounded)
            )
            try:
                with self._sessions() as session:
                    rows = tuple(session.scalars(statement))
            except SQLAlchemyError as exc:
                raise AnalysisResultStoreError("Candidate export iteration failed") from exc
            if not rows:
                return
            records = tuple(self._row_to_record(row) for row in rows)
            yield records
            last_id = records[-1].record_id

    def category_summaries(self, session_id: UUID) -> tuple[CategorySummary, ...]:
        """Return count and bytes grouped by final candidate category."""
        statement = (
            select(
                FileRecordRow.category,
                func.count(FileRecordRow.record_id),
                func.coalesce(func.sum(FileRecordRow.size_bytes), 0),
            )
            .where(
                FileRecordRow.session_id == str(session_id),
                FileRecordRow.matches_plan.is_(True),
            )
            .group_by(FileRecordRow.category)
            .order_by(FileRecordRow.category)
        )
        try:
            with self._sessions() as session:
                rows = tuple(session.execute(statement))
        except SQLAlchemyError as exc:
            raise AnalysisResultStoreError("Category summary query failed") from exc
        return tuple(
            CategorySummary(
                category=FileCategory(category), files=int(count), total_bytes=int(total)
            )
            for category, count, total in rows
        )

    def list_issues(self, session_id: UUID, limit: int = 1_000) -> tuple[ScanIssue, ...]:
        """Return a bounded issue list for the GUI and export report."""
        statement = (
            select(AnalysisIssueRow)
            .where(AnalysisIssueRow.session_id == str(session_id))
            .order_by(AnalysisIssueRow.issue_id)
            .limit(max(1, min(limit, 5_000)))
        )
        try:
            with self._sessions() as session:
                rows = tuple(session.scalars(statement))
        except SQLAlchemyError as exc:
            raise AnalysisResultStoreError("Analysis issue query failed") from exc
        return tuple(
            ScanIssue(code=row.code, message=row.message, path=Path(row.path) if row.path else None)
            for row in rows
        )

    def delete_session(self, session_id: UUID) -> None:
        """Delete application-owned temporary metadata for one analysis session."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                session.execute(
                    delete(AnalysisSessionRow).where(
                        AnalysisSessionRow.session_id == str(session_id)
                    )
                )
        except SQLAlchemyError as exc:
            raise AnalysisResultStoreError("Analysis session cleanup failed") from exc

    def close(self) -> None:
        """Dispose result-store connections."""
        self._engine.dispose()
        self._initialized = False

    def _mark_boolean(self, record_ids: Sequence[int], field_name: str) -> None:
        if not record_ids:
            return
        statement = (
            update(FileRecordRow)
            .where(FileRecordRow.record_id.in_(record_ids))
            .values({field_name: True})
        )
        self._execute_update(statement, f"{field_name} update failed")

    def _execute_update(self, statement: Any, message: str) -> None:
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                session.execute(statement)
        except SQLAlchemyError as exc:
            raise AnalysisResultStoreError(message) from exc

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise AnalysisResultStoreError("Analysis result repository is not initialized")

    @staticmethod
    def _metadata_to_row(session_id: UUID, value: FileMetadata) -> FileRecordRow:
        return FileRecordRow(
            session_id=str(session_id),
            path=str(value.path),
            name=value.name,
            extension=value.extension,
            media_type=value.media_type,
            size_bytes=value.size_bytes,
            created_at=value.created_at,
            modified_at=value.modified_at,
            accessed_at=value.accessed_at,
            scan_root=str(value.scan_root),
            hidden=value.hidden,
            read_only=value.read_only,
            system=value.system,
            offline=value.offline,
            category=value.category.value,
            file_id=str(value.file_id) if value.file_id is not None else None,
            device_id=str(value.device_id) if value.device_id is not None else None,
            windows_attributes=value.windows_attributes,
            is_large=False,
            matches_plan=False,
        )

    @staticmethod
    def _row_to_record(row: FileRecordRow) -> StoredFileRecord:
        inactive = None
        if row.inactive_confidence is not None:
            inactive = InactiveAssessment(
                possibly_inactive=True,
                confidence=InactiveConfidence(row.inactive_confidence),
                evidence=tuple(row.inactive_evidence or ()),
                threshold_days=row.inactive_threshold_days or 1,
            )
        metadata = FileMetadata(
            path=Path(row.path),
            name=row.name,
            extension=row.extension,
            media_type=row.media_type,
            size_bytes=row.size_bytes,
            created_at=_as_utc(row.created_at),
            modified_at=_as_utc(row.modified_at),
            accessed_at=_as_utc(row.accessed_at),
            scan_root=Path(row.scan_root),
            hidden=row.hidden,
            read_only=row.read_only,
            system=row.system,
            offline=row.offline,
            category=FileCategory(row.category),
            file_id=int(row.file_id) if row.file_id is not None else None,
            device_id=int(row.device_id) if row.device_id is not None else None,
            windows_attributes=row.windows_attributes,
        )
        return StoredFileRecord(
            record_id=row.record_id,
            metadata=metadata,
            is_large=row.is_large,
            inactive=inactive,
            duplicate_group_id=row.duplicate_group_id,
            matches_plan=row.matches_plan,
        )


def _as_utc(value: datetime) -> datetime:
    """Restore UTC lost by SQLite's timezone-naive datetime representation."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
