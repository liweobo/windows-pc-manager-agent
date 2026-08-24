# Rollback design

## Stage 4D2C1 winget removal: NONE

`software.uninstall.winget` has `RollbackLevel.NONE`. Windows Package Manager does not provide this
application with a reliable transaction-level inverse that restores the exact prior program version,
settings, license, plug-ins, local databases and user data. Therefore:

- no Undo button or automatic reinstall is offered;
- reinstall guidance is manual recovery, not rollback;
- the Agent never downloads an installer, restores a Source, selects a replacement version or claims
  that reinstall will restore user state;
- stopping monitoring does not undo or cancel a launched installer;
- `INTERRUPTED` means inspect the visible installer and refresh both inventories, not retry;
- residual paths are report-only and are never deleted during recovery.

代码回滚使用 `git revert <Stage-4D2C1-commit>`。它只撤销应用代码，不会恢复已卸载软件；如果
真实卸载已经发生，必须从可信的发布者/官方源人工重新安装，并自行核对数据备份。

## Stage 4D2B Vendor uninstall

`software.uninstall.vendor` has `RollbackLevel.NONE`. A vendor's interactive uninstaller may remove
program files, registrations, integrations, settings or data according to vendor-specific behavior
that the Agent cannot predict or invert. The Agent therefore creates no Undo command, never labels
reinstall as rollback and never attempts to restore deleted software state automatically.

Before confirmation, cancellation needs no rollback because no process has started. After launch:

1. complete or cancel choices in the vendor's own visible UI yourself;
2. “停止监控” only detaches Agent observation—it does not cancel or terminate the uninstaller;
3. for `MONITORING`/`INTERRUPTED`, check whether the vendor window/process is still active, then run a
   fresh software inventory; never replay the old confirmation or automatically start it again;
4. compare the independent process category and fresh verification state; exit code 0 alone is not
   proof of removal;
5. if the exact target remains, review the vendor UI/result before creating any new plan;
6. if `VERIFIED_REMOVED`, reinstall only from a trusted source if you actually want the app back;
7. treat every known installation location as report-only; never manually delete an unexplained
   directory without first determining whether it contains shared or user data.

Reinstall may not restore preferences, licenses, plug-ins, databases, profiles or user files. The
code update itself is recoverable with `git revert <stage-4d2b-commit>`; this reverts application code
but cannot reinstall software already removed by a previous run.

## Stage 4D2A MSI uninstall

`software.uninstall.msi` has `RollbackLevel.NONE`. Windows Installer removal may delete program
state, invoke package-defined custom actions and alter registrations in ways the Agent cannot invert.
The application therefore does not create an Undo button, does not call MSI repair/reinstall as a
rollback, and does not claim that saved settings or user state can be restored.

Recovery guidance is manual:

1. read the installer category and the separate post-uninstall verification state;
2. if `VERIFIED_REMOVED`, reinstall from a trusted original source only if the user wants the app;
3. if `COMPLETED_UNVERIFIED`, refresh installed-software state before taking another action;
4. if `REBOOT_REQUIRED`, the user chooses when to reboot—the Agent never does it;
5. if `WAITING`/`INTERRUPTED`, inspect the visible Windows Installer and fresh inventory; never retry
   the old transaction or confirmation;
6. treat any known install directory as a report-only residual; Stage 4D2A never deletes it.

Reinstall is not Undo and may not restore preferences, licenses, plugins, databases or user files.
The code update itself remains recoverable with `git revert <stage-4d2a-commit>`.

## Stage 4D1 software analysis

All five Stage 4D1 tools are R0 and declare `RollbackLevel.NONE` because they do not change software,
files, services, processes, registry values or package state. `NONE` here means “nothing to undo”,
not “an irreversible uninstall occurred”. Target acknowledgement records understanding only and
cannot be converted into execution authority.

If inventory or Preview data is wrong or stale, close it and create a fresh plan. Identity,
metadata, capability or plan digest changes invalidate the old confirmation/Preview. Code rollback
uses `git revert <stage-4d1-commit>`; no software repair is needed because this stage never invokes
an uninstaller.

## Stage 4C2 startup configuration

| Operation | Risk | Rollback | Exact meaning |
|---|---|---|---|
| Set non-delayed Automatic | R2 | FULL, conditional | A verified original Manual backup can be restored only while identity and the Agent-written Automatic value still match |
| Set Manual | R2 | FULL, conditional | A verified original non-delayed Automatic backup can be restored under the same conflict-free conditions |
| Restore an Agent-owned change | R2 | FULL, conditional | Restore itself creates a new verified backup/change record, enabling a later reverse restore if its conditions still hold |
| Blocked/cancelled before dispatch | R2 | No rollback needed | No `ChangeServiceConfig` call was sent |
| Verification/journal uncertainty after dispatch | R2 | Manual inspection first | Never auto-write or retry when the current value cannot be proved |

`FULL` covers only the single startup-configuration field inside this narrow boundary. It does not
restore runtime state, service memory, an application session, dependency behavior, a future boot result
or changes made by another administrator. The UI and audit use “conditional FULL” to preserve this
distinction.

Before every Preview, `ServiceStartupBackupVault` serializes stable identity, display name, exact source
configuration, runtime state and capture time; encrypts it with current-user DPAPI; persists it separately;
then decrypts and verifies the payload digest. No Preview or write is available if this proof fails.

