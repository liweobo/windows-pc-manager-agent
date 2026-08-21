# Security model

## Stage 4D2B Vendor uninstaller controls

- Exactly one Vendor write tool exists: `software.uninstall.vendor`, R2 manifest, batch size 1,
  rollback `NONE`. MSI and Vendor repositories mutually exclude all active uninstall transactions.
- Raw `UninstallString` and `QuietUninstallString` are untrusted local data. Only the interactive
  value may be parsed; neither raw value enters a command runner, model payload, Preview, audit or
  durable transaction. Quiet execution is unsupported.
- The parser uses `CommandLineToArgvW`, preserves exact tokens and rejects malformed/oversized
  metadata. Executable resolution accepts only a literal absolute drive path and never searches
  PATH, expands environment/home variables or accepts UNC/device/traversal forms.
- Only direct `.exe` files may continue. CMD, PowerShell/pwsh, WScript/CScript, MSHTA, Rundll32,
  Regsvr32, scripts, reparse paths, non-fixed volumes and temp/download/cache locations are blocked.
- Trust is cumulative: stable Windows file identity and metadata, bounded SHA-256, exact known
  install-location containment, valid offline Authenticode and conservative Publisher/signer match
  must all pass. A signed file or Program Files location alone never grants trust.
- The finite argument policy allows no arguments or one recognized interactive uninstall verb. It
  rejects quiet/restart/data/path/response/nested/script/shell-like/unknown forms without deleting or
  rewriting tokens. The LLM, UI and user have no argument field.
- Stage 4D1 software safety remains prior to mechanism trust. Current-user ordinary applications are
  R2; developer tools/runtimes are R2_HIGH_IMPACT. Shared runtimes, databases/background platforms,
  drivers/hardware, Windows/security/network/Agent/enterprise/package/unknown classes and
  machine-wide scope are blocked.
- Plan and runtime confirmations bind all software, capability, source, executable, file/hash,
  signature, publisher, argument, policy, preflight and risk digests. Both are durable, expiring,
  parent-linked, single-use and consumed atomically.
- Preflight is read-only. Related processes produce visible warnings; running related services or
  incomplete probes block. No Stage 4A/4C executor is injected, so uninstall confirmation cannot
  terminate a process or stop a service.
- The Windows adapter uses exact `[absolute_executable, *validated_arguments]`, explicit executable
  and safe cwd, DEVNULL streams, close-on-exec, a small allow-listed environment and `shell=False`.
  It has no runas/ShellExecute/PowerShell/CMD/elevation fallback and never retries or reboots.
- Stopping monitoring or reaching the long-running threshold never terminates the direct process or
  descendants. The transaction remains active; restart marks it `INTERRUPTED` and expires approval.
- Process exit is not uninstall success. Fresh normalized inventory decides verification; partial or
  contradictory evidence remains unverified. Residual analysis performs one `lstat`, never
  enumerates, follows or deletes the path.
- Mandatory transaction or pre-start audit failure aborts before launch. Ordinary audit stores only
  digests/categories/counts and never raw commands, arguments, executable paths, environment values
  or document content.

Rollback is `NONE`. Reinstall is manual recovery guidance and cannot promise to restore settings,
licenses, plugins, local databases or user data.

## Stage 4D2A MSI uninstall controls

- Exactly one write tool exists: `software.uninstall.msi`, R2, batch size 1, rollback `NONE`.
- Execution requires MSI/high confidence, an exact braced ProductCode from local inventory, exactly
  one installed `USER_UNMANAGED` Windows Installer registration, current-user scope, and matching
  name/version/publisher/source-qualified identity evidence.
- Machine/user-managed MSI is outside the ordinary-user boundary. The app refuses to run this stage
  when its own process is elevated and never requests UAC or a `runas` retry.
- `USER_APPLICATION` is R2. Selected developer tool/runtime classes are R2_HIGH_IMPACT. Shared
  runtimes, databases/background platforms, drivers/hardware utilities, Windows components/features,
  security/VPN/network, Agent, enterprise, package-manager and unknown classes are blocked.
- A known installation location and complete process/service probes are mandatory. Strong path-related
  running processes or services block; their separate Stage 4A/4C confirmations cannot be reused.
- Plan and runtime confirmations bind transaction/operation/plan/Preview, all evidence digests, risk,
  object summary and expiry. They are durable, single-use and consumed atomically.
