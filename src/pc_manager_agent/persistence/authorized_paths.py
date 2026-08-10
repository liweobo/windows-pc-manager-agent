"""SQLite persistence for explicit path authorization decisions."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, DateTime, String, UniqueConstraint, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from pc_manager_agent.authorization.models import AuthorizedPath, AuthorizedPathKind
from pc_manager_agent.persistence.database import create_sqlite_engine


class AuthorizedPathStoreError(RuntimeError):
    """Raised when authorization persistence cannot be trusted."""


class AuthorizationBase(DeclarativeBase):
    """Declarative base isolated from the audit schema."""


class AuthorizedPathRow(AuthorizationBase):
    """Internal row for one explicit user path decision."""

    __tablename__ = "authorized_paths"
    __table_args__ = (UniqueConstraint("canonical_path", name="uq_authorized_path"),)

    path_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    canonical_path: Mapped[str] = mapped_column(String(2_048), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    favorite: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuthorizedPathRepository:
    """Store and query authorized roots without exposing SQLAlchemy rows."""

    def __init__(self, database_path: Path) -> None:
        self._engine = create_sqlite_engine(database_path)
        self._sessions = sessionmaker(self._engine, expire_on_commit=False)
        self._initialized = False

    def initialize(self) -> None:
        """Create the authorization table and mark the repository ready."""
        try:
            AuthorizationBase.metadata.create_all(self._engine)
        except SQLAlchemyError as exc:
            raise AuthorizedPathStoreError("Authorization database initialization failed") from exc
        self._initialized = True

    def add(self, record: AuthorizedPath) -> AuthorizedPath:
        """Persist a validated record and reject canonical-path collisions."""
        self._require_initialized()
        row = AuthorizedPathRow(
            path_id=str(record.path_id),
            canonical_path=str(record.path),
            label=record.label,
            kind=record.kind.value,
            favorite=record.favorite,
            created_at=record.created_at,
        )
        try:
            with self._sessions.begin() as session:
                session.add(row)
        except IntegrityError as exc:
            raise AuthorizedPathStoreError("This canonical path is already configured") from exc
        except SQLAlchemyError as exc:
            raise AuthorizedPathStoreError("Authorization record write failed") from exc
        return record

    def remove(self, path_id: UUID) -> bool:
        """Remove exactly one record by its opaque identifier."""
        self._require_initialized()
        try:
            with self._sessions.begin() as session:
                row = session.get(AuthorizedPathRow, str(path_id))
                if row is None:
                    return False
                session.delete(row)
                return True
        except SQLAlchemyError as exc:
            raise AuthorizedPathStoreError("Authorization record removal failed") from exc

    def list(self, kind: AuthorizedPathKind | None = None) -> tuple[AuthorizedPath, ...]:
        """Return configured roots in stable creation order."""
        self._require_initialized()
        statement = select(AuthorizedPathRow).order_by(AuthorizedPathRow.created_at)
        if kind is not None:
            statement = statement.where(AuthorizedPathRow.kind == kind.value)
        try:
            with self._sessions() as session:
                rows = tuple(session.scalars(statement))
        except SQLAlchemyError as exc:
            raise AuthorizedPathStoreError("Authorization query failed") from exc
        return tuple(self._to_model(row) for row in rows)

    def get(self, path_id: UUID) -> AuthorizedPath | None:
        """Return one record or ``None`` when the identifier is unknown."""
        self._require_initialized()
        statement = select(AuthorizedPathRow).where(AuthorizedPathRow.path_id == str(path_id))
        try:
            with self._sessions() as session:
                row = session.scalar(statement)
        except SQLAlchemyError as exc:
            raise AuthorizedPathStoreError("Authorization lookup failed") from exc
        return None if row is None else self._to_model(row)

    def close(self) -> None:
        """Dispose repository connections."""
        self._engine.dispose()
        self._initialized = False

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise AuthorizedPathStoreError("Authorization repository is not initialized")

    @staticmethod
    def _to_model(row: AuthorizedPathRow) -> AuthorizedPath:
        values: dict[str, Any] = {
            "path_id": row.path_id,
            "path": row.canonical_path,
            "label": row.label,
            "kind": row.kind,
            "favorite": row.favorite,
            "created_at": _as_utc(row.created_at),
        }
        return AuthorizedPath.model_validate(values)


def _as_utc(value: datetime) -> datetime:
    """Restore UTC timezone metadata omitted by SQLite."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