Restore never runs automatically. `prepare_restore` loads an Agent-owned change, verifies its backup,
re-reads the exact ServiceName, and requires stable identity plus the current configuration to equal the
previous Agent-written target. It then creates a fresh backup of the current value and follows the same
Preview, PLAN confirmation, runtime revalidation, RUNTIME confirmation, write and read-back sequence.
Any mismatch yields `RESTORE_CONFLICT` without overwrite. An interrupted transaction is retained for
audit and manual inspection and is never resumed on application restart.

## Stage 4C1 service state actions

| Operation/outcome | Risk | Rollback | Truthful recovery statement |
|---|---|---|---|
| START reaches RUNNING | R2 | MANUAL | A later STOP is a new action, not Undo |
| STOP reaches STOPPED | R2 | MANUAL | A later START cannot restore in-memory sessions |
| RESTART reaches RUNNING | R2_HIGH_IMPACT | MANUAL | Restarted internal state is not recoverable |
| STOP succeeds, START fails/cancels/times out | R2_HIGH_IMPACT | MANUAL, partial | Service may remain STOPPED or pending; inspect current state and create a new plan |
| Already in requested state | R2 | NONE needed | No SCM control was dispatched; result is a verified no-op |
| Blocked/cancelled before dispatch | R2/R2_HIGH_IMPACT | NONE needed | No service control was sent |
| Interrupted/unknown result | R2/R2_HIGH_IMPACT | Manual inspection | Refresh inventory; never auto-retry or auto-resume |

Service actions intentionally do not produce a `FULL` undo record. The repository instead
stores immutable identity/state/dependency/permission digests, ordered step/argument digests,
dispatch boundaries, both confirmation bindings, final state and verification evidence.
Requesting the opposite state is always a new transaction with a new Preview and two new
confirmations. In particular, Restart after a partial result is never scheduled automatically.

## Stage 4B startup actions

| Operation | Risk | Rollback | Valid only while |
|---|---|---|---|
| Disable supported HKCU Run entry | R2 | FULL | Exact backup decrypts; value remains absent; approval evidence is unchanged |
| Disable supported current-user `.lnk` | R2 | FULL | Exact moved link remains in Agent storage and original path is empty |
| Restore Agent-disabled entry | R2 | FULL | Restored identity matches and exact inverse disable remains conflict-free |
| Blocked/cancelled before write | R2 | NONE needed | No platform mutation occurred |
| Interrupted/rollback failure | R2 | Manual inspection | Inspect active source, Agent disabled index and encrypted backup |

FULL is a checked conditional capability, not a promise to override new state. The exact
backup is created and verified before confirmation. Audit and backup are separate: audit
contains identifiers/digests and decisions, while the DPAPI vault contains restore bytes.
HKCU Run restore writes the original type and bytes only when the fixed original value is
absent. Startup-folder restore moves the exact same link back only when the original path is
empty; it never replaces a file. A failed postcondition causes an immediate inverse attempt,
with `ROLLED_BACK` or `ROLLBACK_FAILED` recorded truthfully. Restart never auto-resumes.

## Stage 4A process lifecycle actions

| Operation | Risk | Rollback | Truthful recovery statement |
|---|---|---|---|
| Request application `WM_CLOSE` | R2 | NONE | A closed process and unsaved data cannot be restored by the Agent |
| Force `TerminateProcess` | R2_HIGH_IMPACT | NONE | Restarting the executable is not Undo and cannot recover unsaved/in-memory state |
| Blocked/cancelled before action | R2/R2_HIGH_IMPACT | NONE needed | No process mutation was sent |
| Interrupted/unknown result | R2/R2_HIGH_IMPACT | Manual inspection | Check whether the original identity remains; never auto-retry |

Process actions deliberately do not implement `UndoRecord` or feed `RollbackManager`.
Transactions instead retain the original identity digest, exact action, both confirmation
bindings, result state and verification evidence. Cancellation after `WM_CLOSE` means only
“stop waiting”; cancellation in a force batch means “do not begin later members”. It never
claims to reverse a request already sent to Windows.

After a graceful timeout or an unsupported no-window result, force termination is not a
rollback. It is a new higher-impact transaction with a new Preview, fresh current identities,
new plan confirmation and new immediate confirmation. If the application already exited,
the new action is not created. A restart changes in-flight records to `INTERRUPTED` and does
not resume them.

## Stage 3

System diagnostics are R0 query-only operations, so rollback is `NONE`: there is no changed
system state to restore. Cancellation stops future sampling/collectors cooperatively and
retains completed read-only outcomes. Audit evidence is not a system mutation rollback target.
Any future action suggested by a report needs its own risk and confirmation workflow.

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

After Stage 4A is committed, use a new branch and revert its commit(s) newest first:

```powershell
git switch -c fix/revert-stage-4a
git revert <stage-4a-sha>
git push -u origin fix/revert-stage-4a
```

Review and test the revert before merging. Do not force-push or use `git reset --hard` as
the normal recovery procedure. Reverting code does not restart terminated applications,
recover unsaved data, move user files back, or automatically remove additive SQLite tables.
Inspect transaction history and verify any desired file rollback first, then test the code
revert before merging.