- The adapter resolves the system `msiexec.exe` strictly and supplies only `/x`, validated ProductCode,
  `/norestart` as an argument list with `shell=False` and fixed working directory/stdio policy.
- Exit code is evidence, not success. Verification refreshes normalized uninstall-registry inventory
  and Windows Installer registration. Contradictions become explicit `COMPLETED_UNVERIFIED`,
  `REMOVED_WITH_UNEXPECTED_INSTALLER_RESULT` or replacement states.
- There is no automatic reboot, installer termination, retry, residual cleanup, registry cleanup,
  program/user-data deletion, Vendor/winget/MSIX/PowerShell/CMD/WMI fallback or arbitrary process API.
- Mandatory durable transaction and pre-start audit failure abort before launch. Post-launch audit
  failure is shown as a warning and never causes redispatch.

Recovery is truthful: uninstall is `NONE`, usually requiring manual reinstall. Reinstall is not Undo
and cannot promise to restore state. Long-running installers remain `WAITING`; restart changes them to
`INTERRUPTED`, and the user must inspect fresh state rather than letting the Agent resume automatically.

## Stage 4D1 software uninstall-analysis controls

Stage 4D1 is strictly R0 and has no rollback because it performs no change. Plan confirmation allows
only the fixed read-only chain. Target acknowledgement is bound to plan ID/digest,
identity/metadata/capability/Preview digests and expiry, but deliberately produces no
`ExecutionAuthorization`. The workflow always returns a stop reason.

Installed-software names, publishers, paths and uninstall metadata are untrusted. Raw command lines
are never displayed verbatim, logged or sent to a provider. The parser cannot execute and rejects
shell/script wrappers, UNC or relative executables, non-EXE files, missing targets and oversized or
malformed metadata. Package and MSIX sources require exact structured identifiers; absence is
reported as unsupported rather than guessed.

Protected and unknown classes fail closed. Windows features/components, drivers, Agent components,
Microsoft/system/security software and insufficient identities are blocked. Package managers,
VPN/network clients, databases, background platforms, hardware utilities and runtimes receive
high-impact Preview classification only. Ordinary user applications and developer tools may receive
Preview, but none becomes executable in this stage.

Fresh inventory is collected at resolve, inspect, capability and Preview boundaries. Disappearance,
non-unique identity, metadata change or capability change invalidates the flow. Audit records only
digests, counts, decisions and zero-execution state; database failure stops the workflow. Static
tests reject process-creation and uninstall primitives in the Stage 4D1 source set.

## Stage 4C2 service startup-type controls

The explicitly approved R2 exception is the exact, single-object transition
`Automatic (non-delayed) <-> Manual`. All gates are cumulative:

- the fresh object must pass the Stage 4C1 current-user, signed, own-process third-party policy;
- stable identity, startup configuration, runtime state, dependency graph and permission evidence
  must be known and digest-bound;
- the service must have no dependencies and no dependents;
- source and target must both be non-delayed Automatic or Manual and must differ;
- the process must not be elevated and the existing service DACL must grant both query and
  `SERVICE_CHANGE_CONFIG` access to the ordinary user;
- an exact DPAPI-encrypted backup must be stored, decrypted and digest-verified before Preview;
- the immutable plan and first Preview must pass an independent deterministic review;
- PLAN and fresh RUNTIME confirmations must both match plan, Preview, identity, source/target,
  runtime state, impact, permissions, backup and expiry, and may be consumed once;
- the platform must revalidate all evidence immediately before calling the single-field adapter;
- post-write configuration must equal the target and runtime state must equal the pre-write state.

Delayed Automatic, Disabled, Boot/System, driver, shared/system/protected/unknown services and every
service with a dependency relationship remain read-only or blocked. Stage 4C2 has no API for account,
password, binary path, delayed flag, dependencies, recovery actions, security descriptor, service
start/stop, deletion or bulk change. It never calls `ChangeServiceConfig2`, shell, PowerShell, WMI or
`sc.exe`, and it never requests elevation or changes a DACL.

The backup vault and transaction journal are separate from audit. Audit records identifiers, hashes,
policy/confirmation outcomes and verified before/after enum values, but not command lines, binary paths,
account secrets, passwords or ciphertext. If backup, journal or mandatory audit evidence is unavailable,
the write is denied. If a write may already have been dispatched but journaling later fails, the result
is reported as uncertain and never auto-retried.

