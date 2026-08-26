# Changelog

## Unreleased — Stage 4X1

### Added

- Strict versioned Privileged Action Protocol models, canonical JSON serialization, bounded parsing,
  HMAC-SHA-256 Mock integrity, short-lived nonces and typed result envelopes.
- Safety-first `PrivilegeRequirementResolver`, separate privileged allow-list, action-specific payloads,
  durable two-level confirmations and atomic SQLite replay protection.
- Full in-process Mock Broker pipeline with fresh target/safety/risk/privilege/TOCTOU validation, fake
  service Start/Stop execution, postcondition verification and four privacy-minimized audit event types.
- Disabled-by-default runtime composition, explicit developer Mock dialog, protocol documentation and
  unit/integration/security/GUI coverage with a dedicated 95% CI gate.

### Security

- No real elevation, UAC prompt, admin child process, shell, subprocess, SCM write, startup write or
  machine MSI dispatch exists in Stage 4X1. The main Agent remains a standard-user process.
- Only `SERVICE_START` and `SERVICE_STOP` are Mock-executable. Restart, startup-type change, machine
  startup disable/restore and machine MSI removal are defined-only and rejected by the Broker allow-list.
- Requests contain no command, executable, argument list, script or generic dictionary. Authentication
  keys, raw nonces, payloads and caller fingerprints are not written to audit logs.
- Corrupt persistence, unavailable mandatory audit, changed confirmation bindings, expiry, replay,
  concurrent consumption and crash recovery all fail closed without redispatch.

## Unreleased — Stage 4D4

### Added

- Fresh selected-only residual identity/material/classification/ownership/protection/path/activity/
  recoverability revalidation and deterministic `CleanupEligibilityPolicy`.
- `ResidualCleanupPlan`, exact reference-only item requests, dynamic R2/R2_HIGH_IMPACT thresholds,
  all-or-nothing mixed-batch policy, independent durable plan/runtime confirmations and replay guard.
- Shared Stage 2B identity/tree checked Recycle Bin executor, final TOCTOU scan, per-item verification,
  MANUAL recovery records, fail-stop cancellation/partial results and non-resumable crash recovery.
- PySide6 Fresh Preview flow with explicit plan and immediate confirmations, blocked reason table,
  truthful recovery/partial completion messaging, plus detailed Stage 4D4 API documentation.
- Synthetic unit, integration, security, GUI and selected-tree performance coverage.

### Security

- Only HIGH-confidence Program Residual, app-specific Cache/Log, and exact obsolete Shortcut evidence
  can become eligible. Configuration, User Data, Database, Package User Data, Plugin/Extension,
  License/Application State, Unknown, shared, recent, protected, reparse, network and unsupported-volume
  candidates remain blocked regardless of user wording.
- Stage 4D3 reports and confirmations cannot authorize writes. The sole write input contains internal
  transaction/plan/Preview/item references and cannot accept a path, action, command or model argument.
- Every real mutation is Windows Recycle Bin placement. There is no permanent-delete, registry-cleanup,
  shell, elevation, sibling/parent widening or automatic restore path.
- Audit uses candidate IDs and path/evidence digests; source paths, file contents, Recycle Bin identifiers
  and raw Shell result text are excluded from Stage 4D4 audit payloads.

### Changed

- Stage 2B and Stage 4D4 now share `VerifiedRecycleBinExecutor`, preserving the existing Stage 2B public
  contract while preventing duplicate mutation logic.
- `ToolManifest` supports a finite dynamic risk set so the D4 write manifest advertises maximum
  R2_HIGH_IMPACT while accepting only R2 or R2_HIGH_IMPACT plans.

## Unreleased — Stage 4D3

### Added

- Unified durable `UninstallContext` capture for controlled MSI, Vendor, winget and MSIX transactions,
  with exact pre-uninstall paths, identity digests and truthful verified/unverified completion states.
- Stable metadata-only `ResidualIdentity`, deterministic classification, structured ownership evidence,
  independent ownership confidence and user-data protection policy.
