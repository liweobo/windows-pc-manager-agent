# Roadmap

## Stage 0 — secure foundation (complete)

GUI/tray, provider boundary, structured plans, tool registry, risk review,
confirmation, audit, rollback contract, R0 metadata scanner, tests, and CI.

## Stage 1 — read-only analysis (complete)

Large-file reporting, cautious idle-file evidence and confidence, staged
duplicate detection, exports, richer filtering, user-managed roots/exclusions,
structured model planning, aggregate-only explanation, audit, and a 10,000-file
benchmark. Installed-software inventory remains an unimplemented MVP item and is
planned as a separate read-only Stage 1B/Stage 3 increment.

## Stage 2A — safe file operations (complete)

Ordinary directory creation, same-volume file/directory move, same-parent structured
rename, real Preview, conflict/limit checks, persistent transaction and item states,
write-ahead Undo, result verification, crash interruption recovery, reverse-order
rollback, separate rollback confirmation, GUI history, and Windows integration tests.

Overwrite, cross-volume move, arbitrary rename code, and deletion are deliberately absent.

## Stage 2B — Windows recycle bin (complete)

Explicitly selected files/directories can be moved to the Windows Recycle Bin after
strict local-volume capability checks, recursive identity snapshots, R2 plan approval,
fresh revalidation, and a second short-lived object-specific confirmation. Each item has
write-ahead MANUAL recovery evidence and Shell callback verification. Automatic restore,
permanent deletion, emptying the Recycle Bin, and non-system volumes remain prohibited.

## Stage 3 — system status (complete)

Read-only Windows identity, multi-sample CPU, memory/pagefile, local fixed disks,
metadata-only process inventory, Run/Startup entries, SCM service queries, uninstall-registry
software inventory, deterministic threshold findings, partial failure, cancellation, audit,
optional minimal-data model explanation, and a dashboard. No system mutation, command-line
collection, shell, WMI, elevation, malware diagnosis, or `Win32_Product`.

## Stage 4A — controlled process management (complete)

Ordinary-user process resolution, application grouping, deterministic protected-process
classification, read-only impact Preview, `WM_CLOSE` graceful exit, separate
`TerminateProcess` force flow, two confirmations per exact action, PID-reuse defense,
postcondition verification, additive transaction/audit records, restart interruption
handling, and GUI/chat entry points. Force never follows automatically and cannot reuse a
graceful approval. Process command lines, elevation, shell and automatic rollback are absent.

## Stage 4B — controlled startup management (complete)

Fixed-source read-only inventory, exact current-user startup identity, default-deny safety
classification, DPAPI-encrypted exact backup, Preview, two confirmations, single-object
disable/restore, conflict-free FULL rollback, verification, audit, GUI and Windows read-only
probe. Only HKCU Run and supported current-user Startup Folder links can be changed. There is
no generic registry editor, StartupApproved write, machine-wide change, RunOnce change,
permanent deletion, bulk action, elevation or shell fallback.

## Stage 4C1 — controlled service state management (complete)

Bounded SCM inventory, exact ServiceName identity, conservative protected-service policy,
dependency/dependent analysis, ordinary-user permission evidence, Preview, two confirmations,
single-service START/STOP, explicit STOP/START RESTART, write-ahead transaction states,
postcondition verification, partial-result reporting, audit and GUI/chat entry points.
Only signed current-user own-process third-party services can be eligible. Configuration
changes, cascade controls, service deletion/configuration, elevation, shell, WMI, process kill,
automatic retry/resume and automatic rollback are absent.

## Stage 4C2 — controlled service startup type management (complete)

Stable service identity/configuration separation, exact non-delayed Automatic/Manual transitions,
ordinary-user `SERVICE_CHANGE_CONFIG` evidence, dependency-free policy, DPAPI-encrypted original-value
backup, immutable R2 Preview, two confirmations, execution-time revalidation, single-field SCM write,
runtime-state invariant, verified conditional FULL restore history, additive audit and GUI are complete.
Delayed Automatic, Disabled, Boot/System, driver/protected/unknown/dependent services, runtime changes,
generic configuration, account/binary/dependency/recovery/security edits, elevation, shell and automatic
retry/resume remain absent.

