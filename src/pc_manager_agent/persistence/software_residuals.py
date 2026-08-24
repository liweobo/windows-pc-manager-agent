"""SQLite persistence for uninstall contexts and read-only residual reports."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import JSON, DateTime, ForeignKey, String, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from pc_manager_agent.domain.software_residuals import (
    ResidualCandidate,
    ResidualReport,
    UninstallContext,
)
from pc_manager_agent.persistence.database import create_sqlite_engine


class SoftwareResidualStoreError(RuntimeError):
    """Raised when Stage 4D3 durable metadata is unavailable or inconsistent."""


class SoftwareResidualBase(DeclarativeBase):
    """Declarative base isolated from other application-owned tables."""


class UninstallContextRow(SoftwareResidualBase):
    """One durable Agent uninstall context stored as validated JSON."""

    __tablename__ = "software_uninstall_contexts"

    context_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    transaction_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    mechanism: Mapped[str] = mapped_column(String(20), index=True)
    identity_digest: Mapped[str] = mapped_column(String(64), index=True)
    verification_state: Mapped[str] = mapped_column(String(100), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(JSON)


class ResidualReportRow(SoftwareResidualBase):
    """One report header; candidate payloads remain individually addressable."""

    __tablename__ = "software_residual_reports"

    report_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    context_id: Mapped[str] = mapped_column(
        ForeignKey("software_uninstall_contexts.context_id"), index=True
    )
    plan_id: Mapped[str] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSON)


class ResidualCandidateRow(SoftwareResidualBase):
    """One metadata-only residual candidate; no file content is retained."""

    __tablename__ = "software_residual_candidates"

    candidate_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    report_id: Mapped[str] = mapped_column(
        ForeignKey("software_residual_reports.report_id"), index=True
    )
    payload: Mapped[dict[str, object]] = mapped_column(JSON)


class SoftwareResidualRepository:
    """Persist exact uninstall contexts and immutable residual analysis reports."""

    def __init__(self, database_path: Path) -> None:
        self._engine = create_sqlite_engine(database_path)
        self._sessions = sessionmaker(self._engine, expire_on_commit=False)
        self._initialized = False

    def initialize(self) -> None:
        """Create additive Stage 4D3 tables without altering uninstall tables."""
        try:
            SoftwareResidualBase.metadata.create_all(self._engine)
        except SQLAlchemyError as exc:
            raise SoftwareResidualStoreError(
                "Residual metadata store initialization failed"
            ) from exc
        self._initialized = True

    def upsert_context(self, context: UninstallContext) -> None:
        """Insert a draft context or replace the same transaction with a newer snapshot."""
        self._require_initialized()
        payload = context.model_dump(mode="json")
        try:
            with self._sessions.begin() as session:
                row = session.scalar(
                    select(UninstallContextRow).where(
                        UninstallContextRow.transaction_id == str(context.transaction_id)
                    )
                )
                if row is None:
                    session.add(
                        UninstallContextRow(
                            context_id=str(context.context_id),
                            transaction_id=str(context.transaction_id),
                            mechanism=context.mechanism.value,
                            identity_digest=context.software_identity_digest,
                            verification_state=context.verification_state,
                            updated_at=datetime.now(UTC),
                            payload=payload,
                        )
                    )
                else:
                    if row.context_id != str(context.context_id):
                        raise SoftwareResidualStoreError(
                            "Uninstall transaction is already bound to another context"
                        )
                    row.mechanism = context.mechanism.value
                    row.identity_digest = context.software_identity_digest
                    row.verification_state = context.verification_state
                    row.updated_at = datetime.now(UTC)
                    row.payload = payload
        except SoftwareResidualStoreError:
            raise
        except SQLAlchemyError as exc:
            raise SoftwareResidualStoreError("Uninstall context persistence failed") from exc

    def get_context(self, context_id: UUID) -> UninstallContext:
        """Load one exact context and revalidate every persisted field."""
        self._require_initialized()
        try:
            with self._sessions() as session:
                row = session.get(UninstallContextRow, str(context_id))
                if row is None:
                    raise SoftwareResidualStoreError("Unknown uninstall context")
                return UninstallContext.model_validate(row.payload)
        except SoftwareResidualStoreError:
            raise
        except (SQLAlchemyError, ValueError, TypeError) as exc:
            raise SoftwareResidualStoreError("Uninstall context could not be validated") from exc

    def get_context_for_transaction(self, transaction_id: UUID) -> UninstallContext:
        """Resolve one Agent transaction to its single uninstall context."""
        self._require_initialized()
        try:
            with self._sessions() as session:
                row = session.scalar(
                    select(UninstallContextRow).where(
                        UninstallContextRow.transaction_id == str(transaction_id)
                    )
                )
                if row is None:
                    raise SoftwareResidualStoreError("No residual context exists for transaction")
                return UninstallContext.model_validate(row.payload)
        except SoftwareResidualStoreError:
            raise
        except (SQLAlchemyError, ValueError, TypeError) as exc:
            raise SoftwareResidualStoreError("Transaction context could not be validated") from exc

    def list_eligible_contexts(self, limit: int = 100) -> tuple[UninstallContext, ...]:
        """Return recent contexts; eligibility is re-evaluated by the domain model."""
        self._require_initialized()
        statement = (
            select(UninstallContextRow)
            .order_by(UninstallContextRow.updated_at.desc())
            .limit(max(1, min(limit, 500)))
        )
        try:
            with self._sessions() as session:
                contexts = tuple(
                    UninstallContext.model_validate(row.payload)
                    for row in session.scalars(statement)
                )
        except (SQLAlchemyError, ValueError, TypeError) as exc:
            raise SoftwareResidualStoreError("Uninstall contexts could not be listed") from exc
        return tuple(context for context in contexts if context.eligible_for_analysis)

    def save_report(self, report: ResidualReport) -> None:
        """Atomically store an immutable report header and all candidate metadata."""
        self._require_initialized()
        header = report.model_dump(mode="json", exclude={"candidates"})
        try:
            with self._sessions.begin() as session:
                if session.get(UninstallContextRow, str(report.context_id)) is None:
                    raise SoftwareResidualStoreError("Residual report references unknown context")
                if session.get(ResidualReportRow, str(report.report_id)) is not None:
                    raise SoftwareResidualStoreError("Residual report ID already exists")
                session.add(
                    ResidualReportRow(
                        report_id=str(report.report_id),
                        context_id=str(report.context_id),
                        plan_id=str(report.plan_id),
                        status=report.status.value,
                        completed_at=report.completed_at,
                        payload=header,
                    )
                )
                # There is deliberately no ORM relationship that could expose a
                # mutable object graph. Flush the immutable parent explicitly so
                # SQLite observes the foreign-key order inside this transaction.
                session.flush()
                session.add_all(
                    ResidualCandidateRow(
                        candidate_id=str(candidate.candidate_id),
                        report_id=str(report.report_id),
                        payload=candidate.model_dump(mode="json"),
                    )
                    for candidate in report.candidates
                )
        except SoftwareResidualStoreError:
            raise
        except SQLAlchemyError as exc:
            raise SoftwareResidualStoreError("Residual report persistence failed") from exc

    def latest_report(self, context_id: UUID) -> ResidualReport | None:
        """Return the newest immutable report for one context."""
        self._require_initialized()
        statement = (
            select(ResidualReportRow)
            .where(ResidualReportRow.context_id == str(context_id))
            .order_by(ResidualReportRow.completed_at.desc())
            .limit(1)
        )
        try:
            with self._sessions() as session:
                row = session.scalar(statement)
                if row is None:
                    return None
                return self._report_from_row(session, row)
        except SoftwareResidualStoreError:
            raise
        except (SQLAlchemyError, ValueError, TypeError) as exc:
            raise SoftwareResidualStoreError("Residual report could not be loaded") from exc

    def get_report(self, report_id: UUID) -> ResidualReport:
        """Load one exact immutable report by ID for Stage 4D4 intent resolution."""
        self._require_initialized()
        try:
            with self._sessions() as session:
                row = session.get(ResidualReportRow, str(report_id))
                if row is None:
                    raise SoftwareResidualStoreError("Unknown residual report")
                return self._report_from_row(session, row)
        except SoftwareResidualStoreError:
            raise
        except (SQLAlchemyError, ValueError, TypeError) as exc:
            raise SoftwareResidualStoreError("Residual report could not be validated") from exc

    def get_candidate(self, context_id: UUID, candidate_id: UUID) -> ResidualCandidate | None:
        """Return one candidate only when its report belongs to the requested context."""
        self._require_initialized()
        statement = (
            select(ResidualCandidateRow)
            .join(
                ResidualReportRow,
                ResidualReportRow.report_id == ResidualCandidateRow.report_id,
            )
            .where(
                ResidualCandidateRow.candidate_id == str(candidate_id),
                ResidualReportRow.context_id == str(context_id),
            )
        )
        try:
            with self._sessions() as session:
                row = session.scalar(statement)
                return None if row is None else ResidualCandidate.model_validate(row.payload)
        except (SQLAlchemyError, ValueError, TypeError) as exc:
            raise SoftwareResidualStoreError("Residual candidate could not be loaded") from exc

    def close(self) -> None:
        """Dispose Stage 4D3 database connections."""
        self._engine.dispose()
        self._initialized = False

    def _report_from_row(self, session: Session, row: ResidualReportRow) -> ResidualReport:
        candidate_rows: Sequence[ResidualCandidateRow] = tuple(
            session.scalars(
                select(ResidualCandidateRow)
                .where(ResidualCandidateRow.report_id == row.report_id)
                .order_by(ResidualCandidateRow.candidate_id)
            )
        )
        candidates = tuple(
            ResidualCandidate.model_validate(candidate.payload) for candidate in candidate_rows
        )
        payload = dict(row.payload)
        payload["candidates"] = [item.model_dump(mode="json") for item in candidates]
        return ResidualReport.model_validate(payload)

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise SoftwareResidualStoreError("Residual metadata repository is not initialized")
