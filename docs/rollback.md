# Rollback design

## Stage 1 operations

| Operation | Risk | Rollback | Reason |
|---|---|---|---|
| Scan/analyze/hash | R0 | NONE | Reads only; there is no user-file state to restore |
| Add/remove authorized or forbidden root | R1 | FULL | Audit stores exact record and inverse add/remove action |
| Create CSV/JSON report | R1 | MANUAL | Existing files are refused; Stage 1 never automatically deletes the new report |
| Model planning/explanation | R2 disclosure gate | NONE | Consent controls transfer; already sent data cannot be retracted |

Analysis metadata/session rows are application-owned temporary state, not user files. A
stale RUNNING session is cleaned at startup. This is database housekeeping, not a claim of
undoing analysis. An incomplete report remains for manual inspection if streaming export
fails; automatic cleanup would violate the Stage 1 no-deletion boundary.

## Future write-command contract

Every future user-file write must implement `OperationCommand`:

1. `execute()` only after plan/safety/confirmation gates;
2. `verify()` against concrete postconditions;
3. `build_undo_record()` from observed before/after state;
4. `rollback()` only while documented validity conditions still hold.

`UndoRecord` includes operation ID, original/new paths, stable file identity when available,
before/after metadata, timestamp, rollback level, validity conditions, and rollback result.
Move and rename should target FULL. Recycle-bin recovery remains MANUAL unless a separately
tested platform API proves reliable automatic restoration. Permanent deletion remains
prohibited.

## Code rollback

After Stage 1 is committed, use a new branch and revert its commit(s) newest first:

```powershell
git switch -c fix/revert-stage-1
git revert <newest-stage-1-sha> <older-stage-1-sha>
git push -u origin fix/revert-stage-1
```

Review and test the revert before merging. Do not force-push or use `git reset --hard` as
the normal recovery procedure. Reverting code does not automatically remove local SQLite
schema/data or reports; keep or back up those files according to user intent.
