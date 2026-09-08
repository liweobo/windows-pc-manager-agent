# Uninstall and local-data retention

The normal uninstaller removes the Program Files application and Broker binaries plus shortcuts. It
does not silently delete `%LOCALAPPDATA%` state. Preferences, Audit, Memory, task history, logs,
migration backups, confirmation/rollback/recovery records and crash evidence are therefore retained
for reinstall or manual review.

Stage 7A does not offer an installer switch to remove local data. This is intentionally deferred
until a separate UI can enumerate every category and obtain explicit destructive confirmation.
Uninstall never deletes user-managed scan targets, documents, Office outputs, exported reports,
Browser downloads or any file outside the Agent-owned data directory.

To remove retained local data later, first export or back up anything needed, verify the application
is uninstalled and no Agent process is running, then use a future reviewed data-reset workflow. Do
not use a broad recursive command copied from documentation. Current private-RC users should retain
the directory and ask for a scoped support procedure.
