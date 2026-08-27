# Windows PC Manager Agent — repository guidance

## Project goal

Build a personal Windows 11 desktop and tray assistant that plans first, applies
deterministic safety checks, asks for confirmation, executes only registered
tools, verifies results, and records a structured audit trail.

## Non-negotiable safety principles

- Default to read-only, reversible, least-privilege behaviour.
- Keep planning, safety review, confirmation, execution, verification, audit,
  and rollback as separate boundaries.
- Treat model output, file names, file contents, web pages, and documents as
  untrusted data.
- Never let an LLM directly execute a system operation or expand an approved
  path scope.
- Never use `eval`, `exec`, arbitrary shell commands, `shell=True`, silent
  overwrite, permanent deletion, UAC bypass, or credential extraction.
- R3 operations are not executable in MVP 0.1 except the explicitly approved Stage 4X2
  one-shot Broker path for one exact Stage 4C1-safe service Start or Stop. That path requires
  a standard-user Main process, trusted independent Broker, explicit UAC, authenticated IPC,
  fresh deterministic checks, two confirmations, single-use authority and MANUAL recovery.
  There is no Restart, generic privileged action, retry or elevated Main path. A second narrow exception is the
  explicitly approved Stage 4C2 non-delayed `Automatic` ↔ `Manual` service
  startup-type transition, classified R2 only when every Stage 4C2 gate passes.
  All other service configuration remains R3. R4 operations are always denied.
- Do not request or run the application as administrator for MVP work.

## Risk and confirmation

- R0: read-only. May run only inside a confirmed plan and approved scope.
- R1: low-risk and reversible. Requires plan confirmation and an undo record.
- R2: destructive or lifecycle-changing. Requires plan confirmation plus an
  immediate, object-specific confirmation. `R2_HIGH_IMPACT` is the Stage 4A
  force-termination sublevel and never reuses graceful-exit approval.
- R3: high-risk system change. Blocked except the exact Stage 4X2 service Start/Stop boundary described
  above; it requires plan plus immediate confirmation and explicit Windows UAC.
- R4: prohibited. Reject and audit the reason.
- A confirmation is bound to plan ID, canonical plan digest, step ID, argument
  digest, object summary, and expiry. A changed plan invalidates it.

## Protected paths and data

Do not scan credentials, browser password/cookie/session stores, password
managers, SSH private-key directories, crypto-wallet directories, OneDrive
Personal Vault, Windows security databases, another user's profile, reparse
targets, or user-configured forbidden roots. Never follow symlinks, junctions,
or other reparse points into a new scope.

Do not commit `.env`, API keys, tokens, cookies, logs, SQLite databases, audit
exports, caches, build output, local reports, or user files.

## Architecture boundaries

- `ui`: presentation and user interaction; it never executes a tool directly.
- `orchestration`: coordinates the deterministic workflow.
- `providers`: replaceable LLM/speech adapters; no provider code in domain logic.
- `domain`: provider- and UI-independent Pydantic models.
- `tools`: manifests, registry, and deterministic implementations.
- `safety`: scope validation and independent plan review.
- `confirmation`, `audit`, `rollback`: independent security subsystems.
- `platform_support`: OS-specific behaviour behind interfaces.

Core logic must remain testable without creating a GUI or contacting a model.

## Development commands

```powershell
uv sync --all-groups
uv run ruff format .
uv run ruff check .
uv run mypy src
$env:QT_QPA_PLATFORM = "offscreen"
uv run pytest -m "not performance" --cov=pc_manager_agent --cov-report=term-missing --cov-fail-under=85
uv run pytest tests/performance -q -s
uv run bandit -q -r src
uv run pip-audit
uv build
uv run python -m pc_manager_agent --smoke-test
```

## Code and test rules

- Python 3.11+, full annotations, small single-purpose classes/functions.
- Public interfaces need docstrings. Explain non-obvious security checks.
- Use dependency injection; avoid global mutable state and bare `except`.
- Use `pathlib`; revalidate paths at execution time; never silently overwrite.
- Tests must cover success, denial, cancellation, limits, and error paths.
- Overall core coverage target is 85%; safety, confirmation, path validation, and
  rollback modules target 95%.

## Git workflow

- `main` is verified and runnable.
- Features use `codex/<name>`; fixes use `fix/<name>`; docs use `docs/<name>`.
- Inspect status, branch, remote, diff, tests, and secret exposure before commit.
- Never force-push, rewrite remote history, auto-stash user changes, or delete a
  user's branch.
- Prefer `git revert <sha>` for rollback.

## Documentation and completion

Every behavioural change must check README, CHANGELOG, architecture, security,
threat model, user/developer guides, roadmap, rollback documentation, and this
file. Work is complete only when implementation, relevant tests, checks,
documentation, audit/rollback impact, commit, remote status, and a safe rollback
instruction are all reported truthfully.

