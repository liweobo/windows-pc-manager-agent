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

## Stage 2A file-operation rollback

| Operation | Risk | Normal rollback | Valid only while |
|---|---|---|---|
| Create ordinary directory | R1 | FULL | Same directory identity remains and is empty after earlier reverse steps |
| Same-volume file/directory move | R1 | FULL | Result identity/metadata unchanged and original path absent |
| Same-parent structured rename | R1 | FULL | Renamed identity/metadata unchanged and original name absent |
| Conflict/blocked Preview item | R1 | NONE needed | It was persisted as SKIPPED and never executed |

`UndoRecord` is written as PREPARED before the Win32 mutation and promoted to AVAILABLE
only after postcondition verification. It stores operation/transaction IDs, sequence,
operation type, original/result paths, before/after stable identity and metadata, rollback
level, validity conditions, checksum, timestamps, and result state. Audit answers “what
happened”; Undo answers “how this verified operation may be restored”. They are separate.

Rollback is not a blind reverse move. `RollbackManager` reads Undo in descending sequence,
rechecks authorization/reparse points/current identity/current metadata/original-path
availability, accounts for managed children that earlier reverse steps will vacate, and
produces a new immutable Preview. The user must confirm that exact digest. Execution then
reserves reverse arguments in SQLite and invokes only registered tools through the same
write guard. Any unexpected error stops later reverse operations.

FULL is conditional. If the user edits the result, creates a new object at the original
path, revokes authorization, changes permissions, or adds unmanaged content to a created
directory, the Preview reports CONFLICT/BLOCKED and does not overwrite or remove it.

On restart, RUNNING/ROLLING_BACK becomes INTERRUPTED and is never continued automatically.
PREVIEWED/AWAITING_CONFIRMATION/CONFIRMED becomes CANCELLED because the one-time in-memory
confirmation is intentionally unavailable. The history page can inspect interrupted
records and generate a rollback Preview from whatever valid Undo records exist.

## Stage 2B Recycle Bin recovery

| Operation | Risk | Recovery | Valid only while |
|---|---|---|---|
| Move explicitly selected file/directory to Windows Recycle Bin | R2 | MANUAL | Windows Recycle Bin still contains the item |
| Blocked or cancelled object | R2 | NONE needed | No Shell operation occurred |
| Interrupted or ambiguous Shell result | R2 | MANUAL inspection | User checks original path and Windows Recycle Bin |

`TrashRecoveryRecord` is written as PREPARED before `IFileOperation`. It stores the
transaction/item IDs, original path, handle-based before identity, time, checksum,
Shell callback evidence when available, verification status, and explicit manual
instructions. VERIFIED_RECYCLED becomes AVAILABLE/MANUAL. An absent callback object,
process interruption, or otherwise incomplete evidence becomes UNKNOWN and stops later
items; the application never retries automatically.

Stage 2B does not feed these records to `RollbackManager` and does not display a FULL Undo
button. Recovery means opening Windows Recycle Bin, locating the object by original name
and deletion time, and choosing Restore. If the bin was emptied or Windows cannot restore
the original location, application recovery is unavailable. Reverting Git commits never
restores recycled user files.

## Write-command contract

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

After Stage 2A is committed, use a new branch and revert its commit(s) newest first:

```powershell
git switch -c fix/revert-stage-2a
git revert <stage-2a-sha>
git push -u origin fix/revert-stage-2a
```

Review and test the revert before merging. Do not force-push or use `git reset --hard` as
the normal recovery procedure. Reverting code does not move user files back and does not
automatically remove additive SQLite tables. First use the running Stage 2A rollback Preview
for any desired user-file restoration, verify it, then revert code. Keep/back up local state
according to user intent.