- Six finite exact-source collectors, shared object/time/cancellation budget, fail-soft issue reporting,
  reparse/identity revalidation and SQLite report persistence.
- Three R0 tools (`software.residuals.analyze`, `.report`, `.inspect`), privacy-minimized audit, exclusive
  local JSON/CSV export, safe Explorer selection and a cancellable PySide6 report UI.
- Unit, integration, GUI, security, zero-destructive-call and 10,001-object performance tests.

### Security

- Stage 4D3 is report-only: no delete, cleanup, move, rename, Recycle Bin, registry write, shell or
  content-reading path exists, and no cleanup tool is registered.
- Scope comes only from exact durable uninstall evidence. Full-disk/name searches, protected roots,
  network paths, traversal, symlink/junction/reparse traversal and stale identities fail closed.
- User data, databases, package data, configurations, plug-ins, developer environments and unknown
  items stay protected independently of ownership confidence. Provider payloads redact local paths.
- Stage 4D3 plan approval is R0 and cannot be reused by a future Stage 4D4 cleanup flow.

### Fixed

- Ignore queued worker callbacks after the residual dialog starts closing, and wait for export
  completion in GUI coverage so a late modal notification cannot race dialog teardown.
- Include the Stage 4D3 residual-policy tests in the aggregate 95% CI safety-coverage gate.

## Unreleased — Stage 4D2C2

### Added

- Structured PyWinRT current-user MSIX/AppX inventory with raw/normalized records, separate Package
  Family and version-sensitive Package Instance identities, deterministic type classification and
  exact target resolution.
- Framework/resource/bundle/optional/system/security/unknown protection, current-user-only scope,
  complete direct/reverse relationship analysis, orphan-dependency blocking and read-only
  process/service preflight.
- Sole `software.uninstall.msix` R2 tool using WinRT PackageManager with the fixed
  `PreserveRoamableApplicationData` option, durable global uninstall transaction exclusion, two
  expiring single-use confirmations, TOCTOU revalidation, privacy-minimized audit and fresh package
  inventory verification.
- PySide6 double-confirmation flow, MSI/Vendor/winget/MSIX router integration, synthetic unit,
  integration, security and GUI tests, plus a real-Windows read-only PackageManager probe.

### Security

- Display text is discovery-only. Execution binds Package Full Name, Family, version, architecture,
  scope, type, dependency snapshot, safety, preflight and data-impact digests.
- V1 blocks every non-ordinary package, any known dependent, any direct dependency that Windows
  might remove as orphaned, incomplete inventory/relationship/preflight evidence, elevation and
  overlapping MSI/Vendor/winget/MSIX work.
- The Agent never uses PowerShell/CMD/shell, all-users/provisioned removal, elevation, process
  termination, service stop, retry, recursive residual inspection or extra user-data deletion.
  Windows may remove package-managed LocalState; the fixed option requests preservation of Roamable
  data. Rollback is NONE and fresh inventory—not the deployment return—decides success.

## Unreleased — Stage 4D2C1

### Added

- Trusted Desktop App Installer App Execution Alias identity, bounded official-source `winget
  export` JSON inventory, exact Package identity/resolver and high-confidence Package-to-Software
  mapping.
- Default-deny Package capability and software-class policies, read-only process/service/busy-state
  preflight, expiring Preview and two durable digest-bound confirmations.
- Sole `software.uninstall.winget` tool with a fixed current-user interactive argument array,
  sanitized child environment, explicit executable/cwd, DEVNULL streams and `shell=False`.
- Global MSI/Vendor/winget transaction exclusion, atomic one-shot write guard, crash interruption,
  privacy-minimized audit, dual Package+Software verification and exact-path residual report.
- PySide6 non-technical double-confirmation flow, deterministic router integration, synthetic unit/
  integration/security/GUI coverage and a real-Windows read-only alias/inventory probe.

### Security