## Stage 4C1 service controls

- Service actions are default-deny. Eligible objects must be Win32 own-process services,
  run as the current Windows user, have an existing executable outside Windows/Agent roots,
  pass Authenticode validation, and avoid all protected-name/path/publisher/description rules.
- Drivers, shared/interactive services, Microsoft/Windows components, system accounts,
  security/network/login/storage/update/enterprise/Agent services, pending states and unknown
  identity are blocked. Running dependents block Stop/Restart; stopped dependencies block
  Start/Restart. The Agent never changes a related service automatically.
- An elevated Agent process is itself a blocker. Required access is proven by least-privilege
  handle-open checks before confirmation and checked again before execution. Access denial
  fails closed; the application never asks for UAC or retries with more privilege.
- START and STOP are R2. RESTART is R2_HIGH_IMPACT and has explicit STOP and START steps.
  Both confirmation tiers bind plan, Preview, configuration identity, live state, dependency
  graph, permission evidence, object summary and expiry; reuse or any drift is rejected.
- Only `system.service.start` and `system.service.stop` are registered. A durable transaction
  and mandatory audit start before SCM control. Dispatch is recorded before state polling;
  results distinguish no-op, completed, failed, partially completed, cancelled and interrupted.
- Rollback is MANUAL because opposite state control cannot recover in-memory service sessions.
  Reverse actions need a new Preview and confirmations. Interrupted work is never auto-resumed.
- Audit stores ServiceName and cryptographic digests, not the executable path, command line,
  service password, control payload, token, user document, or binary contents.

## Stage 4B startup controls

- Inventory comes only from fixed HKCU/HKLM Run/RunOnce keys and current/common Startup
  folders. No model/UI text can supply a registry key, value, storage path or command.
- Only a supported current-user HKCU Run value or current-user `.lnk` can reach a write tool.
  Machine-wide, RunOnce, common-folder, unresolved, unknown-publisher, Microsoft/system,
  security, driver, enterprise and Agent entries are read-only or blocked.
- The original registry bytes/type or shortcut bytes and identity are captured first,
  encrypted with current-user DPAPI, stored outside audit and read-back verified. A missing,
  corrupt or undecryptable backup stops the action.
- Disable and restore are R2, one object per transaction, with plan and immediate runtime
  confirmation. Both approvals bind action, plan/Preview, identity, visible state, backup and
  expiry and are consumed once.
- The runtime boundary rereads identity, executable path, publisher evidence,
  StartupApproved evidence and exact backup. Any change, conflict or permission error fails
  closed. The adapter never writes StartupApproved.
- Registry mutation is confined to native-view HKCU Run and uses a Windows transaction.
  Startup-folder mutation is a same-volume no-overwrite move to Agent-owned storage.
- Verification failure triggers the exact inverse command. FULL rollback remains conditional
  on unchanged material and an empty destination; no future-launch behavior is guaranteed.
- No generic registry/path tool, bulk disable, delete, shell, elevation or R3 fallback exists.

## Stage 4A process-action controls

- Only two non-generic tools exist: graceful `WM_CLOSE` (`R2`) and force termination
  (`R2_HIGH_IMPACT`). Both require Preview, PLAN and RUNTIME confirmation, and durable exact
  authorization. Force approval can never be derived from graceful approval.
- Target resolution is local. An LLM cannot author executable PIDs or expand an application
  group. Ambiguous names fail closed; selected/stale rows are freshly inspected.
- Every identity binds PID, process creation time, executable path, owner SID and session.
  The adapter repeats those fields on the same opened process handle immediately before
  mutation. PID reuse becomes `PROCESS_IDENTITY_CHANGED`, not a new target.
- System/critical/protected/security/SCM-service/other-user/other-session/Agent processes are
  blocked. Unreadable or incomplete identity/protection data is not eligible.
- `WM_CLOSE` is sent only to windows currently owned by the confirmed PID. Force uses only a
  checked handle and `TerminateProcess`; there is no `taskkill`, shell, arbitrary command,
  debug privilege, UAC prompt, or administrator retry.
- Limits are 5 applications and 20 member processes. Timeouts are 5–30 seconds. Workers keep
  the GUI responsive. Cancel only stops waiting or future members; already sent actions are
  real and not undone.
