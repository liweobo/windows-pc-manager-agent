"""Production SQLite migration, backup, interruption, and downgrade tests."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy import Connection, MetaData

import pc_manager_agent.persistence.migrations as migration_module
from pc_manager_agent.audit.repository import Base as AuditBase
from pc_manager_agent.persistence.analysis_results import AnalysisBase
from pc_manager_agent.persistence.authorized_paths import AuthorizationBase
from pc_manager_agent.persistence.database import create_sqlite_engine
from pc_manager_agent.persistence.migrations import (
    CURRENT_CONFIG_VERSION,
    CURRENT_SCHEMA_VERSION,
    MigrationBackupService,
    MigrationError,
    MigrationManager,
    MigrationStep,
)
from pc_manager_agent.persistence.schema_catalog import expected_table_names


def _create_legacy(path: Path, metadata: tuple[MetaData, ...]) -> None:
    engine = create_sqlite_engine(path)
    try:
        with engine.begin() as connection:
            for item in metadata:
                item.create_all(connection)
    finally:
        engine.dispose()


def _row(path: Path, sql: str) -> tuple[object, ...]:
    with closing(sqlite3.connect(path)) as connection:
        value = connection.execute(sql).fetchone()
    assert value is not None
    return value


def test_fresh_database_creates_complete_versioned_schema(tmp_path: Path) -> None:
    database = tmp_path / "state.db"

    report = MigrationManager(database, "0.1.0").migrate()

    assert report.migrated
    assert report.from_version is None
    assert report.to_version == CURRENT_SCHEMA_VERSION
    assert report.config_version == CURRENT_CONFIG_VERSION
    assert report.backup is None
    assert report.table_count == len(expected_table_names()) + 1
    assert _row(database, "PRAGMA user_version") == (CURRENT_SCHEMA_VERSION,)
    assert _row(database, "SELECT state FROM app_schema") == ("READY",)


def test_current_database_reopens_without_backup_or_schema_writes(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    first = MigrationManager(database, "0.1.0").migrate()
    stat_before = database.stat()

    second = MigrationManager(database, "0.1.0").migrate()

    assert not second.migrated
    assert second.backup is None
    assert second.schema_digest == first.schema_digest
    assert database.stat().st_size == stat_before.st_size
    assert not (tmp_path / "migration-backups").exists()


@pytest.mark.parametrize(
    "metadata",
    [
        (AuthorizationBase.metadata,),
        (AuditBase.metadata,),
        (AuthorizationBase.metadata, AuditBase.metadata, AnalysisBase.metadata),
    ],
)
def test_multiple_legacy_schema_shapes_upgrade_with_verified_backup(
    tmp_path: Path,
    metadata: tuple[MetaData, ...],
) -> None:
    database = tmp_path / "state.db"
    _create_legacy(database, metadata)

    report = MigrationManager(database, "0.1.0").migrate()

    assert report.from_version == 0
    assert report.backup is not None
    assert report.backup.path.is_file()
    assert report.backup.quick_check == "ok"
    assert len(report.backup.sha256) == 64
    assert report.backup.path.parent.name == "migration-backups"
    assert _row(database, "SELECT state, schema_version FROM app_schema") == (
        "READY",
        CURRENT_SCHEMA_VERSION,
    )


def test_corrupt_database_is_never_recreated_or_backed_up(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    original = b"not a sqlite database\x00user material"
    database.write_bytes(original)

    with pytest.raises(MigrationError, match="DATABASE_INTEGRITY_CHECK_FAILED"):
        MigrationManager(database, "0.1.0").migrate()

    assert database.read_bytes() == original
    assert not (tmp_path / "migration-backups").exists()


def test_interrupted_state_and_stale_lock_fail_closed(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    MigrationManager(database, "0.1.0").migrate()
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("UPDATE app_schema SET state='IN_PROGRESS'")
        connection.commit()

    with pytest.raises(MigrationError, match="MIGRATION_RECOVERY_REQUIRED"):
        MigrationManager(database, "0.1.0").migrate()

    with closing(sqlite3.connect(database)) as connection:
        connection.execute("UPDATE app_schema SET state='READY'")
        connection.commit()
    lock = database.with_suffix(".db.migration.lock")
    lock.write_text("interrupted", encoding="utf-8")
    with pytest.raises(MigrationError, match="MIGRATION_RECOVERY_REQUIRED"):
        MigrationManager(database, "0.1.0").migrate()


@pytest.mark.parametrize(
    ("column", "value", "code"),
    [
        ("schema_version", CURRENT_SCHEMA_VERSION + 1, "DATABASE_DOWNGRADE_NOT_SUPPORTED"),
        ("config_version", CURRENT_CONFIG_VERSION + 1, "CONFIG_DOWNGRADE_NOT_SUPPORTED"),
    ],
)
def test_future_database_or_config_version_blocks_downgrade(
    tmp_path: Path,
    column: str,
    value: int,
    code: str,
) -> None:
    database = tmp_path / "state.db"
    MigrationManager(database, "0.1.0").migrate()
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(f"UPDATE app_schema SET {column}=?", (value,))
        connection.commit()

    with pytest.raises(MigrationError, match=code):
        MigrationManager(database, "0.1.0").migrate()


def test_missing_step_retains_backup_and_requires_manual_recovery(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    _create_legacy(database, (AuthorizationBase.metadata,))
    manager = MigrationManager(database, "0.1.0", steps=())

    with pytest.raises(MigrationError, match="MIGRATION_STEP_MISSING"):
        manager.migrate()

    marker = database.with_suffix(".db.migration-recovery-required")
    assert marker.is_file()
    assert _row(database, "SELECT state FROM app_schema") == ("RECOVERY_REQUIRED",)
    backups = tuple((tmp_path / "migration-backups").glob("*.bak"))
    assert len(backups) == 1
    with pytest.raises(MigrationError, match="MIGRATION_RECOVERY_REQUIRED"):
        manager.migrate()


def test_failed_transaction_preserves_legacy_backup(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    _create_legacy(database, (AuthorizationBase.metadata,))

    def fail_after_schema_write(connection: Connection) -> None:
        connection.exec_driver_sql("CREATE TABLE must_roll_back (id INTEGER PRIMARY KEY)")
        raise RuntimeError("synthetic failure")

    manager = MigrationManager(
        database,
        "0.1.0",
        steps=(MigrationStep(0, 1, fail_after_schema_write),),
    )

    with pytest.raises(MigrationError, match="MIGRATION_FAILED"):
        manager.migrate()

    assert _row(database, "SELECT state FROM app_schema") == ("RECOVERY_REQUIRED",)
    with closing(sqlite3.connect(database)) as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='must_roll_back'"
            ).fetchone()
            is None
        )
    backup = next((tmp_path / "migration-backups").glob("*.bak"))
    assert _row(backup, "PRAGMA quick_check") == ("ok",)


def test_verified_backup_restore_never_overwrites_live_database(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    _create_legacy(database, (AuthorizationBase.metadata,))
    evidence = MigrationBackupService().create_verified_backup(database)
    recovery = tmp_path / "manual-recovery" / "state-restored.db"

    restored = MigrationBackupService.restore_to_new_path(evidence, recovery)

    assert restored == recovery
    assert recovery.read_bytes() == evidence.path.read_bytes()
    with pytest.raises(MigrationError, match="MIGRATION_RECOVERY_TARGET_UNSAFE"):
        MigrationBackupService.restore_to_new_path(evidence, recovery)


def test_current_schema_drift_is_not_silently_repaired(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    MigrationManager(database, "0.1.0").migrate()
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("ALTER TABLE authorized_paths ADD COLUMN unexpected TEXT")
        connection.commit()

    with pytest.raises(MigrationError, match="MIGRATION_SCHEMA_DRIFT"):
        MigrationManager(database, "0.1.0").migrate()


def test_backup_failure_leaves_source_unchanged_and_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "state.db"
    _create_legacy(database, (AuthorizationBase.metadata,))
    original = database.read_bytes()

    def fail_backup(_path: Path) -> object:
        raise MigrationError("BACKUP_FAILED")

    backup_service = MigrationBackupService()
    monkeypatch.setattr(backup_service, "create_verified_backup", fail_backup)

    with pytest.raises(MigrationError, match="BACKUP_FAILED"):
        MigrationManager(database, "0.1.0", backup_service=backup_service).migrate()

    assert database.read_bytes() == original
    assert not database.with_suffix(".db.migration-recovery-required").exists()


def test_backup_rejects_missing_or_non_file_source(tmp_path: Path) -> None:
    with pytest.raises(MigrationError, match="MIGRATION_SOURCE_UNSAFE"):
        MigrationBackupService().create_verified_backup(tmp_path / "missing.db")
    with pytest.raises(MigrationError, match="MIGRATION_SOURCE_UNSAFE"):
        MigrationBackupService().create_verified_backup(tmp_path)


def test_backup_rejects_reparse_backup_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "state.db"
    _create_legacy(database, (AuthorizationBase.metadata,))
    monkeypatch.setattr(
        migration_module,
        "is_reparse_point",
        lambda path: path.name == "migration-backups",
    )

    with pytest.raises(MigrationError, match="MIGRATION_BACKUP_DIRECTORY_UNSAFE"):
        MigrationBackupService().create_verified_backup(database)


def test_corrupt_backup_is_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "state.db"
    _create_legacy(database, (AuthorizationBase.metadata,))
    original_check = migration_module._integrity_check
    monkeypatch.setattr(
        migration_module,
        "_integrity_check",
        lambda path: "failed" if path.suffix == ".bak" else original_check(path),
    )

    with pytest.raises(MigrationError, match="MIGRATION_BACKUP_CORRUPT"):
        MigrationBackupService().create_verified_backup(database)

    assert tuple((tmp_path / "migration-backups").iterdir()) == ()


def test_restore_rejects_changed_backup_and_cleans_mismatched_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "state.db"
    _create_legacy(database, (AuthorizationBase.metadata,))
    evidence = MigrationBackupService().create_verified_backup(database)
    changed = evidence.model_copy(update={"sha256": "0" * 64})
    with pytest.raises(MigrationError, match="MIGRATION_BACKUP_VERIFICATION_FAILED"):
        MigrationBackupService.restore_to_new_path(changed, tmp_path / "changed.db")

    destination = tmp_path / "copy-mismatch.db"
    original_hash = migration_module._sha256_file
    monkeypatch.setattr(
        migration_module,
        "_sha256_file",
        lambda path: "f" * 64 if path == destination else original_hash(path),
    )
    with pytest.raises(MigrationError, match="MIGRATION_RECOVERY_COPY_MISMATCH"):
        MigrationBackupService.restore_to_new_path(evidence, destination)
    assert not destination.exists()


def test_restore_rejects_missing_backup_and_reparse_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "state.db"
    _create_legacy(database, (AuthorizationBase.metadata,))
    evidence = MigrationBackupService().create_verified_backup(database)
    missing = evidence.model_copy(update={"path": tmp_path / "missing.bak"})
    with pytest.raises(MigrationError, match="MIGRATION_BACKUP_VERIFICATION_FAILED"):
        MigrationBackupService.restore_to_new_path(missing, tmp_path / "unused.db")

    destination = tmp_path / "linked-parent" / "restore.db"
    monkeypatch.setattr(
        migration_module,
        "is_reparse_point",
        lambda path: path == destination.parent,
    )
    destination.parent.mkdir()
    with pytest.raises(MigrationError, match="MIGRATION_RECOVERY_TARGET_UNSAFE"):
        MigrationBackupService.restore_to_new_path(evidence, destination)


def test_restore_io_failure_removes_partial_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "state.db"
    _create_legacy(database, (AuthorizationBase.metadata,))
    evidence = MigrationBackupService().create_verified_backup(database)
    destination = tmp_path / "partial.db"

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("disk")

    monkeypatch.setattr(migration_module.os, "fsync", fail_fsync)

    with pytest.raises(MigrationError, match="MIGRATION_RECOVERY_COPY_FAILED"):
        MigrationBackupService.restore_to_new_path(evidence, destination)

    assert not destination.exists()


def test_migration_rejects_reparse_data_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "unsafe-data" / "state.db"
    monkeypatch.setattr(
        migration_module,
        "is_reparse_point",
        lambda path: path == database.parent,
    )

    with pytest.raises(MigrationError, match="MIGRATION_DATA_DIRECTORY_UNSAFE"):
        MigrationManager(database, "0.1.0").migrate()

    monkeypatch.setattr(
        migration_module,
        "is_reparse_point",
        lambda path: path == database,
    )
    with pytest.raises(MigrationError, match="MIGRATION_DATA_DIRECTORY_UNSAFE"):
        MigrationManager(database, "0.1.0").migrate()


def test_fresh_schema_failure_sets_durable_recovery_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "state.db"

    def fail_schema(_connection: Connection) -> None:
        raise RuntimeError("synthetic fresh initialization failure")

    monkeypatch.setattr(migration_module, "create_current_schema", fail_schema)
    with pytest.raises(MigrationError, match="FRESH_DATABASE_INITIALIZATION_FAILED"):
        MigrationManager(database, "0.1.0").migrate()

    marker = database.with_suffix(".db.migration-recovery-required")
    assert marker.is_file()
    assert "FRESH_DATABASE_INITIALIZATION_FAILED" in marker.read_text(encoding="utf-8")


def test_explicit_version_zero_metadata_uses_adjacent_migration(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    MigrationManager(database, "0.1.0").migrate()
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("UPDATE app_schema SET schema_version=0")
        connection.execute("PRAGMA user_version=0")
        connection.commit()

    report = MigrationManager(database, "0.1.0").migrate()

    assert report.migrated
    assert report.from_version == 0
    assert report.backup is not None


def test_step_cannot_change_durable_migration_state(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    _create_legacy(database, (AuthorizationBase.metadata,))

    def tamper_state(connection: Connection) -> None:
        connection.exec_driver_sql("UPDATE app_schema SET schema_version=99")

    with pytest.raises(MigrationError, match="MIGRATION_STATE_CHANGED"):
        MigrationManager(
            database,
            "0.1.0",
            steps=(MigrationStep(0, 1, tamper_state),),
        ).migrate()

    assert database.with_suffix(".db.migration-recovery-required").is_file()


def test_old_config_version_and_mismatched_user_version_fail_closed(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    MigrationManager(database, "0.1.0").migrate()
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("UPDATE app_schema SET config_version=0")
        connection.commit()
    with pytest.raises(MigrationError, match="MIGRATION_RECOVERY_REQUIRED"):
        MigrationManager(database, "0.1.0").migrate()

    with closing(sqlite3.connect(database)) as connection:
        connection.execute("UPDATE app_schema SET config_version=?", (CURRENT_CONFIG_VERSION,))
        connection.execute("PRAGMA user_version=0")
        connection.commit()
    with pytest.raises(MigrationError, match="MIGRATION_VERSION_MISMATCH"):
        MigrationManager(database, "0.1.0").migrate()


def test_missing_table_and_invalid_metadata_are_rejected(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    MigrationManager(database, "0.1.0").migrate()
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("DROP TABLE authorized_paths")
        connection.commit()
    with pytest.raises(MigrationError, match="MIGRATION_SCHEMA_INCOMPLETE"):
        MigrationManager(database, "0.1.0").migrate()

    invalid = tmp_path / "invalid.db"
    with closing(sqlite3.connect(invalid)) as connection:
        connection.execute("CREATE TABLE app_schema (singleton INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO app_schema VALUES (1)")
        connection.commit()
    with pytest.raises(MigrationError, match="MIGRATION_METADATA_INVALID"):
        MigrationManager(invalid, "0.1.0").migrate()


def test_multiple_or_invalid_metadata_rows_are_rejected(tmp_path: Path) -> None:
    for name, inserts in (("empty.db", ()), ("multiple.db", ((1,), (2,)))):
        database = tmp_path / name
        with closing(sqlite3.connect(database)) as connection:
            connection.execute(
                """CREATE TABLE app_schema (
                singleton INTEGER, schema_version INTEGER, config_version INTEGER,
                state TEXT, schema_digest TEXT)"""
            )
            connection.executemany(
                "INSERT INTO app_schema VALUES (?, 1, 1, 'READY', '')",
                inserts,
            )
            connection.commit()
        with pytest.raises(MigrationError, match="MIGRATION_METADATA_INVALID"):
            MigrationManager(database, "0.1.0").migrate()

    invalid_state = tmp_path / "state-invalid.db"
    with closing(sqlite3.connect(invalid_state)) as connection:
        connection.execute(
            """CREATE TABLE app_schema (
            singleton INTEGER, schema_version INTEGER, config_version INTEGER,
            state TEXT, schema_digest TEXT)"""
        )
        connection.execute("INSERT INTO app_schema VALUES (1, 1, 1, 'UNKNOWN', '')")
        connection.commit()
    with pytest.raises(MigrationError, match="MIGRATION_METADATA_INVALID"):
        MigrationManager(invalid_state, "0.1.0").migrate()


def test_foreign_key_corruption_blocks_before_backup(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
        connection.execute("CREATE TABLE child (parent_id INTEGER REFERENCES parent(id))")
        connection.execute("INSERT INTO child VALUES (99)")
        connection.commit()

    with pytest.raises(MigrationError, match="DATABASE_FOREIGN_KEY_CHECK_FAILED"):
        MigrationManager(database, "0.1.0").migrate()

    assert not (tmp_path / "migration-backups").exists()


def test_schema_initializer_callback_has_expected_signature() -> None:
    callback: Callable[[Connection], None] = MigrationStep(0, 1, lambda _connection: None).apply
    assert callable(callback)