- Block Package Name execution, ambiguous/missing identity, custom/msstore source, machine scope,
  stale version/source/mapping/alias, protected software, related running services, incomplete
  probes, elevated Agent and overlapping uninstall transactions.
- No raw args, override/silent/force/all/purge, source mutation, PATH lookup, Shell/PowerShell/CMD,
  UAC, automatic retry/restart, process termination, service stop, MSIX/AppX/Store fallback or
  program/user/residual deletion.
- Exit code never means success. Both fresh inventories must prove the exact identities disappeared;
  otherwise the result remains explicit and unverified. Rollback is NONE.

## Unreleased — Stage 4D2B

### Added

- Strict parsing of untrusted interactive Vendor metadata into a separate executable token and
  exact argv vector using Windows command-line semantics; raw `UninstallString` remains ephemeral.
- `VendorUninstallerIdentity` binding absolute local path, Windows file identity, metadata,
  SHA-256, offline Authenticode evidence, conservative Publisher match, registry source and exact
  argument-policy fingerprint.
- Default-deny execution policy, read-only process/service preflight, expiring Preview, durable
  two-tier confirmations, replay protection and MSI/Vendor cross-mechanism transaction exclusion.
- Sole `software.uninstall.vendor` tool and shell-free Windows adapter with explicit executable/cwd,
  minimized secret-free environment, child-process observation and truthful stop-monitoring state.
- Fresh post-process inventory verification, report-only exact-location residual analysis,
  privacy-minimized audit, crash interruption handling and deterministic MSI/Vendor UI routing.
- Synthetic parser, policy, adapter, verification, persistence, security, integration and GUI tests,
  plus a dedicated Stage 4D2B Windows CI coverage gate. No test launches a real uninstaller.

### Security

- Block CMD/PowerShell/pwsh/script-host/Rundll32 wrappers, scripts, UNC/device/relative/PATH-resolved
  targets, temporary/download/cache locations, QuietUninstallString and unsafe/unknown arguments.
- Require current-user scope, complete stable identity, valid offline signature, conservative signer
  match, approved install-location relation, two fresh confirmations and mandatory pre-start audit.
- Never auto-elevate, terminate related processes, stop services, control Vendor UI, force-kill,
  reboot, retry, delete program/user/residual data, or treat process exit as verified removal.

## Unreleased — Stage 4D2A

### Added

- Strict `ValidatedMsiProduct` identity, Windows Installer registration validation, execution-only
  safety policy, process/service preflight, fresh execution Preview and one-product R2 transaction.
- Durable plan and runtime confirmations bound to plan/Preview/identity/ProductCode/capability/
  safety/preflight/risk digests, with expiry, atomic consumption and replay protection.
- Sole `software.uninstall.msi` tool using fixed system `msiexec.exe` arguments, `shell=False`,
  `/norestart`, deterministic exit mapping and no raw UninstallString input.
- Post-installer inventory/MSI verification, non-deleting residual report, interrupted/waiting crash
  recovery, privacy-minimized audit and a modeless PySide6 two-confirmation workflow.
- Synthetic unit, integration, security and GUI tests; no test invokes a real system uninstaller.

### Changed

- Classify Visual C++/redistributable/shared runtimes before developer runtimes and block their
  Stage 4D2A execution.
- Restore UTC awareness when SQLite returns confirmation timestamps without timezone metadata.

### Security

- Current-user unmanaged MSI only; machine/managed MSI, protected classes, ambiguous identities,
  related running processes/services and incomplete evidence fail closed.
- No automatic process termination, service stop, elevation, reboot, retry, residual deletion,
  Vendor/winget/MSIX fallback, shell wrapper or arbitrary installer argument.

All notable changes are documented here. The project follows semantic versioning
once release tags are introduced.

## [Unreleased]

### Added

- Stage 4D1 normalized software identity, conservative exact target resolution, source-qualified
  uninstall capability analysis, deterministic safety classification, and process/startup/service
  impact evidence behind exactly five read-only R0 tools.
