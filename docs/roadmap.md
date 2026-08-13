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

## Stage 4C2+ — other controlled system operations (not started)

Broader service changes, software uninstall, firewall changes, cleanup and other system
operations remain design-only. Each capability requires its own threat model, confirmation,
recovery plan, Windows API experiment, and isolated test/review before implementation.

## Later stages

Each R3 capability receives an independent threat model, implementation, test
plan, and review. Voice, office/browser automation, schedules, and cross-platform
adapters follow only after the safety boundary is stable.
