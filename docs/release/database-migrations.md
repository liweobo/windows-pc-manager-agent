# Production database migration and recovery

## Scope and versions

All durable repositories share %LOCALAPPDATA%\WindowsPCManagerAgent\state.db. Stage 7A adds a single
app_schema row and SQLite user_version. The current database schema is version 1; the persisted-config
contract is version 1. There is currently no separate configuration file: provider credentials remain outside
SQLite and are not copied into migration metadata.

The complete table catalog is created by one versioned migration before Audit, confirmations, Memory, task state,
or any domain repository is opened. Repository-level create_all calls remain idempotent compatibility checks;
they are not an upgrade strategy. A new durable table or column requires an adjacent migration step and a schema
version increase.

## Startup sequence

1. Reject a reparse-point data directory/database and an existing migration/recovery marker.
2. Run SQLite quick_check and foreign_key_check on an existing database.
3. Read the single migration state and reject future versions, invalid metadata, drift, or partial state.
4. Before upgrading a legacy database, create an online SQLite backup under migration-backups, then verify its
   integrity and SHA-256.
5. Persist IN_PROGRESS, run each adjacent DDL step under an explicit BEGIN IMMEDIATE transaction, update
   user_version, and store the exact schema digest.
6. Re-run integrity, foreign-key, exact-table, schema-digest, and version checks before repositories start.

No migration drops all tables, recreates user state, silently repairs drift, or restores an old confirmation.
Downgrade is not supported.

## Failure and interruption

Any failed step rolls back its SQLite transaction, marks the database RECOVERY_REQUIRED, writes the content-free
state.db.migration-recovery-required sidecar, and retains the verified pre-migration backup. A process crash may
also leave state.db.migration.lock; the next start fails closed instead of guessing whether the previous migration
completed. Audit cannot record this startup failure because the database is not yet trusted; the marker is the
durable recovery evidence.

Do not delete the live database or markers merely to make the app start. Preserve the whole application data
directory and seek a reviewed recovery procedure. MigrationBackupService.restore_to_new_path() verifies the
backup and copies it only to an absent path; it never overwrites the live database. Replacing a live database is a
manual support action after the failed database has been retained separately.

## Retention and uninstall

Migration backups, Audit, Memory, confirmation evidence, rollback/recovery records, and task state are retained
indefinitely in Stage 7A. No automatic cleanup runs. Normal application uninstall preserves this data. A future
explicit “remove local application data” installer option must list these categories and must never include user
documents, scan targets, exported reports, Office outputs, or browser downloads.

## Test coverage

Automated tests cover fresh creation, repeated startup, three legacy schema shapes, verified backup, backup copy,
corrupt SQLite, foreign-key corruption, missing migration, explicit migration failure/rollback, interrupted state,
stale lock, future schema/config versions, schema drift, reparse paths, and non-overwrite restore. All fixtures are
temporary synthetic databases; no real user database is migrated by CI.