- Expiring uninstall Preview and digest-bound target acknowledgement that deliberately stop without
  creating execution authority; raw uninstall strings remain ephemeral, local, unlogged and
  unexecuted.
- Stage 4D1 GUI/chat entry points, privacy-minimized audit events, zero-execution source guards,
  Windows read-only inventory probe, 10,000-record benchmark, and dedicated 95% CI coverage gate.

- Stage 4C2 exact-service startup configuration workflow with three narrow registered tools:
  set non-delayed Automatic, set Manual, and restore an Agent-owned verified backup.
- Pre-Preview DPAPI-encrypted backup verification, immutable R2 plan, two digest-bound one-time
  confirmations, execution-time identity/configuration/runtime/dependency/permission revalidation,
  write-ahead SQLite transactions, privacy-minimized audit, and conditional FULL restore history.
- Service-management UI for Automatic/Manual Preview and restore history, plus fake-SCM unit,
  integration, security and GUI coverage and an opt-in real-Windows read-only permission probe.

- Stage 4C1 bounded service inventory and conservative classification for exact ServiceName,
  current-user own-process third-party services with verified signatures.
- Registered R2 `system.service.start` and `system.service.stop` tools; Restart is an explicit
  R2_HIGH_IMPACT Stop/verify/revalidate/Start/verify transaction with no single-step shortcut.
- SCM-only ordinary-user execution, dependency/dependent blocking, two digest-bound one-time
  confirmations, write-ahead SQLite states, privacy-minimized audit, partial-result reporting,
  cancellation, GUI/chat entry points, and truthful MANUAL recovery guidance.
- Stage 4C1 fake-SCM unit/integration/security/GUI coverage plus an opt-in real-Windows
  read-only adapter probe. Tests never mutate an installed service.

- Stage 4B fixed-source startup inventory and conservative classification for HKCU/HKLM
  Run/RunOnce plus current/common Startup folders, with only current-user HKCU Run and
  supported `.lnk` entries eligible for management.
- Narrow R2 `startup.disable` and `startup.restore` tools, exact DPAPI-encrypted backups,
  transaction-bound two-tier confirmations, execution-time identity revalidation, verified
  FULL rollback under conflict-free preconditions, privacy-minimized audit, and GUI.
- Stage 4B unit, integration, security, GUI, and real-Windows read-only adapter tests,
  including stale state, confirmation replay/expiry, source expansion, backup corruption,
  conflict, cancellation, verification and rollback boundaries.

- Stage 4A deterministic current-user process resolution, application grouping,
  protected/system/security/service/Agent classification, live impact Preview, and
  handle-bound PID-reuse defense.
- Registered R2 `system.process.request_exit` (`WM_CLOSE`) and independently planned
  R2_HIGH_IMPACT `system.process.force_terminate` tools with two one-time confirmations,
  SQLite transaction states, verification, audit, restart interruption handling, and GUI.
- Unit, integration, security, GUI, and disposable real-Windows child-process tests for
  Stage 4A, including stale identity, replay, no-window, timeout, cancellation, and failure.
- Stage 3 R0 Windows diagnostics for system identity, multi-sample CPU, memory,
  local fixed disks, processes, startup entries, services, and installed-software
  registry records behind registered tools and exact plan confirmation.
- A partial-result `SystemSnapshot`, deterministic threshold engine, conservative
  findings/actions, privacy-minimized audit trail, and cancellable background dashboard.
- Provider-neutral diagnostic intent/explanation contracts whose external payloads
  exclude measurements, paths, process/service/software identities, and commands.
- Unit, integration, security, GUI, performance, and real-Windows query-only tests for
  Stage 3, plus a detailed function-level API reference.

- Secure stage 0 Windows desktop and tray foundation.
- Replaceable LLM provider contract and OpenAI Responses API adapter.
- Structured plans, risk review, registered-tool execution, confirmation, audit,
  rollback contracts, and a metadata-only directory scanner.