- Verification waits on the original opened handle. `ALREADY_EXITED`, `IDENTITY_CHANGED`,
  `ACCESS_DENIED`, `STILL_RUNNING`, `NOT_ATTEMPTED`, `FAILED`, and `UNKNOWN` are distinct.
- Rollback is `NONE`. Audit records the target query, process identity evidence, policy class,
  reason codes, action, both confirmations, state changes and verified result, but never a
  process command line, window text, document content, token or credential.

Process management remains ordinary-user only. The residual risks are unsaved application
data loss, application state corruption after force termination, a kernel/in-process attacker,
and unavoidable races after a final handle-bound check. These risks are displayed before both
confirmations and are never described as recoverable.

## Stage 3 system-query boundary

Every Stage 3 tool is `R0`, read-only, `RollbackLevel.NONE`, plan-confirmed, and registered
with a fixed Pydantic input/output schema. `NONE` is truthful because no system state is
changed. Execution re-runs safety review and requires the canonical plan digest approved by
the user. A changed sample count, interval, collector set, item limit, plan ID, or body
invalidates approval.

The adapter opens only query handles and registry keys. Process records deliberately lack a
command-line field. Software records deliberately lack uninstall strings. The service reader
extracts an executable path while discarding arguments. Startup values remain local to the
dashboard and are never included in audit or model explanations. Audit stores collector name,
status, counts, warning count, duration, and sanitized exception type—not raw inventories.

Findings are conservative observations. CPU uses multiple samples and every report includes
the actual thresholds. High utilization does not prove fault, malware, or causation. Suggested
actions are schema-enforced as non-executable. Missing access becomes partial/failed; the app
never asks for elevation. Audit failure stops execution before collectors.

## Stage 2B controls

- `file.trash` is R2, non-idempotent, Preview-required, double-confirmed, and MANUAL.
- Only current explicit GUI selections become paths; the LLM cannot name or choose targets.
- PLAN approval binds plan/Preview/object-set digests, counts, bytes, and expiry.
- RUNTIME approval is issued only after a fresh snapshot, has a shorter expiry, and is
  consumed once. SQLite must contain approved PLAN and consumed RUNTIME evidence.
- Before every Shell call, source authorization, handle identity, metadata, and complete
  directory snapshot are rechecked. Any change stops execution.
- Windows, Program Files, ProgramData, AppData, other users, credentials, `$Recycle.Bin`,
  authorized roots, reparse points, SYSTEM, and OFFLINE objects are blocked.
- The first implementation permits only writable fixed NTFS on the Windows system volume
  with a queryable Recycle Bin. Unknown/removable/network/non-system volumes fail closed.
- Each item receives PREPARED recovery and mandatory audit before execution. Ambiguous
  results make both the item and an otherwise-empty parent transaction UNKNOWN, stop the
  batch, and require manual inspection.
- Permanent deletion, emptying the bin, fallback APIs, automatic restore, and administrator
  elevation remain unavailable.
- Permanent-delete, Recycle Bin bypass, and empty-bin language is refused locally and
  recorded as an R4 audit event without invoking a model or platform tool.

## Trust boundaries

Trusted deterministic code owns path authorization, schemas, risk, confirmation,
execution, verification, audit, and rollback claims. Model output, UI text, file names,
file contents, documents, and web pages are untrusted data. They never become commands.

## Stage 2A risk policy

| Level | Meaning | Stage 2A behavior |
|---|---|---|
| R0 | Read-only | Scanner and three analyzers, inside a reviewed and confirmed plan |
| R1 | Low-risk reversible app/user data | Configuration/export plus Previewed same-volume move, rename, mkdir and rollback |
| R2 | Destructive or external disclosure | No file operation; external model transfer needs exact immediate consent |
| R3 | High-risk system change | No executable tool is registered |
| R4 | Prohibited | Always denied |

The registered read tools are `file.scan`, `file.analyze.large`,
`file.analyze.inactive`, and `file.analyze.duplicates`. All manifests are R0,
read-only, cancellable, bounded, and rollback `NONE`.