## Stage 4D1 — software identity and uninstall Preview (complete)

Normalized source-qualified identity, exact conservative resolution, MSI/vendor/package/MSIX/
portable/feature/driver capability classification, deterministic safety policy, read-only impact
evidence, five R0 tools, expiring Preview, privacy-minimized audit and target acknowledgement are
complete. Dedicated zero-execution tests, a 95% boundary coverage gate, a Windows read-only probe and
a 10,000-record benchmark protect the boundary.

Stage 4D1 intentionally has no uninstaller, package remover, elevation, shell, program-file deletion
or user-data cleanup. Any future uninstall execution is a new security stage and cannot reuse this
stage's target acknowledgement.

## Stage 4D2A — controlled current-user MSI uninstall (complete)

One high-confidence current-user unmanaged MSI may pass strict ProductCode/API identity validation,
default-deny safety classification, complete process/service preflight, a fresh Preview, durable plan
and runtime confirmations, and the one-tool fixed `msiexec /x ProductCode /norestart` adapter.
Post-exit verification uses both refreshed inventories; residuals are report-only. There is no
elevation, reboot, retry, process/service control, raw UninstallString or automatic rollback.

## Stage 4D2B — controlled interactive Vendor uninstall (complete)

One high-confidence current-user direct local Vendor `.exe` may pass Windows command-line parsing,
strict path/File ID/SHA-256/offline Authenticode/Publisher/install-location trust, the finite
interactive argument policy, Stage 4D1 class policy, read-only preflight, fresh Preview, two durable
confirmations and the sole shell-free adapter. Process/descendant observation remains non-controlling;
fresh inventory determines removal and residuals are report-only. Raw/Quiet UninstallString,
wrappers, scripts, network/relative/PATH targets, elevation, retry, reboot, process/service control,
vendor-UI automation and cleanup remain prohibited.

## Stage 4D2C1 — controlled current-user winget uninstall (complete)

Trusted Desktop App Installer alias identity, official-source bounded Package inventory, exact
Package ID/version/source/scope identity, high-confidence Package-to-Software mapping, protected-class
policy, read-only preflight, two durable confirmations, fixed shell-free adapter, three-mechanism
transaction exclusion, dual fresh verification, audit, report-only residuals and GUI are complete.

Custom/msstore sources, machine scope, raw args, source changes, silent/override/force/purge, elevation,
restart/retry, process/service control, cleanup, MSIX/AppX and Store execution remain absent.

## Stage 4D2C2 — controlled current-user MSIX / Store App uninstall (complete)

Structured current-user WinRT inventory, Family/Instance identity, package-type and protected-class
policy, complete relationship gate, process/service preflight, double confirmation, fixed
`PreserveRoamableApplicationData` PackageManager adapter, global uninstall exclusion, fresh
verification, privacy-minimized audit, report-only residual status and GUI are complete.

All-users and Provisioned removal, Framework/Resource/Bundle/Optional/Dependency/System/Security/
Unknown removal, PowerShell, elevation, lifecycle control, automatic retry and extra data deletion
remain absent. Windows may remove package-managed LocalState; V1 blocks known orphan dependency risk.

## Stage 4D3 — residual analysis and user-data protection (planned)

Future work is report-only research into ownership-confidence classification for Program Files,
AppData, ProgramData, shortcuts, cache, configuration, databases and user-generated content. It does
not inherit deletion authority from Stage 4D2C2.

## Stage 4C3+ / other controlled system operations (not started)

Broader service changes, unsupported uninstall mechanisms, firewall changes, cleanup and other system
operations remain design-only. Each capability requires its own threat model, confirmation,
recovery plan, Windows API experiment, and isolated test/review before implementation.

## Later stages

Each R3 capability receives an independent threat model, implementation, test
plan, and review. Voice, office/browser automation, schedules, and cross-platform
adapters follow only after the safety boundary is stable.