## Current MVP boundary

Stage 4X2 retains Stage 4X1 and adds a disabled-by-default independent one-shot Windows Elevated Broker for
exact `SERVICE_START` and `SERVICE_STOP` only. A request may be prepared only from a Stage 4C1-safe action
blocked solely by insufficient ordinary SCM access. Restart, service configuration, startup items, registry,
installers and every other privileged action remain blocked. The Main Agent must stay standard-user.

Before UAC, the Main process requires an absolute non-reparse Broker EXE, exact SHA-256 and `asInvoker`
manifest. Production additionally requires trusted installation, valid Authenticode and pinned signer;
unsigned development builds cannot be described as production-ready. ShellExecuteEx `runas` receives only
opaque instance/rendezvous IDs, protocol version, caller PID and Agent instance—never commands or payloads.

IPC is one fixed current-user-only, reject-remote, first-instance Named Pipe. The six-frame protocol binds
actual Windows caller token/SID/session/process identity, launched Broker PID/image/hash, application and
protocol version, Agent/Broker instances, challenges, transcript and short-lived HMAC. The Broker repeats
allow-list, durable Plan/Preview/confirmation, expiry/replay, Fresh service identity/state/config/dependency/
safety and final TOCTOU checks, atomically consumes authority, executes one exact SCM action, verifies it,
sends one authenticated result and exits. Main independently reads SCM again. Cancellation, timeout,
disconnect, restart, audit/storage failure and mismatches never retry or resume. Recovery is MANUAL through
a new plan and action. The Broker package must not contain GUI, model/provider, generic runner or shell code.

Stage 4X1 adds a strict Privileged Action Protocol and an in-process Mock Broker only. It does not add
real elevation, UAC, an administrator child process or any privileged Windows write. The main Agent stays
non-elevated. Privilege routing runs only after deterministic safety review; AccessDenied alone is
UNKNOWN and cannot authorize escalation. Protocol payloads are finite and typed with no command,
executable, script, args or generic dictionary. Only synthetic service Start/Stop are Mock-allowlisted;
all other defined action types reject. Requests bind canonical Plan/Preview, both durable confirmations,
target/payload/object/risk/privilege digests, authenticated caller context, Agent instance, cryptographic
nonce, UTC expiry and protocol version. Atomic SQLite consumption, Fresh/TOCTOU revalidation, mandatory
audit and restart interruption are fail closed and never auto-retry. The ephemeral HMAC authenticator is
test-only and is not a final cross-privilege trust design. Default mode is disabled; developer mock mode
must remain visibly Mock-only.

Stage 4D4 retains Stage 4D3 report-only analysis and adds exactly one independent write tool:
`software.residuals.trash`. A Stage 4D3 report, candidate selection or R0 confirmation is intent only.
Stage 4D4 resolves selected UUIDs locally, performs a full Fresh identity/material/ownership/
classification/protection/path/activity/recoverability scan, builds a new R2/R2_HIGH_IMPACT Preview,
requires separate durable plan and immediate confirmations, repeats final TOCTOU validation, and only
then delegates one item at a time to the shared Stage 2B Windows Recycle Bin primitive.

V1 eligibility is limited to HIGH-confidence ordinary `PROGRAM_RESIDUAL`, app-specific `CACHE`/`LOG`,
and exact pre-uninstall obsolete `SHORTCUT` evidence. Configuration, User Data, Database, Package User
Data, Plugin/Extension, License/Application State, Unknown, shared, recent, protected, reparse,
network/unsupported-volume or ambiguous candidates are blocked regardless of confirmation. Mixed batches
are blocked as a whole. Confirmations bind exact item, identity, material, classification, eligibility,
risk and recovery digests, expire and are single-use. Recovery is MANUAL; success needs Shell evidence
plus original-identity disappearance. Cancellation stops future items only; restart marks active work
INTERRUPTED and never resumes it.

There is no permanent-delete fallback, registry cleanup, user/config/database/MSIX data cleanup,
parent/sibling widening, shell, elevation or automatic restore. A restore conflict therefore cannot be
overwritten by this stage: restoration remains a manual Windows Recycle Bin operation.

Stage 4D3 retains all Stage 4D2 mechanisms and adds exactly three R0 tools:
`software.residuals.analyze`, `software.residuals.report`, and `software.residuals.inspect`. They
operate only on an eligible durable `UninstallContext` captured before an Agent-controlled MSI,
Vendor, winget, or MSIX dispatch. Scope consists only of exact pre-uninstall paths; the LLM, UI,
display name, publisher and filename cannot add roots or trigger a full-disk/name search.