The registered write tools are `file.mkdir`, `file.move`, `file.rename`, and the
rollback-only `file.rollback.rmdir-empty`. They are R1, non-overwriting, Preview-enabled,
ordinary-user tools with truthful FULL rollback preconditions. The rollback-only tool
cannot remove an arbitrary directory: its operation/identity/argument digest must match
an Undo record of a directory created by the same transaction, and the directory must be
unchanged and empty immediately before the checked Win32 call.

## Authorization and path controls

- A user adds each local root explicitly; authorizing a child never authorizes a parent.
- The model receives opaque UUIDs and cannot introduce path text or broaden scope.
- Relative paths, `..`, UNC/device syntax, mapped remote drives, trailing-dot/space
  ambiguity, missing roots, and non-directories are rejected.
- Paths are canonicalized and compared by path components, never string prefixes.
- Credential/browser/session/password-manager/SSH/wallet/Personal Vault/Windows
  security roots, other profiles, and user forbidden roots are denied.
- Existing path components and each enumerated entry are checked for symlinks,
  junctions, and other reparse points before traversal. Redirected paths are skipped.
- Each directory identity is captured and checked before enumeration. Files opened for
  hashing are revalidated against approved scope, size, modification time, and available
  file/device identifiers before and after reading.
- Offline placeholders are not hydrated for hashing. A changed or unreadable candidate
  becomes a structured issue; it does not make a duplicate group.

## Plan and confirmation controls

The compiler derives executable arguments from a validated intent. The safety validator
checks registry membership, schemas, R0/read-only manifests, exact authorized roots,
excluded paths, session IDs, thresholds, selected analysis tools, ALL/ANY mode, zero
modifications, and zero deletions.

Plan confirmation includes the canonical SHA-256 digest and expiry. Execution repeats
review and calls `require_plan_approved`; any plan/UI change invalidates approval.

For Stage 2A, a second confirmation type binds both the immutable operation plan and the
live Preview, including file identities, final paths, operation/ready counts, transaction
ID and expiry. It is consumed once. A durable transaction guard additionally matches the
registered tool and exact Pydantic-validated argument digest. Neither UI state nor model
output alone can authorize a write. Rollback uses an independent confirmation bound to a
new live reverse Preview.

Stage 2A path controls add reserved device-name/invalid-character/name-length checks,
same-parent enforcement for rename, same-volume checks for move, target-absence checks,
and execution-time revalidation. Stable Windows identity combines volume serial and File
ID; metadata must also remain unchanged. Any missing identity, permission change, target
appearance, reparse component, source change, or volume change stops that item and all
later writes. No silent auto-rename or overwrite policy exists.

External model confirmation is separate. It binds purpose, provider, exact JSON payload
digest, object summary, and expiry. Planning sends a goal, labels, opaque IDs, allowed
analyses, and tool names. Explanation sends aggregate counts, byte totals, categories,
thresholds, and analysis types. Neither sends paths, names, content, nor raw candidate rows.

## Resource and failure controls

- Scanner output streams in configurable batches; SQLite and exports use bounded pages.
- Hard file-count and timeout limits constrain every root; cancellation is cooperative.
- Recoverable per-object errors are counted and scanning continues without widening scope.
- Cancellation, timeout, and truncation are truthful terminal states, not “completed”.
- Tool output types and final report IDs/counts are verified.
- A tool failure records the exact tool and error, aborts the plan, and records task failure.
- An unavailable or corrupt audit database prevents trusted execution.
- CSV/JSON export uses an absolute local target and exclusive create. Existing files are
  never overwritten; an incomplete newly created report is left for manual inspection
  because this stage contains no deletion capability.

## Credentials, audit, and rollback

API keys come only from environment-backed settings and Pydantic `SecretStr`; they are
never written to configuration or logs. Recursive key and inline-value redaction runs
before every audit insert. CI performs pinned dependency installation, Bandit,
`pip-audit`, and Gitleaks checks.

User-file analysis changes nothing, so rollback is `NONE` (nothing to undo). Authorized
path configuration records a FULL inverse action. Export is MANUAL: the application does
not claim it can automatically remove the report. No administrator privilege is used.

Successful Stage 2A writes have a PREPARED Undo record before mutation and an AVAILABLE
record only after postcondition verification. Undo and audit are separate tables and
purposes. Reverse execution refuses changed results, occupied original paths, unsafe
scope, and non-empty created directories. `FULL` describes the normal verified case,
not a promise that later user changes cannot create a rollback conflict.