- Unit, integration, security, GUI, CI, and project documentation foundations.
- Detailed API reference covering every production function and method.
- Stage 1 authorized/favorite/forbidden directory management backed by SQLite.
- Streaming, cancellable metadata scanning with progress, timeout, file limits,
  typed partial outcomes, central file classification, and paged result storage.
- Configurable large-file and cautious inactive-file analysis with confidence and
  evidence, plus staged duplicate verification using quick hashes, SHA-256, and
  optional byte comparison.
- Deterministic file-analysis plan compiler, independent semantic validator,
  exact plan confirmation, aggregate-only explanation, and per-tool audit events.
- Stage 1 GUI for plan review, progress, cancellation, result filtering/sorting,
  Explorer selection, and exclusive-create CSV/JSON export.
- 10,000-file performance benchmark and expanded unit, integration, GUI, security,
  cancellation, audit, provider, and export tests.
- Stage 2A immutable operation plans, finite rename/organization rules, local source
  resolution, real filesystem Preview, conflict and same-volume checks.
- Registered R1 `file.mkdir`, `file.move`, `file.rename`, and rollback-only empty
  created-directory tools using verified Windows file identities and no-overwrite APIs.
- Persistent operation/item state machines, write-ahead Undo journal, one-time
  Preview-bound confirmation, fail-safe executor, interrupted transaction recovery,
  reverse-order rollback Preview and independent rollback confirmation.
- Stage 2A GUI for checked analysis results, manual/natural-language planning,
  operation Preview, progress, stop-future behavior, history, and rollback.
- Stage 2A unit, Windows integration, security, failure, recovery, rollback, and GUI tests.
- Stage 2B deterministic `TrashPlan`, directory-tree snapshots, local fixed-NTFS
  capability checks, and the registered R2 `file.trash` tool using Windows
  `IFileOperation` with per-item result callbacks.
- Two independent one-time R2 confirmations, additive SQLite confirmation/recovery
  records, crash reconciliation to UNKNOWN, MANUAL recovery guidance, and an
  independent Windows Recycle Bin GUI page.
- Stage 2B unit, integration, security, COM progress-sink, persistence, cancellation,
  and GUI tests, including a production-source permanent-delete API guard and an
  opt-in disposable real-Windows Recycle Bin probe.

### Changed

- Service stable identity is now separated from mutable startup configuration. Stage 4C1 control
  confirmations bind both digests, while Stage 4C2 can prove a deliberate configuration change
  without weakening ServiceName, type, binary fingerprint, account, state or dependency checks.

- Startup inventory now treats Windows executables without version-resource publisher
  metadata as unknown/read-only instead of aborting the complete inventory.

- Renamed the internal `platform` package to `platform_support` to distinguish
  operating-system adapters from Python's standard-library `platform` module.
- The chat page now routes file-analysis goals to the formal Stage 1 workflow;
  disabled model configuration falls back to deterministic manual planning.
- Chat now routes move/rename/organize/rollback goals to Stage 2A; concrete paths and
  file selection remain local and deterministic.
- Chat refuses permanent-delete/empty-Recycle-Bin wording locally and only routes
  Recycle Bin intent to a page where the user must explicitly select objects.
- Informational Windows copy-engine success HRESULTs now use COM `SUCCEEDED` semantics;
  positive recycle evidence remains mandatory.

### Security

- The user-approved Stage 4C2 R2 exception is limited to one dependency-free, Stage 4C1-eligible
  service and exact Automatic (not delayed) to/from Manual transitions. Disabled, delayed,
  driver/Boot/System, protected, unknown and dependent services remain read-only or blocked.
- Configuration writes require existing ordinary-user `SERVICE_CHANGE_CONFIG` access and call only
  `ChangeServiceConfig` with every field except start type set to no-change. There is no UAC,
  elevation retry, shell, `sc.exe`, WMI, `ChangeServiceConfig2`, runtime control or generic adapter.
