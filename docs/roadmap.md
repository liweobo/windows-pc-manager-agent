# Roadmap

## Stage 5B — bounded voice input/output (implemented; real-device validation pending)

Visible PTT, independent upload disclosure, provider-neutral STT/TTS, mandatory final review, shared
text/voice preparation, metadata-only single-use journal, safe finite summaries, cancellation and fake
device/SDK/GUI/security tests. All existing domain permissions remain unchanged. Exact default-device
24kHz PCM only; no ambient listening or biometrics. Some domain receipts remain text-only/unverified.
Complete the separately authorized real Windows microphone/output/network-cost checklist before release.

## Stage 5C — proposed only, not started

Design a separate browser domain with exact origin/tab scope, read-only-first actions, untrusted page
content, independent external-send/purchase confirmations, secret protection and no inherited voice or
Office authority. No browser controller, extension, background task or auto-click is added by Stage 5B.

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

## Stage 4D3 — residual analysis and user-data protection (complete)

Unified MSI/Vendor/winget/MSIX uninstall contexts, exact-source metadata collectors, stable residual
identity, deterministic classification, structured ownership evidence/confidence, independent user-
data protection, bounded/cancellable scanning, three R0 tools, persistence, audit, local export and a
report-only GUI are complete. The implementation performs no full-disk/name search or content read.

## Stage 4D4 — safe residual cleanup (complete)

Selected Stage 4D3 UUIDs are intent only. Stage 4D4 adds selected-only Fresh identity/material/
classification/ownership/protection/path/activity/recoverability validation, a deterministic allow-list,
all-or-nothing mixed batches, R2/R2_HIGH_IMPACT Preview, separate durable plan/runtime confirmations,
final TOCTOU checks, shared Stage 2B Recycle Bin execution, identity-aware verification, MANUAL recovery,
audit, cancellation/partial results and non-resumable crash handling.

Only HIGH-confidence Program Residual, app-specific Cache/Log and exact obsolete Shortcut can pass every
gate. User/config/database/package/plugin/license/application-state/unknown/shared/recent/reparse/network/
unsupported-volume cleanup, permanent deletion, registry cleanup, automatic restore, shell and elevation
remain absent.

## Stage 4X1 — Privileged Action Protocol and Mock Broker (complete)

- Added safety-first permission resolution and strict versioned typed action payloads.
- Added canonical authenticated requests, exact two-level confirmation bindings and atomic replay store.
- Added a complete in-process Mock Broker for synthetic service Start/Stop only, with Fresh/TOCTOU
  validation, fake execution, verification, audit and crash interruption.
- Default remains disabled. No UAC, real elevation, admin process or Windows privileged write exists.

## Stage 4X2 — one-shot elevated service Broker (implementation complete; production signing pending)

- Added a separate frozen Broker, explicit one-shot UAC launcher, fixed current-user named-pipe ACL,
  OS-derived caller/Broker identity, bounded authenticated handshake, replay protection and durable audit.
- Added only exact Stage 4C1-safe `SERVICE_START` and `SERVICE_STOP`, with Broker-side Fresh/TOCTOU checks,
  one SCM dispatch, Broker readback and independent Main readback. No Restart or generic action channel.
- Added development hash-pinned packaging and no-UAC automated tests. Production remains `NOT_READY` until
  a release certificate, pinned signer identity, trusted installer location and signed update workflow exist.

## Stage 4X3 — dedicated privileged business integrations (implementation complete; manual release validation pending)

The one-shot Broker now has dedicated actions for Stage 4C2 service startup change/restore, exact 32/64-view
HKLM Run disable/restore, and exact machine-scope MSI uninstall. Each reuses its established business safety
policy, adds immutable manifest/schema/policy binding, performs Broker Fresh revalidation and action-specific
verification, then requires standard-user Main readback. The GUI preserves two confirmations before one UAC
attempt. Automated tests use synthetic adapters only.

Machine-wide Vendor uninstall is deliberately deferred. Production release remains blocked on trusted
installation and pinned Authenticode release signing, plus explicit disposable-system manual tests.

## Stage 4E1 — read-only system optimization analysis (complete)