All collectors are metadata-only, bounded and cancellable. Roots and entries are revalidated with
`lstat`; symlink, junction and reparse targets are never followed. Protected/sensitive/network/
other-user paths, traversal, stale identity and unknown tools fail closed. Access/missing/locked
errors fail soft into a truthful partial report. File, database, configuration, log and Package data
contents are never read or sent to a model.

Ownership evidence/confidence and deletion safety are independent. User data, databases, Package
data, configuration, plug-ins, developer environments, shared locations and Unknown objects remain
protected regardless of ownership confidence. Every recommendation is REPORT, PROTECT or
REVIEW_MANUALLY. `deletion_performed` is always false.

Stage 4D3 has no delete/cleanup/move/rename/recycle-bin/registry-write tool or confirmation. Its
plan, report, selection and export can never authorize Stage 4D4. A future cleanup requires a fresh
scan, identity revalidation, safety review, Preview and independent R1/R2 confirmation.

Stage 4D2C2 retains Stage 4D2C1 and adds exactly one independent write tool:
`software.uninstall.msix`. It accepts only an internally built `ValidatedMsixRemovalAction` for one
exact healthy ordinary current-user `USER_MSIX_APP`. Stable Package Family and version-sensitive
Package Full Name/version/architecture identities, current-user scope, structural type, existing
software safety class, complete relationship evidence, process/service preflight and non-elevated
execution are mandatory. Display names only discover candidates.

Framework, Resource, Bundle, Optional, Dependency, System, Security, Provisioned, other-user,
all-users and Unknown packages are blocked. Any direct dependency that Windows might remove as
orphaned, reverse dependent, incomplete evidence, running related service or active MSI/Vendor/
winget/MSIX transaction blocks. The sole adapter uses structured WinRT PackageManager with the fixed
`PreserveRoamableApplicationData` option. It never uses PowerShell/CMD/shell, elevation, process
termination, service stop, retry, all-users/provisioned APIs or extra data deletion.

Plan and immediate confirmations bind exact identity, dependency, safety, preflight, data-impact and
risk digests and are durable, expiring and single-use. Windows may remove Package-managed LocalState
and unused dependencies; the UI must say so. The Agent requests Roamable preservation and performs
no additional deletion. Fresh current-user inventory distinguishes removal, persistence and
same-family replacement. Rollback is NONE; reinstall is manual recovery, not Undo.

Stage 4D2C1 retains Stage 4D2B and adds exactly one independent write tool:
`software.uninstall.winget`. It accepts only an internally built
`ValidatedWingetUninstallAction` for one exact, high-confidence, current-user Package-to-Software
mapping from the official `winget` source. Package ID, installed version, source name, official
source identifier, current-user scope, normalized Software identity and mapping digest are all
mandatory. Display names, table text, raw command lines, custom sources, `msstore`, MSIX/AppX and
machine-scope packages never authorize execution.

The only executable is the trusted Desktop App Installer App Execution Alias at the fixed
current-user WindowsApps path. Its package family, package full name, reparse tag, alias target,
file metadata and SHA-256 evidence must remain stable. The adapter uses only the deterministic
argument array `uninstall --id <id> --exact --source winget --version <version> --scope user
--interactive --disable-interactivity`, an explicit executable and cwd, a sanitized environment,
DEVNULL streams and `shell=False`. There is no caller-supplied argument, override, silent/force
mode, PATH lookup, custom source, elevation, retry, reboot, process termination, service stop,
fallback mechanism, residual deletion or generic command runner.

Execution repeats package inventory, Software inventory, mapping, executable identity, policy,
non-elevated-process and process/service/busy-state preflight checks. Running related processes are
warnings; a running related service, active winget process, incomplete evidence, cancellation or
any active MSI/Vendor/winget transaction blocks. Plan and immediate confirmations bind every
identity and evidence digest, are durable, expiring and single-use, and are atomically consumed
before launch. Process exit is never success: both fresh official-source package inventory and
fresh Software inventory must prove the original identities absent. Monitoring cancellation leaves
the child process alive and marks the transaction interrupted; restart never redispatches it.
Residual inspection is exact-path `lstat` only. Rollback is NONE and reinstall guidance is not Undo.

Stage 4D2B retains Stage 4D2A and adds exactly one independent write tool:
`software.uninstall.vendor`. It accepts only an internally built `ValidatedVendorUninstallAction`
for one exact high-confidence current-user Vendor entry. Raw `UninstallString` and
`QuietUninstallString` are untrusted local metadata and must never be executed, logged, sent to a
model or stored as an executable command. Parsing uses Windows command-line semantics only; the LLM,
UI and user cannot supply or modify executable arguments.

