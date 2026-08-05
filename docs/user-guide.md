# User guide

## Safe first run

1. Install dependencies with `uv sync --all-groups`.
2. Run `uv run pc-manager-agent` as a normal user.
3. Open **只读扫描**, choose one directory, and generate a plan.
4. Read the included/excluded paths, R0 risk, zero modification count, limit,
   timeout, and rollback statement.
5. Confirm only if the displayed root is correct, then start the scan.

The table shows metadata only. `accessed_at` is not proof that a file was used or
unused; Windows/filesystem settings can make it unreliable. Stage 0 makes no
cleanup recommendation.

## Cancel and exit

Use **取消扫描** to request cooperative cancellation. Closing the window hides it
to the tray when available. Choose **安全退出** to cancel active work, wait up to
five seconds, remove the tray icon, and close the app.

## Audit records

Open **审计** and refresh to view plan, confirmation, start, completion, or
failure events. The database is under the local application-data directory shown
on **设置**. Do not manually edit it while the app is running.

## Common errors

- “outside approved scope”: regenerate a plan after selecting the intended root.
- “reparse point”: choose the real directory rather than a shortcut or junction.
- audit initialization failure: verify the shown local data directory is writable
  and that security software is not locking the database.
- OpenAI not configured: leave the provider disabled or set all required
  environment variables; stage 0 scanning does not need a model.
