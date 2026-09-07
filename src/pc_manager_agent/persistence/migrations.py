"""Fail-closed SQLite schema migrations with verified, retained pre-change backups."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Callable, Iterable, Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, create_engine

from pc_manager_agent.persistence.database import create_sqlite_engine
from pc_manager_agent.persistence.schema_catalog import (
    create_current_schema,
    expected_table_names,
)
from pc_manager_agent.safety.path_policy import is_reparse_point

CURRENT_SCHEMA_VERSION = 1
CURRENT_CONFIG_VERSION = 1
_SCHEMA_TABLE = "app_schema"
_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {_SCHEMA_TABLE} (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    schema_version INTEGER NOT NULL CHECK (schema_version >= 0),
    config_version INTEGER NOT NULL CHECK (config_version >= 0),
    state TEXT NOT NULL,
    source_version INTEGER,
    target_version INTEGER,
    started_at TEXT,
    completed_at TEXT,
    backup_file TEXT,
    backup_sha256 TEXT,
    schema_digest TEXT NOT NULL,
    app_version TEXT NOT NULL
)
"""


class MigrationState(StrEnum):
    """Durable migration lifecycle; only READY allows repository initialization."""

    READY = "READY"
    IN_PROGRESS = "IN_PROGRESS"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


class MigrationError(RuntimeError):
    """Stable, content-free startup failure for unsafe database state."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class MigrationBackupEvidence(BaseModel):
    """Verified local backup metadata; the backup itself is never placed in diagnostics."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: Path
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=1)
    quick_check: str