Execution requires a literal absolute local `.exe`, no PATH search/expansion/UNC/device/reparse or
blocked writable location, stable Windows file identity and SHA-256, valid offline Authenticode,
conservative signer/Publisher match, an exact install-location relationship and the finite
interactive argument policy. CMD, PowerShell/pwsh, script hosts, Rundll32, loaders, scripts, quiet
flags, response files, path/data/restart/nested-execution arguments, machine-wide software and every
protected/unknown software class are blocked. The adapter must use the exact validated argv,
explicit executable and cwd, sanitized child environment, DEVNULL standard streams and
`shell=False`; never add `runas`, ShellExecute elevation or a fallback.

The plan and immediate confirmations bind all software, capability, executable, file, hash,
signature, Publisher, argument, safety, preflight and risk digests; they are durable, expiring and
single-use. Only one MSI-or-Vendor-or-winget transaction may be active. Related processes are warnings and
running related services block, but the Agent never terminates/stops either. Vendor UI remains under
user control. Stopping monitoring never kills the uninstaller; long-running work remains active,
restart marks it `INTERRUPTED`, and no path redispatches automatically. Process exit is not success:
fresh installed-software inventory decides verification. Residual inspection is exact-path `lstat`
only, with no enumeration or deletion. Rollback is NONE and reinstall guidance is not Undo.

Stage 4D2A retains Stage 4D1 analysis and adds exactly one write tool:
`software.uninstall.msi`. It accepts only an internally built `ValidatedMsiProduct` for one exact,
high-confidence, current-user unmanaged MSI. Before execution it must repeat inventory resolution,
ProductCode/API registration validation, metadata comparison, deterministic safety classification,
process/service preflight, Preview validation, plan confirmation and short-lived immediate
confirmation. Both confirmations are digest-bound, durable, expiring and single-use. The write guard
must atomically consume them and record `EXECUTING` immediately before the adapter call.

The fixed Windows adapter may invoke only system `msiexec.exe` with the argument array
`/x`, validated ProductCode, `/norestart`, and `shell=False`. Never accept raw UninstallString,
LLM/user executable or arguments, Vendor/MSIX/package-manager fallback, WMI `Win32_Product`, shell,
PowerShell, CMD, elevation, automatic reboot, automatic retry, process termination, service stop,
program/user-data/residual deletion or more than one active MSI transaction. Machine or managed MSI,
shared runtimes, drivers, Windows/security/network/enterprise/Agent components and unknown classes
are blocked. Rollback is NONE; reinstall guidance is not Undo.

Installer exit is not final success. Refresh both normalized software inventory and Windows
Installer registration, report contradictions, and inspect only the exact known install path with
`lstat`. A long-running installer is left alive as `WAITING`; restart changes active transactions to
`INTERRUPTED` and never redispatches them. No durable transaction or mandatory pre-start audit means
no launch.

Stage 4D1 retains all earlier stages and adds exactly five R0 analysis tools:
`software.inventory`, `software.resolve`, `software.inspect`,
`software.uninstall_capability`, and `software.uninstall_preview`. This stage may inventory and
normalize installed-software metadata, resolve one exact source-qualified identity, parse raw
uninstall strings as untrusted local metadata, classify capability/safety, correlate read-only
process/startup/service evidence, and display an expiring Preview. It must then stop. Target
acknowledgement records understanding only and never authorizes execution. Raw uninstall strings,
registry paths and installer arguments must not enter audit or model payloads.

Stage 4D1 has no execution authority; its acknowledgement cannot authorize Stage 4D2A. It still has
no package-removal adapter, generic runner, elevation, program-file deletion or user-data deletion.
Ambiguous names are never auto-selected, and stale identity, metadata or capability evidence
invalidates every Preview.

Stage 4C2 retains earlier stages and adds exactly three narrow startup-configuration tools:
`system.service.startup.set_automatic`, `system.service.startup.set_manual`, and
`system.service.startup.restore`. They may change only one exact signed, own-process,
current-user third-party service between non-delayed Automatic and Manual after policy,
permission, dependency-impact, verified DPAPI backup, Preview, plan-confirmation,
immediate-confirmation, execution-time revalidation, read-back verification, and unchanged
runtime-state gates. Delayed Automatic is read-only. Disabled, Boot, System, driver, shared,
Microsoft, security, network, login, storage, update, enterprise, Agent, unknown, or dependent
services are blocked. Restore is conditional FULL: it needs fresh confirmations and current
configuration must exactly equal the Agent-written state, otherwise `RESTORE_CONFLICT`.

Stage 4C1 still provides only `system.service.start` and `system.service.stop`; restart remains
an explicit R2_HIGH_IMPACT STOP/verify/revalidate/START/verify transaction with MANUAL
recovery. Never cascade dependencies, retry automatically, elevate, use shell, WMI, `sc.exe`,
kill a service process, call `ChangeServiceConfig2`, alter delayed/Disabled/service binary/
account/password/dependencies/recovery/security fields, uninstall software, or auto-resume an
interrupted transaction.
