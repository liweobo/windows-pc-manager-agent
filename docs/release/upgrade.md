# Upgrade and downgrade policy

## Supported upgrade path

Upgrade is an explicit installer run from an older test/RC version to a newer trusted RC. The same
Program Files location is reused. Safe reinstall is the repair strategy; there is no separate repair
command and no silent background self-update.

Before application repositories open, the Migration Manager validates SQLite integrity, schema and
version. A legacy database receives a verified online backup before an adjacent transactional
migration. A failure or interrupted marker produces `MIGRATION_RECOVERY_REQUIRED`; the application
does not drop/recreate the database or restore confirmations.

The upgrade preserves preferences, approved directories, task/audit history, Memory, backups and
recovery records when compatible. Provider credentials remain outside SQLite and are never copied to
migration metadata. The installer replaces application files only; it does not delete user data.

Downgrade is **NOT SUPPORTED**. A previous application must reject a future schema rather than modify
it. Automatic update is **DISABLED** until a signed metadata and binary trust chain is implemented;
a webpage, Browser Agent or model response can never choose an update URL.

If migration fails, preserve the whole LocalAppData directory and the retained migration backup.
Use `MigrationBackupService.restore_to_new_path()` only to make an independently verified recovery
copy at an absent destination. Replacing the live database is a reviewed manual support operation.