class MigrationReport(BaseModel):
    """Content-free startup result for release evidence and support diagnostics."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    from_version: int | None
    to_version: int
    config_version: int
    migrated: bool
    backup: MigrationBackupEvidence | None
    schema_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    table_count: int = Field(ge=1)


@dataclass(frozen=True, slots=True)
class MigrationStep:
    """One exact adjacent schema transition."""

    source_version: int
    target_version: int
    apply: Callable[[Connection], None]


@dataclass(frozen=True, slots=True)
class _SchemaRecord:
    schema_version: int
    config_version: int
    state: MigrationState
    schema_digest: str


class MigrationBackupService:
    """Create a SQLite online backup, verify integrity, and never overwrite a backup."""

    def create_verified_backup(self, database_path: Path) -> MigrationBackupEvidence:
        """Back up an existing non-reparse database before any migration metadata write."""
        try:
            source = database_path.resolve(strict=True)
        except OSError as exc:
            raise MigrationError("MIGRATION_SOURCE_UNSAFE") from exc
        if not source.is_file() or is_reparse_point(database_path) or is_reparse_point(source):
            raise MigrationError("MIGRATION_SOURCE_UNSAFE")
        backup_directory = source.parent / "migration-backups"
        backup_directory.mkdir(parents=False, exist_ok=True)
        if is_reparse_point(backup_directory):
            raise MigrationError("MIGRATION_BACKUP_DIRECTORY_UNSAFE")
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        target = backup_directory / (
            f"{source.name}.v{self._read_user_version(source)}.{timestamp}.{uuid4().hex}.bak"
        )
        try:
            target.touch(exist_ok=False)
            with (
                closing(sqlite3.connect(source, timeout=10.0)) as source_connection,
                closing(sqlite3.connect(target, timeout=10.0)) as target_connection,
            ):
                source_connection.execute("PRAGMA query_only=ON")
                source_connection.backup(target_connection)
                target_connection.commit()
            quick_check = _integrity_check(target)
            if quick_check != "ok":
                raise MigrationError("MIGRATION_BACKUP_CORRUPT")
            return MigrationBackupEvidence(
                path=target,
                sha256=_sha256_file(target),
                size_bytes=target.stat().st_size,
                quick_check=quick_check,
            )
        except MigrationError:
            target.unlink(missing_ok=True)
            raise
        except Exception as exc:
            target.unlink(missing_ok=True)
            raise MigrationError("MIGRATION_BACKUP_FAILED") from exc

    @staticmethod
    def restore_to_new_path(evidence: MigrationBackupEvidence, destination: Path) -> Path:
        """Copy a verified backup to an absent recovery path; never replace the live database."""
        try:
            source = evidence.path.resolve(strict=True)
        except OSError as exc:
            raise MigrationError("MIGRATION_BACKUP_VERIFICATION_FAILED") from exc
        if _sha256_file(source) != evidence.sha256 or _integrity_check(source) != "ok":
            raise MigrationError("MIGRATION_BACKUP_VERIFICATION_FAILED")
        if destination.parent.exists() and is_reparse_point(destination.parent):
            raise MigrationError("MIGRATION_RECOVERY_TARGET_UNSAFE")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise MigrationError("MIGRATION_RECOVERY_TARGET_UNSAFE")
        try:
            with source.open("rb") as reader, destination.open("xb") as writer:
                for chunk in iter(lambda: reader.read(1024 * 1024), b""):
                    writer.write(chunk)
                writer.flush()
                os.fsync(writer.fileno())
        except Exception as exc:
            destination.unlink(missing_ok=True)
            raise MigrationError("MIGRATION_RECOVERY_COPY_FAILED") from exc
        if _sha256_file(destination) != evidence.sha256:
            destination.unlink(missing_ok=True)
            raise MigrationError("MIGRATION_RECOVERY_COPY_MISMATCH")
        return destination

    @staticmethod
    def _read_user_version(database_path: Path) -> int:
        with closing(sqlite3.connect(database_path, timeout=10.0)) as connection:
            return int(connection.execute("PRAGMA user_version").fetchone()[0])


class MigrationManager:
    """Upgrade one SQLite store exactly once or stop with recovery-required evidence."""

    def __init__(
        self,
        database_path: Path,
        app_version: str,
        *,
        backup_service: MigrationBackupService | None = None,
        steps: tuple[MigrationStep, ...] | None = None,
    ) -> None:
        self._database_path = database_path
        self._app_version = app_version
        self._backup_service = backup_service or MigrationBackupService()
        self._steps = steps if steps is not None else (MigrationStep(0, 1, create_current_schema),)
        self._lock_path = database_path.with_suffix(f"{database_path.suffix}.migration.lock")
        self._recovery_marker = database_path.with_suffix(
            f"{database_path.suffix}.migration-recovery-required"
        )

    def migrate(self) -> MigrationReport:
        """Verify, back up and migrate before any repository can use the database."""
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        if is_reparse_point(self._database_path.parent) or is_reparse_point(self._database_path):
            raise MigrationError("MIGRATION_DATA_DIRECTORY_UNSAFE")
        if self._recovery_marker.exists() or self._lock_path.exists():
            raise MigrationError("MIGRATION_RECOVERY_REQUIRED")
        lock_descriptor = self._acquire_lock()
        try:
            if not self._database_path.exists() or self._database_path.stat().st_size == 0:
                return self._initialize_fresh_database()
            _require_healthy_database(self._database_path)
            record = self._read_record()
            if record is not None:
                if record.state is not MigrationState.READY:
                    raise MigrationError("MIGRATION_RECOVERY_REQUIRED")
                if record.schema_version > CURRENT_SCHEMA_VERSION:
                    raise MigrationError("DATABASE_DOWNGRADE_NOT_SUPPORTED")
                if record.config_version > CURRENT_CONFIG_VERSION:
                    raise MigrationError("CONFIG_DOWNGRADE_NOT_SUPPORTED")
                if record.schema_version == CURRENT_SCHEMA_VERSION:
                    return self._verify_current_database(record)
                source_version = record.schema_version
            else:
                source_version = 0
            backup = self._backup_service.create_verified_backup(self._database_path)
            return self._migrate_existing(source_version, backup)
        finally:
            os.close(lock_descriptor)
            self._lock_path.unlink(missing_ok=True)

    def _initialize_fresh_database(self) -> MigrationReport:
        engine = create_sqlite_engine(self._database_path)
        try:
            with engine.connect() as connection:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                try:
                    create_current_schema(connection)
                    connection.exec_driver_sql(_SCHEMA_SQL)
                    digest = _schema_digest(connection)
                    connection.exec_driver_sql(
                        """INSERT INTO app_schema
                        (singleton, schema_version, config_version, state, completed_at,
                         schema_digest, app_version)
                        VALUES (1, ?, ?, ?, ?, ?, ?)""",
                        (
                            CURRENT_SCHEMA_VERSION,
                            CURRENT_CONFIG_VERSION,
                            MigrationState.READY.value,
                            _utc_now(),
                            digest,
                            self._app_version,
                        ),
                    )
                    connection.exec_driver_sql(f"PRAGMA user_version={CURRENT_SCHEMA_VERSION}")
                    connection.exec_driver_sql("COMMIT")
                except Exception:
                    connection.exec_driver_sql("ROLLBACK")
                    raise
            return self._verify_current_database(self._read_record_required(), migrated=True)
        except Exception as exc:
            self._write_recovery_marker(None, "FRESH_DATABASE_INITIALIZATION_FAILED")
            raise MigrationError("FRESH_DATABASE_INITIALIZATION_FAILED") from exc
        finally:
            engine.dispose()

    def _migrate_existing(
        self, source_version: int, backup: MigrationBackupEvidence
    ) -> MigrationReport:
        self._mark_in_progress(source_version, backup)
        current = source_version
        try:
            while current < CURRENT_SCHEMA_VERSION:
                step = next(
                    (
                        item
                        for item in self._steps
                        if item.source_version == current and item.target_version == current + 1
                    ),
                    None,
                )
                if step is None:
                    raise MigrationError("MIGRATION_STEP_MISSING")
                self._apply_step(step, final=step.target_version == CURRENT_SCHEMA_VERSION)
                current = step.target_version
            return self._verify_current_database(
                self._read_record_required(),
                migrated=True,
                backup=backup,
                from_version=source_version,
            )
        except Exception as exc:
            self._mark_recovery_required()
            self._write_recovery_marker(backup, "MIGRATION_FAILED")
            if isinstance(exc, MigrationError):
                raise
            raise MigrationError("MIGRATION_FAILED") from exc

    def _mark_in_progress(self, source_version: int, backup: MigrationBackupEvidence) -> None:
        try:
            with closing(sqlite3.connect(self._database_path, timeout=10.0)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(_SCHEMA_SQL)
                connection.execute("DELETE FROM app_schema")
                connection.execute(
                    """INSERT INTO app_schema
                    (singleton, schema_version, config_version, state, source_version,
                     target_version, started_at, backup_file, backup_sha256,
                     schema_digest, app_version)
                    VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, '', ?)""",
                    (
                        source_version,
                        CURRENT_CONFIG_VERSION,
                        MigrationState.IN_PROGRESS.value,
                        source_version,
                        CURRENT_SCHEMA_VERSION,
                        _utc_now(),
                        backup.path.name,
                        backup.sha256,
                        self._app_version,
                    ),
                )
                connection.commit()
        except Exception as exc:
            self._write_recovery_marker(backup, "MIGRATION_MARKER_FAILED")
            raise MigrationError("MIGRATION_MARKER_FAILED") from exc

    def _apply_step(self, step: MigrationStep, *, final: bool) -> None:
        engine = create_sqlite_engine(self._database_path)
        try:
            with engine.connect() as connection:
                # Python's sqlite driver may otherwise auto-commit DDL before SQLAlchemy
                # begins a transaction. Explicit BEGIN keeps schema and version changes atomic.
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                try:
                    step.apply(connection)
                    digest = _schema_digest(connection) if final else ""
                    state = MigrationState.READY if final else MigrationState.IN_PROGRESS
                    completed_at = _utc_now() if final else None
                    connection.exec_driver_sql(
                        """UPDATE app_schema
                        SET schema_version=?, config_version=?, state=?, schema_digest=?,
                            completed_at=?, app_version=?
                        WHERE singleton=1 AND schema_version=? AND state=?""",
                        (
                            step.target_version,
                            CURRENT_CONFIG_VERSION,
                            state.value,
                            digest,
                            completed_at,
                            self._app_version,
                            step.source_version,
                            MigrationState.IN_PROGRESS.value,
                        ),
                    )
                    changed = connection.exec_driver_sql("SELECT changes()").scalar_one()
                    if changed != 1:
                        raise MigrationError("MIGRATION_STATE_CHANGED")
                    connection.exec_driver_sql(f"PRAGMA user_version={step.target_version}")
                    connection.exec_driver_sql("COMMIT")
                except Exception:
                    connection.exec_driver_sql("ROLLBACK")
                    raise
        finally:
            engine.dispose()

    def _verify_current_database(
        self,
        record: _SchemaRecord,
        *,
        migrated: bool = False,
        backup: MigrationBackupEvidence | None = None,
        from_version: int | None = None,
    ) -> MigrationReport:
        if (
            record.state is not MigrationState.READY
            or record.schema_version != CURRENT_SCHEMA_VERSION
            or record.config_version != CURRENT_CONFIG_VERSION
        ):
            raise MigrationError("MIGRATION_RECOVERY_REQUIRED")
        _require_healthy_database(self._database_path)
        with closing(sqlite3.connect(self._database_path, timeout=10.0)) as connection:
            user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            tables = frozenset(
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
                if not str(row[0]).startswith("sqlite_")
            )
            digest = _schema_digest_dbapi(connection)
        expected = expected_table_names() | {_SCHEMA_TABLE}
        if user_version != CURRENT_SCHEMA_VERSION:
            raise MigrationError("MIGRATION_VERSION_MISMATCH")
        if tables != expected:
            raise MigrationError("MIGRATION_SCHEMA_INCOMPLETE")
        if digest != _expected_schema_digest() or digest != record.schema_digest:
            raise MigrationError("MIGRATION_SCHEMA_DRIFT")
        return MigrationReport(
            from_version=from_version,
            to_version=CURRENT_SCHEMA_VERSION,
            config_version=CURRENT_CONFIG_VERSION,
            migrated=migrated,
            backup=backup,
            schema_digest=digest,
            table_count=len(tables),
        )

    def _read_record(self) -> _SchemaRecord | None:
        with closing(sqlite3.connect(self._database_path, timeout=10.0)) as connection:
            table_exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (_SCHEMA_TABLE,),
            ).fetchone()
            if table_exists is None:
                return None
            try:
                rows = connection.execute(
                    """SELECT schema_version, config_version, state, schema_digest
                    FROM app_schema"""
                ).fetchall()
            except sqlite3.DatabaseError as exc:
                raise MigrationError("MIGRATION_METADATA_INVALID") from exc
        if len(rows) != 1:
            raise MigrationError("MIGRATION_METADATA_INVALID")
        try:
            return _SchemaRecord(
                schema_version=int(rows[0][0]),
                config_version=int(rows[0][1]),
                state=MigrationState(str(rows[0][2])),
                schema_digest=str(rows[0][3]),
            )
        except (TypeError, ValueError) as exc:
            raise MigrationError("MIGRATION_METADATA_INVALID") from exc

    def _read_record_required(self) -> _SchemaRecord:
        record = self._read_record()
        if record is None:
            raise MigrationError("MIGRATION_METADATA_MISSING")
        return record

    def _mark_recovery_required(self) -> None:
        try:
            with closing(sqlite3.connect(self._database_path, timeout=10.0)) as connection:
                connection.execute(
                    "UPDATE app_schema SET state=? WHERE singleton=1",
                    (MigrationState.RECOVERY_REQUIRED.value,),
                )
                connection.commit()
        except sqlite3.DatabaseError:
            return

    def _write_recovery_marker(self, backup: MigrationBackupEvidence | None, reason: str) -> None:
        payload = json.dumps(
            {
                "reason": reason,
                "backup_file": backup.path.name if backup is not None else None,
                "backup_sha256": backup.sha256 if backup is not None else None,
                "target_schema_version": CURRENT_SCHEMA_VERSION,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        try:
            with self._recovery_marker.open("x", encoding="utf-8", newline="\n") as marker:
                marker.write(payload)
                marker.flush()
                os.fsync(marker.fileno())
        except FileExistsError:
            return

    def _acquire_lock(self) -> int:
        try:
            return os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise MigrationError("MIGRATION_RECOVERY_REQUIRED") from exc


def _require_healthy_database(database_path: Path) -> None:
    if _integrity_check(database_path) != "ok":
        raise MigrationError("DATABASE_INTEGRITY_CHECK_FAILED")
    with closing(sqlite3.connect(database_path, timeout=10.0)) as connection:
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise MigrationError("DATABASE_FOREIGN_KEY_CHECK_FAILED")


def _integrity_check(database_path: Path) -> str:
    try:
        with closing(sqlite3.connect(database_path, timeout=10.0)) as connection:
            rows = connection.execute("PRAGMA quick_check").fetchall()
    except sqlite3.DatabaseError as exc:
        raise MigrationError("DATABASE_INTEGRITY_CHECK_FAILED") from exc
    if len(rows) != 1:
        return "failed"
    return str(rows[0][0])


def _schema_digest(connection: Connection) -> str:
    rows = connection.exec_driver_sql(
        """SELECT type, name, tbl_name, sql FROM sqlite_master
        WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"""
    ).all()
    return _digest_schema_rows(rows)


def _schema_digest_dbapi(connection: sqlite3.Connection) -> str:
    rows = connection.execute(
        """SELECT type, name, tbl_name, sql FROM sqlite_master
        WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"""
    ).fetchall()
    return _digest_schema_rows(rows)


@lru_cache(maxsize=1)
def _expected_schema_digest() -> str:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    try:
        with engine.begin() as connection:
            create_current_schema(connection)
            connection.exec_driver_sql(_SCHEMA_SQL)
            return _schema_digest(connection)
    finally:
        engine.dispose()


def _digest_schema_rows(rows: Iterable[Sequence[object]]) -> str:
    normalized = [tuple(row) for row in rows]
    canonical = json.dumps(normalized, ensure_ascii=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