- Restore is a new R2 transaction and succeeds only while the stable identity and Agent-written
  current value still match and the encrypted backup verifies. Interrupted transactions never
  auto-resume, and audit never stores service command lines, account secrets or encrypted payloads.

- Stage 4B has no generic registry/file/shell primitive and never writes StartupApproved.
  Machine-wide, RunOnce, common-folder, Microsoft/system/security/driver/enterprise/Agent,
  unresolved and changed entries are read-only or blocked. Exact command/shortcut bytes are
  encrypted separately from audit and are never accepted as public tool arguments.

- Stage 4A blocks critical/protected/system/security/service/other-user/other-session/Agent
  targets, collects no command line, never elevates or invokes shell/taskkill, and never
  upgrades graceful exit into force termination without a new plan and two new approvals.
- Process termination is explicitly non-reversible (`RollbackLevel.NONE`); cancel stops
  waiting/future work only and never claims to restore a process or unsaved application data.
- Stage 3 never collects process command lines or uninstall commands and has no process,
  service, startup, registry, software, elevation, shell, WMI, or system mutation tool.
- Diagnostic model output is finite untrusted intent only; local compilation, registered
  R0 manifests, independent review, digest-bound confirmation, and execution-time review
  remain authoritative. Audit stores collector names, counts, state, warnings, and timing,
  not raw inventories.

- Default-deny protected paths, path traversal, symlink, junction, and reparse
  point handling.
- No permanent deletion, system mutation, elevation, arbitrary shell execution,
  or credential collection.
- External model calls require an expiring digest-bound confirmation. Planning
  sends opaque root IDs and labels only; explanation sends aggregates only.
- Local, canonical paths reject traversal, UNC/device/network roots, ambiguous
  Windows names, protected locations, symlinks, junctions, and reparse points.
- All R1 writes require a persisted transaction capability matching plan, Preview,
  operation, registered tool, and exact arguments. Preview approval is expiring,
  one-time, and invalid after any binding change or restart.
- Move/rename/create revalidate authorization, reparse components, source identity,
  target absence and volume immediately before use; overwrite and cross-volume copy
  behavior are absent. Rollback applies the same checks in reverse order.
- Stage 2B blocks protected/system/application-data roots, authorized roots themselves,
  links/reparse points, system/offline objects, network/removable/non-system volumes,
  overlapping selections, stale directory trees, and unavailable Recycle Bins.
- Recycle execution requires persisted PLAN approval plus consumed RUNTIME approval,
  writes MANUAL recovery evidence before the Shell call, never invokes a legacy or
  permanent-delete fallback, and stops the batch on an ambiguous result.
- Permanent-delete, Recycle Bin bypass, and empty-bin requests are refused and audited
  as R4; an ambiguous first result also marks its parent transaction UNKNOWN.

### Fixed

- Include the Stage 3 confirmation state-machine tests in the CI security-boundary
  coverage job so the package-level 95% gate measures every confirmation module.

- Pin uv 0.11.32 in CI so setup does not depend on a latest-release API lookup.
- Prevent the file-analysis plan button's checked state from being passed to the
  goal text field as a boolean value.
- Use one Windows directory-identity API for discovery and execution-time
  revalidation, and make scanner timeout tests independent of clock resolution.
- Allow the secret-scanning job to read pull-request commit metadata without
  granting any repository write permission.
- Restore UTC information lost by SQLite datetime storage and treat zero-valued
  Windows directory-enumeration file IDs as unknown before duplicate hashing.
- Audit the exact registered tool on execution failure and preserve incomplete
  report artifacts instead of deleting any file in Stage 1.
- Preserve the precise reparse-point denial instead of relabeling it as a generic
  unavailable-path error on Windows runners that can create symbolic links.
- Flush parent operation transactions before child reservations inside the same SQLite
  commit, and preserve conflict-only rollback Previews without creating a confirmation.
- Account for earlier reverse steps when assessing transaction-created directory
  emptiness, while still blocking unmanaged contents.