The application now provides an isolated R0 snapshot, bounded known-location and authorized-root metadata
analysis, candidate safety/protection/confidence classification, multi-factor performance findings,
non-executable recommendations, aggregate audit, cancellation, export and a seven-view dashboard. No cleanup,
tuning, service/startup/process action, uninstall, shell, elevation or Broker call is reachable.

Reliable non-elevated Windows Update, Delivery Optimization and disk-I/O detail remains intentionally
unavailable rather than guessed. Stage 4E2 cannot reuse a Stage 4E1 report or confirmation as authority;
it starts from the separate Fresh workflow described below.

## Stage 4E2 — controlled cleanup execution (complete, narrow V1)

Implemented session-only report intent, exact known-root child rediscovery, Fresh identity/material/
classification/protection/activity/recoverability evidence, a second default-unchecked selection,
R2/R2_HIGH_IMPACT Preview, durable two-level confirmation, single-use reference-only execution, fail-stop
Recycle Bin placement, verification, audit and MANUAL recovery. Recycle Bin inspection/emptying is a
separate exact-volume R2_HIGH_IMPACT/recovery-NONE workflow.

Direct V1 sources are current-user Temp, DirectX shader cache and current-user crash dumps. Stage 1 personal
file candidates route to Stage 2B and Stage 4D3 residual candidates route to Stage 4D4. Browser/system Temp,
Windows Update, Delivery Optimization and broader Windows-managed cleanup remain deferred. There is no
permanent delete, shell, elevation/Broker, service/process action or automatic resume/restore.

## Stage 4E3 — recommendation review orchestration (implemented, narrow V1)

Typed recommendations, a finite default-deny routing matrix, four independent R0 review tools, bounded
single-use navigation, sequential persistent sessions, original domain preparation/confirmation flows,
domain-result verification and separately confirmed minimal observation refresh are implemented. Services
are review-only; no new Windows writer, global authorization/Undo, auto-chain or privileged action exists.

The first new Preview in a review can be correlated with its original domain transaction. Stage 4X receipts
and subsequent Force/Restore actions remain in their original business UI. Metrics are observations in an
independent window, not a persistent causal-benefit time series. Production signing and isolated manual
Windows validation from earlier stages remain outstanding; this is not a production-ready release claim.

## Stage 5A — structured Office automation (narrow V1 implemented; local checks passed)

Exact document authorization, handle identity, bounded static parsing, finite edit plans, Preview/Diff,
verified encrypted backup, independent confirmations, conflict-safe commits, verification and conditional
recovery are implemented. The GUI and optional minimum-context OpenAI capability are separate from system
management. See [the actual format matrix and limitations](office-automation-model.md). No macro, COM,
shell, Office UI control or privileged route exists. Final quality/CI results are reported separately;
this is not a production-signing or full Microsoft Office compatibility claim. See the
[local verification record](stage5a-validation.md); remote CI must be checked against the final commit.

## Stage 5B — Voice Interaction (planned, not started)

Push-to-talk, replaceable STT/TTS, editable transcription and voice cancellation need a separate plan.
Voice recognition must never substitute for destructive confirmation. Do not start automatically.

## Stage 5C / 5D / 5E (planned, not started)

Structured browser automation, memory/multi-agent integration and the final cross-domain orchestrator
remain independently designed future stages; Stage 5A grants none of their capabilities.

## Stage 4X4+ — additional privileged actions (not started)

Any startup, service-configuration, installer, registry or other R3 adapter needs its own narrow stage,
threat model, production signing/deployment proof, Preview, confirmation, recovery semantics and disposable
Windows tests. Stage 4X2 authority must never be generalized or reused.

## Stage 4C3+ / other controlled system operations (not started)

Broader service changes, unsupported uninstall mechanisms, firewall changes, broader cleanup and other system
operations remain design-only. Each capability requires its own threat model, confirmation,
recovery plan, Windows API experiment, and isolated test/review before implementation.

## Later stages

Each R3 capability receives an independent threat model, implementation, test
plan, and review. Voice, office/browser automation, schedules, and cross-platform
adapters follow only after the safety boundary is stable.
