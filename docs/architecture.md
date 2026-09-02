# Architecture

## Stage 5D bounded Agent coordination

`app/agents.py` composes a sealed role registry, graph/goal validators, Context gateway, scoped Memory, task journal,
delegation coordinator and resource lock service. `Stage5DAgentRuntime` deliberately has no Domain Executor or
ConfirmationService. It creates a content-free journal and a non-authoritative handoff into the same domain UI that
already owns Fresh resolution, Preview, confirmation, execution and verification.

Runtime identity, capability narrowing and message trust are local code. Provider graph drafts cannot choose their
effective role; `PlannerAgent` replaces role claims with the finite domain mapping. Independent reads can overlap;
any same-resource write intent conflicts. Durable task rows contain only IDs/digests/counts/status and active tasks
become INTERRUPTED on startup. See [full topology](multi-agent-architecture.md),
[Context contract](context-governance.md) and [API](api-stage5d.md).

## Stage 5C browser boundary

`app/browser.py` composes independent URL/action/download policies, SQLite confirmation authority, minimal
audit, a five-tool registry and `BrowserTaskService`. The Qt tab calls orchestration only. The default adapter
is `BrowserWorkerClient`, which launches `python -I -m pc_manager_agent.browser.worker` with `shell=False` and
a credential-scrubbed environment. The Worker alone owns Playwright/Chromium objects and accepts a finite
JSON-lines command protocol; no selector, script, command, HTTP request or generic arguments cross IPC.

The flow is `visible intent → typed action → URL/DNS + semantic policy → exact plan → durable confirmation →
atomic consume → registered tool → isolated worker → fresh observation/readback → minimized audit`. Page
elements bind session/page/navigation plus role/name/href fingerprint. Navigation, takeover, cancellation or
restart invalidates old authority. A download adds Preview → worker temporary file → Main filename/type/size/
magic/hash validation → exclusive commit → conditional recovery record. Browser-to-Office sends only a hint;
Office grants and confirmations remain independent. See [model](browser-automation-model.md) and
[API](api-browser-automation.md).

## Stage 5B voice boundaries

`app/voice.py` composes one application-local coordinator, metadata store, provider service and router.
`ui/voice_audio.py` owns bounded Qt hardware; `ui/voice_controller.py` owns private cancellable workers.
Mirrored `VoiceControls` share that same instance. No device is opened at import or construction.
`domain/user_requests.py` and `orchestration/user_requests.py` form the shared text/voice request boundary.
The Main window only navigates/prepares existing domains; each domain retains its own authority.

The pipeline is `explicit PTT → stopped PCM → exact disclosure → STT → editable visual review → atomic
request claim → shared route → domain Fresh/Preview/confirm/execute/verify`. Input COMPLETED means delivered,
never executed. TTS is an independent `read-only verified facts → finite summary → exact disclosure →
bounded synthesis → visible playback`; PTT first resets output and invalidates late synthesis.

The Broker imports neither this composition nor voice/speech/Qt modules. The separate additive
`voice.sqlite3` has only state/reference/revision/digests; raw audio/transcripts stay volatile.
See [contracts and limitations](voice-interaction-model.md) and [API](api-voice-interaction.md).

## Stage 5A Office boundary

`app/office.py` composes independent read and write registries, exact-file grants, bounded parsing,
metadata persistence, confirmation, audit and recovery. `office/*` operates on structured bytes/models;
only `platform_support/windows/office_files.py` owns held-handle filesystem changes. The UI delegates to
`OfficeDocumentService`, `OfficeEditService` and optional `OfficeModelService`, never directly to a writer.
No Stage 4 tool, global consent or Broker capability is inherited. See [full data flow](office-automation-model.md)
and [function contracts](api-office-automation.md). R0 discovery and R1 backup approvals are not R2 authority.

## Stage 4E3 review orchestration boundary

`app.optimization_reviews` composes the finite policy/resolver, sealed preparation registry, expiring
handoff store, additive SQLite session journal, independent domain receipt reader and outcome coordinator.
The optimization layer owns no execution or confirmation authority. Its four R0 tools remain isolated
from the unchanged Stage 4E1 five-tool and Stage 4E2 four-tool registries.

The GUI prepares one review off-thread, then embeds an existing domain UI. New domain Preview events
carry only an enum and transaction UUID; the result coordinator binds before dispatch, then reads the
domain's durable confirmation and verification evidence. MSIX adds minimal evidence to its existing result
column; process resolution additionally checks the selected observation's creation time/path. No schema
or adapter broadens Windows execution. Sessions use compare-and-swap revisions, expire with their source
reports and become STALE after restart. There is no resumed authority or global undo transaction.

See [complete routing and data flow](optimization-action-routing.md) and
[per-function API](api-optimization-actions.md), including V1 limits for privileged receipts and metrics.

## Stage 4E2 controlled cleanup boundary

Stage 4E2 does not extend the Stage 4E1 registry. `ApplicationRuntime` creates a second isolated
`ToolRegistry` with exactly four tools and a `SystemCleanupExecutionGuard`:

```text
session-local Stage 4E1 report + explicit candidate UUIDs (intent only)
  -> optimization.cleanup.prepare (R0 Fresh discovery)
  -> second exact item selection (default unchecked)
  -> CleanupExecutionPlanBuilder + independent safety review
  -> durable plan + plan confirmation
  -> Fresh selected-item revalidation + runtime Preview
  -> object-specific immediate confirmation
  -> atomic consumption in SystemCleanupRepository
  -> optimization.cleanup.trash, one reference-only item at a time
  -> Windows Recycle Bin primitive + original-identity verification
  -> aggregate audit + MANUAL recovery record
```

`FreshCleanupCandidateRevalidator` resolves a report only from a bounded in-memory TTL store cleared at
shutdown. It never accepts a report body or path from the UI/model. Known-location candidates must exactly
match a finite source/category/root tuple; Stage 1 and Stage 4D3 provenance routes to existing Stage 2B/4D4
workflows. Direct roots are rediscovered as exact children, then each tree is walked without following
reparse points under item/object/byte limits. Identity is read before and after the tree, while protection,
recency, lock, installer activity and Recycle Bin evidence remain independent.

The writer request contains only transaction, plan, Preview and validated-item UUIDs. The guard resolves
immutable local state, checks the consumed confirmation and argument digest, reserves one call, then
`VerifiedRecycleBinExecutor` repeats identity/material checks immediately before the Shell primitive.
Failure or cancellation stops future objects. Restart marks active state `INTERRUPTED`; it never resumes.

Recycle Bin emptying is a separate graph and cannot join an item plan:

```text
exact current-user system-volume inspection
  -> complete aggregate + namespace snapshot
  -> independent R2_HIGH_IMPACT plan and confirmation
  -> exact snapshot reinspection + immediate confirmation
  -> atomic one-use capability
  -> SHEmptyRecycleBinW(exact volume) once
  -> exact-volume reinspection + NONE irreversibility record
```

The adapter never passes a null/all-volume scope. Ordinary cleanup recovery is MANUAL; Bin empty recovery
is NONE. There is no elevation/Broker edge, shell, generic runner, permanent delete or automatic restore.

## Stage 4E1 read-only optimization analysis

Stage 4E1 uses an isolated `ToolRegistry` containing exactly five R0 tools:

```text
local goal + Stage 1 authorized-root IDs
  -> deterministic OptimizationPlan
  -> independent registry/scope/risk review
  -> digest-bound plan confirmation
  -> optimization.snapshot
  -> [storage goal] optimization.storage.analyze -> optimization.cleanup_candidates.analyze
  -> [performance goal] optimization.performance.analyze
  -> optimization.recommendations
  -> aggregate-only audit + non-authoritative report
```

`SystemOptimizationPlatform` is query-only: it exposes system snapshot and storage metadata methods but
no mutation method. The Windows implementation composes Stage 3 query APIs, documented
`SHQueryRecycleBinW` totals and a bounded `pathlib` metadata walker. Personal roots are resolved from
opaque Stage 1 authorization IDs; known system roots use a separate finite scope policy. Every entry is
revalidated with `lstat`; reparse targets and protected roots are not followed.

The plan is a canonical subset of the five-tool registry and binds a minimal `SystemCollector` tuple.
Disk-space questions collect disk plus storage evidence without CPU/services; slow-PC questions collect
CPU/memory/disk/process evidence without storage walking; boot questions collect process/startup evidence.
Only a general check uses the full query surface.

Raw storage observations are separated from candidate policy, performance rules and recommendations.
This prevents a file name or model explanation from becoming safety evidence. The GUI calls only the
orchestrator on a worker thread. Reports also enter a bounded session-only intent store for Stage 4E2 Fresh
discovery. They still cannot pass a write guard or replace a new plan, Preview, safety review or confirmation.

## Stage 4X3 dedicated privileged integration boundary

Stage 4X3 adds no generic administrator interface. A standard-user business workflow first resolves an
exact target and runs its existing safety policy. `PrivilegeRequirementResolver` then routes only a
complete, safety-approved `REQUIRED` result to `ElevatedStage4X3PreparationService`; `NOT_REQUIRED` stays on
the ordinary executor and every other result stops.

The Main side creates a dedicated typed payload, immutable manifest/schema/policy binding, R3 plan and
Preview. Separate durable plan and runtime confirmations precede one UAC attempt. The Broker validates the
authenticated request and stored authority, asks `PrivilegedActionDispatcher` for the exact handler,
performs Fresh revalidation, atomically consumes authority, repeats final validation, invokes one narrow
adapter, verifies a typed postcondition and exits. `Stage4X3PostconditionVerifier` then performs an
independent standard-user readback. Broker success alone is insufficient.

The concrete handler map is finite: service control (existing Start/Stop), service startup
(change/restore), machine startup (disable/restore) and machine MSI (uninstall). Manifest registration and
handler registration are both required; missing either is default-deny. Service configuration uses only the
narrow startup-type adapter, HKLM uses only explicit-view transacted Run-value methods, and MSI uses only the
fixed system `msiexec` adapter. Vendor elevation is deferred.

Qt workers call orchestration only. The UI shows object, action, R3 risk, required Administrator privilege,
rollback level, plan ID and the separate confirmation/UAC phases. It never constructs a command or calls a
Windows mutation adapter directly.

## Stage 4X2 one-shot elevated Broker boundary

Stage 4X2 preserves the Stage 4X1 plan/confirmation/replay protocol and replaces only its execution edge
for two exact actions. The GUI and orchestration stay in a standard-user process; a separate frozen Broker
exists only for one UAC-approved request.

```text
Stage 4C1 safe Start/Stop blocked only by ordinary SCM rights
  -> separate R3 Plan + durable plan confirmation
  -> fresh identity/state/config/dependency/permission Preview
  -> object-specific durable runtime confirmation
  -> registered single-use request in fixed per-user SQLite
  -> pre-UAC Broker path/hash/signature/install/token checks
  -> ShellExecuteEx("runas") with opaque bootstrap identifiers
  -> current-user-only Named Pipe + six-frame mutual handshake
  -> Broker allow-list/binding/Fresh/TOCTOU/audit/replay checks
  -> atomic confirmation/request consumption
  -> exact SCM Start OR Stop + Broker readback
  -> authenticated result + independent Main-process SCM readback
  -> Main bounded-waits for natural Broker exit; no kill/retry/resume/daemon
```

`ElevatedServicePreparationService` is the only bridge from Stage 4C1. It refuses Restart, safety or
dependency blocks, elevated Main processes, incomplete query evidence and actions that ordinary access can
already perform. `ElevatedServiceActionCoordinator` owns one UAC/IPC attempt and a per-request double-click
lock. `ElevatedBrokerServerSession` and `ElevatedBrokerClientSession` implement the fixed transport;
`ElevatedPrivilegedBroker` owns authorization ordering; `WindowsServicePrivilegedHandler` is the sole real
adapter. UI workers only call orchestration off-thread.

The executable is packaged separately and excludes PySide6, provider, model, agent and UI modules. Both
executables declare `asInvoker`; only the explicit `runas` call creates the short-lived elevated process.
The Broker derives the fixed Windows user-data database path itself. No caller-controlled database path,
service name, command or action enters its command line.

## Stage 4X1 privileged protocol and Mock Broker boundary

Stage 4X1 introduces a protocol seam, not an elevated Windows component. The standard-user process
performs ordinary Stage 4C/4D safety review first and then resolves whether the already-safe exact action
needs Administrator access. A safety denial is terminal; a generic access failure is only `UNKNOWN`.

```text
deterministic safety evidence
  -> PrivilegeRequirementResolver
  -> typed PrivilegedActionPlan + fresh Mock-only Preview
  -> durable plan confirmation
  -> fresh Preview + durable immediate confirmation
  -> canonical request + integrity proof + replay record
  -> Mock Broker parse/authenticate/bind/revalidate
  -> atomic request + confirmation consumption
  -> final TOCTOU revalidation
  -> fake Start/Stop executor
  -> fresh fake-state verification + signed result + audit
```

The protocol domain is provider-neutral and contains one discriminated Pydantic payload per finite
`PrivilegedActionType`. It cannot represent a shell command, executable or free-form argument vector.
`PrivilegedActionRegistry` is independent from `ToolRegistry`, is not exposed to the LLM, and registers
only Mock service Start/Stop. The standard-user `ApplicationRuntime` composes it only when the explicit
developer mode is `mock`; `disabled` is the default.

The SQLite repository stores immutable Plan/Preview JSON, two exact confirmation records and a request
record whose request digest and nonce fingerprint are unique. One transaction atomically moves the
request to CONSUMING and both approvals to CONSUMED. Startup recovery converts every active signed,
validating, consuming, executing or verifying transaction to INTERRUPTED and never redispatches it.

The HMAC authenticator is an injected process-local test implementation. It demonstrates canonical
authentication and key separation but is explicitly not the future cross-privilege trust channel. A real
Broker requires a separate design for executable identity, IPC ACLs, process/session identity, code
signing, key establishment, UAC lifecycle and installer/service deployment.

## Stage 4D4 controlled residual-cleanup boundary

Stage 4D4 is an independent R2 workflow layered after, not inside, Stage 4D3:

```text
Stage 4D3 report UUID + explicitly checked candidate UUIDs (intent only)
  -> software.residuals.prepare_cleanup (R0 Fresh Revalidation)
  -> CleanupEligibilityPolicy (deterministic, all rows retained)
  -> all-eligible ResidualCleanupPlan + Fresh Preview
  -> durable PLAN confirmation
  -> second complete Fresh Revalidation + runtime Preview
  -> durable object-specific RUNTIME confirmation
  -> third whole-batch TOCTOU Revalidation
  -> atomic confirmation consumption + reference-only WriteExecutionGuard
  -> per item: PREPARED recovery + mandatory audit
  -> shared VerifiedRecycleBinExecutor -> Windows IFileOperation
  -> original-identity verification -> result/recovery/audit
```

The UI passes only `source_report_id` and candidate UUIDs. `FreshResidualRevalidator` resolves paths from
the local Stage 4D3 repository and never widens to a parent or sibling. It checks the old `lstat` identity,
then creates a new Windows file identity and a complete bounded metadata tree digest. Classification is
based only on the selected root and relative descendants, so unrelated ancestor names cannot authorize or
misclassify content. Any forbidden descendant, budget truncation, identity/material change or unavailable
evidence blocks the entire selected candidate.

`CleanupEligibilityPolicy` is separate from ownership. It combines exact uninstall context, HIGH ownership,
finite classification allow-list, independent protection, shared/recent/path/reparse signals and current
Recycle Bin capability. `ResidualCleanupPreviewEngine` refuses mixed and overlapping batches. Risk is R2 or
R2_HIGH_IMPACT using configured item/object/total/single-item thresholds.

Stage 4D4 has independent additive SQLite tables for plans, items and two confirmation tiers. The write
tool schema has only transaction, plan, Preview and item references; the repository resolves the path only
after the guard proves both approvals were atomically consumed. Restart turns every active transaction into
`INTERRUPTED` and approvals into `EXPIRED`; no code path redispatches work.

Mutation logic is not duplicated. Stage 2B `TrashTool` and Stage 4D4 `SoftwareResidualTrashTool` delegate to
the same `VerifiedRecycleBinExecutor`, which compares exact identity and full tree snapshot immediately
before one `RecycleBinPlatform.recycle` call. There is no delete adapter or fallback. Shell evidence is then
combined with a fresh original-path identity check; a new object at the same path is distinguished from the
removed original.

Audit remains independent and mandatory before dispatch. D4 events store UUIDs, counts and digests; local
paths, file contents, Recycle Bin item identifiers and raw Shell text are excluded. Public recovery output
contains only successful `AVAILABLE` MANUAL records. Manual Windows Recycle Bin restore is outside the
Agent write boundary, so the Agent cannot overwrite a restore conflict.

## Stage 4D3 report-only residual-analysis boundary

```text
controlled MSI / Vendor / winget / MSIX pre-dispatch Preview
  -> UninstallContextRecorder (exact identity + exact known paths; no command/content)
  -> durable context finalized with verified or completed-unverified result
  -> local ResidualAnalysisPlanCompiler (scope is not chosen by the LLM)
  -> independent ResidualSafetyReviewer + expiring R0 plan confirmation
  -> ToolRegistry[software.residuals.analyze]
  -> shared bounded budget
  -> one source-specific metadata collector per exact ContextPathEvidence
  -> lstat identity + reparse/TOCTOU checks
  -> deterministic classification + ownership evidence/confidence
  -> independent UserDataProtectionPolicy
  -> SQLite ResidualReport + aggregate-only audit
  -> report / inspect / local exclusive-create export / safe Explorer selection
  -> STOP
```

The four uninstall executors only call `UninstallContextRecorder` immediately before their already
authorized adapter dispatch and finalize that context after verification. Failure to store Stage 4D3
evidence never expands or changes D2 execution authority. Raw uninstall strings, argv, package
contents and file contents are not part of the context.

`ResidualScanScopePolicy` is separate from ordinary user-file authorization because it handles a
software-management scenario, but it reuses the protected-path and reparse principles. Only exact
pre-uninstall paths are accepted; Program Files, ProgramData, AppData, Desktop or a drive root is
never broadened into a name search. Six collectors own fixed source types instead of one universal
scanner. A shared 25,000-object/default 60-second budget and cancellation token span every collector.

`ResidualIdentity` records normalized path, device/file ID, object type, size and modification time
from `lstat`. Ownership classification and cleanup safety are separate models: exact install/package/
shortcut evidence may produce HIGH ownership, while a database, configuration, plug-in or package
user-data candidate remains protected. All classification is deterministic; an LLM may receive only
the redacted metadata payload for explanation.

There is deliberately no Stage 4D3 Preview or authorization that a future cleanup can consume. A
future Stage 4D4 must start with fresh discovery, identity revalidation, safety review and new R1/R2
confirmations.

## Stage 4D2C1 controlled winget Package execution boundary

```text
exact Installed Software selection
  -> read-only SoftwareUninstallRouter (MSI / Vendor / winget / ambiguous / unsupported)
  -> fresh `winget export --source winget` PackageInventoryService + exact PackageTargetResolver
  -> high-confidence WingetSoftwareMapper (ID + manager + version + current-user scope)
  -> fixed WindowsApps AppExecLink -> Desktop App Installer identity
  -> WingetCapabilityPolicy -> existing software class policy -> WingetUninstallPolicy
  -> read-only process/service/winget-busy/global-transaction preflight
  -> immutable plan + expiring Preview + durable plan confirmation
  -> rebuild Package/Software/mapping/alias/policy/preflight evidence
  -> independent invariant review + short-lived immediate confirmation
  -> atomic pair consumption + mandatory pre-start audit
  -> ToolRegistry[software.uninstall.winget] + one-shot execution guard
  -> fixed argv, absolute alias, sanitized env, DEVNULL, shell=False
  -> process evidence (no kill, stop, elevation, restart or retry)
  -> fresh Package inventory + fresh Installed Software inventory
  -> exact-path lstat residual report + terminal transaction/audit
```

Package inventory and Installed Software inventory remain separate authorities. `winget export` is
used because localized `winget list` table text is not treated as a stable API. Export records are
accepted only from source name `winget` plus the exact Microsoft source identifier；Source URL 被忽略。
Execution eligibility still requires a unique current-user Software identity containing an exact
structured package-manager/Package ID/version link. This intentionally creates safe false negatives
when Windows metadata cannot prove the mapping.

The three uninstall mechanisms keep independent domain types, repositories, registries, adapters and
confirmations. Each repository reads the other additive transaction tables so only one MSI, Vendor or
winget transaction can be active. Winget's write guard changes `DISPATCHING` to `EXECUTING` inside the
same durable authorization check immediately before the adapter call.

`ValidatedWingetUninstallAction` deliberately has no argv field. The adapter alone generates the
finite argument tuple. App execution alias identity is reread at the adapter boundary, while Package,
Software, mapping, source, version, safety and runtime facts are reread before immediate confirmation.
Stopping monitoring leaves the child alive and reports `INTERRUPTED`; restart never redispatches.

## Stage 4D2B controlled Vendor execution boundary

Stage 4D2B is a second, separate one-tool write slice. It does not turn registry command text into a
generic process runner:

```text
chat/table selection (display query only)
  -> fresh SoftwareUninstallRouter (MSI / Vendor / ambiguous / unsupported)
  -> fresh SoftwareTargetResolver + Stage 4D1 capability and software policy
  -> VendorUninstallMetadataParser (parse only; raw string remains ephemeral)
  -> literal absolute-local VendorExecutableResolver
  -> Windows file identity + bounded SHA-256 + offline Authenticode + Publisher relation
  -> VendorArgumentPolicy + VendorUninstallerIdentity
  -> read-only process/service preflight
  -> immutable VendorUninstallPlan + expiring Preview + independent safety review
  -> durable plan confirmation
  -> rebuild every software/executable/argument/policy/preflight fact
  -> short-lived object-specific runtime confirmation
  -> atomic pair consumption + mandatory pre-start audit
  -> ToolRegistry[software.uninstall.vendor] + VendorUninstallExecutionGuard
  -> WindowsVendorUninstallPlatform(exact argv, explicit executable/cwd, sanitized env, shell=False)
  -> direct/descendant process observation (no signal, kill, UI automation or retry)
  -> fresh Installed Software inventory verification
  -> exact known-location lstat report + terminal transaction/audit
```

`VendorUninstallerIdentity` is the stable security boundary. It binds the source-qualified software
identity and registry-source digest to one exact executable observation (absolute path, volume/File
ID, size, creation/modification times, SHA-256, location relationship, signature and signer match),
the exact parsed argv tuple and the argument decision. Preview and runtime confirmation compare the
stable invariant digest, so replacing the file or changing metadata/arguments after Preview stops
before dispatch.

There are deliberately separate registries and repositories for MSI and Vendor execution, while
both repositories check the other's table to enforce one active uninstall globally. The Vendor
write guard changes `DISPATCHING` to `EXECUTING` in the same durable authorization check immediately
before the adapter call. No durable transaction or mandatory pre-start audit means no launch.

The process adapter observes the direct process and descendants. A normal process exit advances to
fresh inventory verification; it does not prove success. Cancellation after launch and the bounded
long-running threshold return an unknown state without terminating anything, keep the transaction
active as `MONITORING`, and defer final verification. On application restart, active dispatched work
becomes `INTERRUPTED`; confirmations expire and the executable is never redispatched.

Raw command text, raw arguments and full executable paths are excluded from durable confirmation and
ordinary audit payloads. Only digests, evidence categories, counts, process facts and verification
facts cross those boundaries. The GUI receives a safe Preview and leaves the vendor's native UI
entirely under user control.

## Stage 4D2A controlled MSI execution boundary

Stage 4D2A adds a new one-tool write slice without granting Stage 4D1 any execution authority:

```text
chat/table selection (untrusted display query)
  -> fresh SoftwareInventoryService + exact SoftwareTargetResolver
  -> UninstallCapabilityResolver (MSI + high confidence only)
  -> MsiProductValidator (strict ProductCode + msi.dll registration + metadata/context match)
  -> SoftwareUninstallSafetyPolicy -> SoftwareUninstallExecutionPolicy
  -> SoftwareExecutionPreflight (read-only process/service path evidence)
  -> MsiUninstallPlan + MsiUninstallPreview + independent safety review
  -> durable plan confirmation
  -> repeat every identity/capability/policy/preflight check
  -> fresh short-lived runtime confirmation
  -> atomic confirmation consumption + mandatory write-ahead audit
  -> ToolRegistry[software.uninstall.msi] + MsiUninstallExecutionGuard
  -> WindowsMsiUninstallPlatform(fixed msiexec argument array, shell=False)
  -> exit-code evidence + fresh registry/MSI inventory verification
  -> bounded lstat residual report + final transaction/audit
```

`ValidatedMsiProduct` is the only adapter input. It binds ProductCode, normalized Stage 4D1 identity,
metadata, capability, Windows Installer registration, scope, architecture and source anchor. There is
no executable path, raw command or arbitrary argument field. `MsiUninstallRepository` separately
persists the transaction and both confirmation capabilities; the audit store receives only digests,
decisions, counts, installer category/exit code and verification state.

At most one MSI transaction may be active. The write guard changes `DISPATCHING` to `EXECUTING` in
the same durable authorization check immediately before the platform call. A completed process is
verified rather than trusted. If the fixed monitor window elapses, the child is not killed: the
transaction remains `WAITING`, verification/residual inspection are deferred, and restart recovery
marks it `INTERRUPTED` without redispatch.

The PySide6 dialog uses three workers: preparation, immediate revalidation, and execution/monitoring.
The UI only resolves explicit confirmation choices through the orchestration service and never calls
the registry or Windows adapter directly. Cancellation before launch prevents process creation;
cancellation after launch is recorded but cannot terminate Windows Installer.

## Stage 4D1 zero-execution uninstall-analysis boundary

Stage 4D1 adds a read-only slice; it does not extend the write executor. Raw source records and
normalized domain records are separate. The Windows adapter reads fixed registry views and optional
structured package providers, then `SoftwareInventoryService` normalizes and deduplicates them. Raw
uninstall commands stay only in an ephemeral snapshot keyed by a stable, source-qualified identity
digest; GUI, audit and provider layers receive only safe projections.

```text
chat/selected software
  -> local intent + immutable R0 plan
  -> independent plan validator + plan confirmation
  -> software.inventory -> software.resolve -> software.inspect
  -> software.uninstall_capability -> fresh identity/metadata check
  -> deterministic policy + read-only impact correlations
  -> software.uninstall_preview -> independent Preview validation
  -> target acknowledgement -> STOP (no execution capability exists)
```

The dedicated registry contains exactly five tools. Each manifest is R0, read-only, cancellable,
has `RollbackLevel.NONE`, and forbids runtime execution confirmation. `SoftwareZeroExecutionGuard`
validates the allow-list and every result's `execution_performed=false`. The Qt dialog runs analysis
in workers and contains no uninstall worker or executable button.

Identity includes source, scope, architecture and source anchors. Resolution accepts exact identity
or exact supplied fields; substring results remain candidates. MSI evidence requires ProductCode and
WindowsInstaller metadata to agree. Vendor command lines are parsed with `CommandLineToArgvW` only
to expose sanitized structure; wrappers, relative/UNC/missing/non-EXE targets are unsupported.
Impact correlation is evidence, not a complete dependency graph.

## Stage 4C2 service startup-configuration boundary

Stage 4C2 is additive to Stage 4C1 and deliberately separates immutable service identity from
mutable startup configuration:

- `ServiceStableIdentity` binds ServiceName, service type, binary-path fingerprint and account;
- `ServiceStartupConfiguration` separately binds startup type and delayed-auto evidence;
- Stage 4C1 state controls revalidate both, while Stage 4C2 authorizes one explicit configuration
  transition without treating the intended source-to-target change as an identity mismatch.

The data flow is:

```text
fresh SCM observation + permission probe
  -> Stage 4C1 base policy + Stage 4C2 transition/impact policy
  -> DPAPI-encrypted backup, immediate decrypt/digest verification
  -> immutable R2 plan + Preview + independent safety review
  -> persisted PLAN confirmation
  -> fresh observation/permission/impact Preview
  -> short-lived RUNTIME confirmation
  -> persisted execution authorization
  -> one registered ChangeServiceConfig adapter
  -> read-back configuration/runtime verification
  -> change history + privacy-minimized audit
```

`domain/service_startup_actions.py` owns provider-neutral plans, Preview, backup references,
transactions and results. `safety/service_startup_*` owns default-deny classification and independent
cross-model review. `confirmation/service_startup_actions.py` owns two one-time bindings.
`persistence/service_startup_actions.py` keeps backup, transaction, confirmation and change-history
tables separate. `platform_support/windows/service_startup.py` exposes only Automatic, Manual and
verified restore; it has no generic configuration dictionary. `orchestration/service_startup_actions.py`
is the only coordinator, and the UI only starts workers and resolves explicit confirmations.

The write adapter sets only SCM start type and requires the current runtime state to remain unchanged.
Backup bytes are protected for the current Windows user and never enter a public tool request or audit.
Restore is another complete transaction: it first proves that the current configuration still equals
the Agent-written value, creates a new backup, obtains two new confirmations, writes one field and
verifies it. Each successful restore creates another change record, so a later reverse restore remains
possible under the same conflict checks. Startup transactions found active on restart become
`INTERRUPTED`; no transaction or confirmation is reconstructed for automatic continuation.

## Stage 4C1 controlled service-action boundary

Stage 4C1 keeps the Stage 3 service inventory as read-only evidence and adds a separate
service-management presentation. The GUI and chat can express only `START`, `STOP`, or
`RESTART` plus one target hint; the deterministic resolver obtains the execution identity
from a fresh local SCM inventory. DisplayName is presentation metadata. Only an exact unique
DisplayName may help resolve a row, after which ServiceName is the sole execution identity.

```text
GUI/chat hint
  -> ServiceTargetResolver (fresh exact ServiceName)
  -> ServiceSafetyPolicy + ServiceDependencyAnalyzer + permission handle probes
  -> ServiceActionPlanCompiler + ServicePreviewEngine
  -> ServiceActionSafetyValidator
  -> PLAN confirmation (plan/preview/identity/state/dependency/permission digests)
  -> fresh local revalidation
  -> short-lived RUNTIME confirmation
  -> ServiceActionRepository write-ahead state + mandatory audit
  -> ToolRegistry + ServiceExecutionGuard
  -> system.service.stop / system.service.start
  -> WindowsServiceControlPlatform (SCM API only)
  -> state/configuration postcondition verification + terminal audit
```

`RESTART` is composition, not a platform primitive. The persisted ordered steps are STOP and
START. After STOP reaches `STOPPED`, cancellation or a changed configuration prevents START;
otherwise START reopens the exact service and revalidates its immutable configuration identity.
A Stop-success/Start-failure outcome is durable `PARTIALLY_COMPLETED`, with the freshly queried
state shown to the user. The system never retries or resumes a service transaction after an
application restart.

The Windows adapter opens SCM/service handles with query rights and only the single required
`SERVICE_START` or `SERVICE_STOP` access. It uses `StartService` and `ControlService`, then
polls `QueryServiceStatusEx` with a bounded timeout, wait-hint/checkpoint awareness, and
cooperative cancellation. It does not expose configuration change, delete, pause, process
termination, shell, `sc.exe`, PowerShell, CMD, WMI, elevation, or dependent-service cascade.

Service write models, confirmations, persistence and audit are separate from Stage 3 query
models and from Stage 4A process controls. `ServiceExecutionGuard` requires matching ordered
tool/argument digests and consumed durable confirmations, so neither the UI nor a provider can
call a write tool directly. The UI runs inventory, Preview, revalidation and execution in Qt
workers; shutdown cancels future undispatched steps and waits through the bounded SCM timeout.

## Stage 4B startup-management boundary

```text
startup management page / explicit selected row
  -> WindowsStartupManagementPlatform.list_entries (fixed read-only sources)
  -> StartupTargetResolver (exact identity; ambiguity fails)
  -> StartupSafetyPolicy (default-deny classification)
  -> capture exact material -> DPAPI encrypted StartupBackupVault -> verify readback
  -> StartupActionPlan + StartupPreviewEngine + StartupActionSafetyValidator
  -> PLAN confirmation
  -> fresh source/identity/approval/publisher/path/backup revalidation
  -> new Preview -> short-lived RUNTIME confirmation
  -> SQLite consumed-capability guard -> ToolRegistry
       startup.disable OR startup.restore
  -> fixed Windows adapter mutation -> postcondition verification
  -> automatic inverse attempt on verification failure -> transaction/audit/UI
```

The domain, safety, confirmation, persistence and orchestration layers do not depend on Qt.
The platform protocol exposes finite inventory, inspection, backup, disable, restore and
verification methods; it exposes neither an arbitrary registry path nor a command executor.
The Windows implementation reads six compile-time sources but writes only native-view HKCU
Run and the current-user Startup folder. It treats StartupApproved as read-only concurrent
state evidence because its binary format is not used as a write contract.

Registry value bytes/type and shortcut bytes are captured before mutation, encrypted with
current-user DPAPI, persisted independently from audit, read back and digest-verified. HKCU
Run uses `RegOpenKeyTransactedW` plus transaction commit/rollback. Startup-folder disabling
moves one unchanged `.lnk` to an Agent-owned path on the same volume without overwrite.
Restore requires the Agent disabled index, the exact backup, an empty original location and
unchanged disabled material. FULL rollback is conditional on those checked preconditions.

All writes are R2 single-object operations. Plan and runtime confirmations bind action,
transaction, operation, plan and Preview digests, exact startup identity, current-state
digest, backup ID/digest and expiry. Restart never resumes a mutation automatically. The UI
uses finite workers, initializes COM per worker and waits during shutdown; it never invokes a
platform or registered tool directly.

## Stage 4A controlled process-action boundary

```text
chat / selected Stage 3 process row
  -> ProcessTargetResolver (fresh local PID/name/application-group resolution)
  -> ProcessActionPlanCompiler (graceful-first finite action)
  -> ProcessPreviewEngine + ProcessSafetyPolicy (default-deny classification)
  -> ProcessActionSafetyValidator (exact registered manifest/schema/risk/bounds)
  -> PLAN confirmation
  -> fresh identity/group/policy revalidation -> new Preview
  -> short-lived RUNTIME confirmation
  -> SQLite consumed-capability check -> ToolRegistry
       system.process.request_exit OR system.process.force_terminate
  -> WindowsProcessManagementPlatform (checked handle / WM_CLOSE / TerminateProcess)
  -> WaitForSingleObject verification -> transaction + audit + GUI result
```

`domain.process_actions` has no Qt, model, or Windows dependency. `ProcessIdentity` binds
PID, creation time, executable path, owner SID and session; its digest is carried by plan,
Preview, both confirmations, exact tool arguments and audit. Name matching and application
grouping are local and fail on ambiguity. The UI never turns a stale Stage 3 row into
authority: it supplies a selected PID only as a query, and the resolver rereads all identity
and protection metadata in a worker.

`ProcessSafetyPolicy` is deterministic and default-deny. It blocks Agent PIDs, system SIDs,
other owners/sessions, critical names/flags, non-NONE process protection, known security
processes, active SCM service PIDs and Windows-directory executables. A graceful group is
supported when at least one member owns a top-level window; helper members remain visible
and verified. Unknown/inaccessible metadata is omitted or blocked, never guessed.

The Windows adapter uses query-limited process handles, `GetProcessTimes`,
`QueryFullProcessImageNameW`, token owner SID, session ID, `IsProcessCritical`, process
protection information, top-level-window enumeration, SCM query handles, `PostMessageW`,
`TerminateProcess`, and `WaitForSingleObject`. It exposes no command string, shell, elevation,
service-control, registry-write or arbitrary process primitive. Graceful group requests run
concurrently under a single configured timeout instead of multiplying it per helper process.

Normal exit and force termination are separate immutable plans and separate transaction IDs.
A graceful timeout or unsupported window can only expose a button that creates a fresh force
Preview from currently remaining application members. It cannot reuse either confirmation.
Transactions persist exact digests and lifecycle states. Restart changes active mutations to
`INTERRUPTED`; nothing auto-resumes. Because process exit cannot restore unsaved state,
rollback is always `NONE`; starting an executable again is not Undo.

## Stage 3 read-only system diagnostics

Stage 3 follows the same plan-first boundaries without reusing file-operation authority:

```text
chat / system dashboard
  -> DiagnosticPlanCompiler (finite local intent and bounded parameters)
  -> DiagnosticSafetyValidator (registered R0 manifests and exact schemas)
  -> DiagnosticConfirmationService (plan ID + canonical digest + expiry)
  -> DiagnosticOrchestrator -> ToolRegistry
       system.info / cpu / memory / disks / processes / startup / services / software
  -> WindowsSystemDiagnosticsPlatform (query-only APIs)
  -> SystemSnapshotService (explicit partial failures)
  -> DiagnosticEngine (published thresholds and deterministic evidence)
  -> DiagnosticReport / dashboard / minimized audit
```

`domain.system_diagnostics` has no Qt, OpenAI, pywin32, or `psutil` dependency. The eight
tool classes validate schemas and delegate to `SystemDiagnosticsPlatform`, allowing core
tests to use a deterministic fake. Windows implementation uses `psutil`, read-only `winreg`,
`GetDriveTypeW`, and query-only Service Control Manager handles. It does not spawn a
subprocess and does not use PowerShell, CMD, WMI, `Win32_Product`, service control, process
termination, registry writes, or uninstall APIs.

CPU and process resource usage are sampled across a bounded interval. Up to four independent
R0 collectors run concurrently after write-ahead audit so sampling waits can overlap query
latency; results and audit completion records are restored to plan order. Each result has its
own status, item count, warnings, duration, and sanitized error, so one failed collector does
not erase successful independent results. The Qt worker owns a cooperative cancellation token.
Stage 3 currently does not cache inventories: every explicit execution refreshes all selected
collectors and the dashboard labels the new snapshot time. This avoids presenting stale data
as current until a persistent cache with explicit refresh/invalidation semantics is designed.

The optional model has two narrow contracts. Planning sends only the user goal and finite
intent/collector allow-lists; local compilation remains authoritative. Explanation sends only
finding code/category/severity/title and evidence field names—never measurements, paths,
process/service/software identities, startup commands, or inventory records. Both network
calls use the existing digest-bound external-data confirmation.

## Stage 2B R2 Recycle Bin boundary

Stage 2B is deliberately parallel to, rather than hidden inside, the Stage 2A R1 service:

`explicit selection -> TrashPlanCompiler -> TrashSafetyValidator -> TrashPreviewEngine ->`
`PLAN confirmation -> fresh tree revalidation -> RUNTIME confirmation -> PREPARED recovery ->`
`ToolRegistry/transaction guard -> Windows IFileOperation -> callback verification -> audit`

`TrashPlan` and `TrashPreview` cannot claim FULL rollback. `TrashPathPolicy` composes the
ordinary authorization policy with system/application-data exclusions. Complete directory
snapshots hash relative path, file ID, kind, size, timestamps, and attributes; a changed
tree invalidates confirmation. The provider layer is absent from this data flow and cannot
select targets.

The shared transaction journal gains additive confirmation and recovery tables instead of
changing existing Stage 2A rows in place. PLAN and RUNTIME proof is checked again by the
registry write guard. `TrashRecoveryRecord` is separate from `UndoRecord`; restart turns
an in-flight trash item into UNKNOWN and never automatically resumes it.

`WindowsRecycleBinPlatform` runs one `IFileOperation` in a worker-thread STA per item. It
sets recycle/undo/early-failure flags and implements `IFileOperationProgressSink`.
Success requires a zero operation HRESULT, no abort, a recycle-capable transfer flag, a
non-null newly created Recycle Bin Shell item, and absence of the original path. No legacy
Shell API, command line, `unlink`, recursive deletion, or permanent fallback exists.

## Objective and boundary

Stage 2A preserves the complete Stage 1 read-only slice and adds the first narrow R1
write slice. Core logic still runs without Qt and without a provider. Only ordinary
directory creation, same-volume move, same-parent finite-rule rename, and verified
rollback are executable. Overwrite, cross-volume copy/delete, recycle bin, permanent
deletion, arbitrary commands, system mutation, and elevation remain unavailable.

## Layer ownership

```text
Chat / FileAnalysisTab / headless caller
                 |
       FileAnalysisPlanner (optional LLM intent)
                 |
       FileAnalysisPlanCompiler (deterministic)
                 |
       FileAnalysisSafetyValidator
                 |
     digest-bound plan confirmation
                 |
       FileAnalysisOrchestrator
                 |
            ToolRegistry
       /          |           \
 file.scan   large/inactive   duplicates
       \          |           /
       AnalysisResultRepository
                 |
      paged GUI / CSV-JSON export
                 |
 aggregate-only explanation (optional LLM)
```

- `domain` owns immutable Pydantic intent, plan, progress, metadata, candidate,
  duplicate, confidence, report, risk, and rollback models.
- `authorization` owns explicit authorized/favorite roots and custom forbidden
  roots. Plans refer to opaque root IDs; only deterministic code resolves paths.
- `providers` is replaceable. OpenAI is one adapter and does not appear in domain,
  scanner, analyzer, or safety code.
- `safety` canonicalizes local paths, rejects protected and redirected paths, and
  independently compares the semantic plan with every executable step.
- `confirmation` binds plan or external payload digests to purpose and expiry.
- `tools` is the only execution allow-list. There is no shell or dynamic-code tool.
- `orchestration` repeats review and confirmation before execution, verifies typed
  outputs and terminal reports, and emits plan/tool/result audit events.
- `persistence` stores authorizations, audit events, and bounded analysis batches in
  SQLite. Candidate rows are paged; all discovered metadata is not kept in memory.
- `ui` only changes presentation state and starts `QRunnable` workers. It never
  executes a scanner or analyzer directly.
- `platform_support.windows` contains mapped-drive/atime checks, Explorer selection,
  single-instance behavior, and checked Win32 identity/move/directory primitives.

## Stage 2A write boundary

```text
Natural language / checked Stage 1 rows / manual selection
                 |
    FileOperationPlanner (optional, intent only)
                 |
 FileOperationSourceResolver + PlanCompiler (local paths)
                 |
       FileOperationSafetyValidator
                 |
     OperationPreviewEngine (read-only live state)
                 |
       plan + preview digest confirmation
                 |
 FileOperationService -> OperationRepository write-ahead journal
                 |
 TransactionExecutor -> ToolRegistry + TransactionExecutionGuard
                 |
 file.mkdir / file.move / file.rename -> Win32 -> verify
                 |
        available Undo + audit + terminal report
                 |
 RollbackManager -> reverse live Preview -> separate confirmation
                 |
 registered reverse tools -> verify -> rollback terminal state
```

The model sees only goal text, non-sensitive root labels, opaque root IDs, and finite
enums. It cannot provide concrete paths, a command, Python code, risk, confirmation,
or execution policy. `FileOperationSourceResolver` discovers literal extensions only
inside those IDs. The compiler observes Windows file identity and computes every final
path, including modified-year directories and finite rename results.

Preview is a real filesystem snapshot. It classifies every item `READY`, `CONFLICT`,
or `BLOCKED`, counts directory-tree impact without following reparse points, checks
target presence and nearest-parent volume, and reports truthful FULL rollback counts.
Conflicts stay visible but are persisted as `SKIPPED`; only READY arguments are eligible.

`OperationConfirmation` binds transaction ID, plan ID/digest, preview ID/digest, item
counts, approval time, and expiry. Approval is held in memory, consumed once, and lost
on restart. `OperationRepository` separately reserves the exact tool and argument digest.
The registry requires both the one-time approval workflow and a durable RUNNING item
capability before invoking any write tool.

Immediately before mutation, tools repeat lexical/canonical scope checks, reject any
reparse component, re-open a handle to compare Volume Serial Number, 128-bit File ID,
size/timestamps/attributes, recheck target absence, and reject a volume change. The
Windows adapter uses `MoveFileExW` with only write-through; it does not request replace,
copy-across-volume, delayed reboot, or shell behavior.

## Stage 2A transaction and recovery model

Transactions use explicit states: `PREVIEWED → AWAITING_CONFIRMATION → CONFIRMED →
RUNNING → COMPLETED/PARTIALLY_COMPLETED/FAILED/CANCELLED`. Rollback uses `ROLLING_BACK →
ROLLED_BACK/PARTIALLY_ROLLED_BACK/ROLLBACK_FAILED`. Each item independently records
`PENDING/RUNNING/COMPLETED/FAILED/SKIPPED` and rollback states.

Before every write, the repository atomically changes the item to RUNNING and stores a
checksum-protected PREPARED Undo record. After Win32 returns, deterministic verification
must pass before the item becomes COMPLETED and Undo becomes AVAILABLE. Unexpected error
stops all later PENDING items. Cancellation means “stop future items”; it never kills an
active filesystem call.

At startup, stale RUNNING/ROLLING_BACK transactions become `INTERRUPTED` and are shown
to the user; they are never resumed. PREVIEWED/AWAITING_CONFIRMATION/CONFIRMED transactions
become `CANCELLED` because their memory-only authorization cannot survive restart.

Rollback reads only persisted Undo records, orders them by descending original sequence,
and evaluates live identity, modification, restored-path conflicts, authorization, and
created-directory contents. It does not ask a model to guess reverse paths. A directory
created by the transaction may be removed only after earlier reverse steps vacate its
managed children and no unmanaged entry remains. Rollback receives a new digest-bound
confirmation and traverses the same registry/transaction guard/verification boundaries.

## Data flow

1. The user explicitly adds a local authorized root and optional forbidden roots.
2. Manual controls or an LLM produce `FileAnalysisIntentDraft`. The provider sees
   the goal, labels, opaque root IDs, allowed analyses, and registered tool names;
   it does not see paths or files.
3. `FileAnalysisPlanCompiler` resolves IDs locally, rejects overlapping roots,
   divides global file/time limits, derives exclusions, and creates only R0 steps.
4. `FileAnalysisSafetyValidator` combines the generic reviewer with Stage 1 checks:
   exact scope, session ID, thresholds, match mode, analysis set, and read-only impact.
5. Confirmation binds the complete `TaskPlan` digest. UI control changes discard it.
6. `file.scan` revalidates each root and streams metadata batches to SQLite. Progress,
   cancellation, timeout, per-object error continuation, and maximum count are explicit.
7. Selected analyzers page stored metadata. Duplicate analysis alone reads content,
   using size → quick hash → SHA-256 → optional byte comparison with identity checks.
8. SQL calculates ALL/ANY membership and serves validated filtering, sorting, paging,
   category summaries, and streaming export.
9. Optional explanation sends only typed aggregate totals after a second external-data
   confirmation. Provider observations cannot contain digits; measured numbers are
   rendered by deterministic code.

## Persistence and recovery

The three SQLAlchemy subsystems share the application SQLite path but have isolated
declarative bases. Foreign keys, WAL, full synchronous writes, and bounded queries are
configured centrally. A stale `RUNNING` analysis session is application-owned temporary
data and is removed at the next initialization. Corrupt audit storage fails closed.

Authorized-path changes produce FULL inverse records. Report export is R1 and uses
exclusive creation; existing files are never overwritten. Export cleanup is deliberately
manual because Stage 1 never deletes even an incomplete report. User-file analysis is R0
and truthfully declares rollback `NONE` because it changes nothing.

## Extension rules

New providers implement `LLMProvider`. New tools require strict input/output models,
a complete `ToolManifest`, deterministic implementation, cancellation/bounds, safety
validation, audit, and tests. A future write tool additionally needs `OperationCommand`,
a truthful `UndoRecord`, conflict rules, verification, and the correct confirmation tier.
R2/R3 tools remain unregistered in Stage 2A. Stage 2B recycle-bin work requires a
separate R2 design and is not implied by the rollback-only empty-directory primitive.
## Stage 4D2C2 MSIX boundary

`WindowsMsixPackagePlatform` is the only WinRT boundary. Inventory uses PackageManager for the empty
user SID (current user) and retains Raw records before normalization. Stable `MsixFamilyIdentity` is
separate from version-sensitive `MsixInstanceIdentity`; display names never authorize a write.

The execution chain is `inventory → exact resolution → type classifier → dependency snapshot →
software safety/scope policy → read-only preflight → Preview → plan confirmation → fresh revalidation
→ immediate confirmation → durable one-shot guard → software.uninstall.msix → fresh inventory →
verification → exact-path-only residual report → audit`. UI work runs in Qt workers and reaches the
adapter only through the registered tool. MSI, Vendor, winget and MSIX repositories share one global
active-uninstall exclusion.

The adapter exposes neither arbitrary PackageManager operations nor command execution. Its sole write
is current-user `remove_package_with_options_async` with the fixed WinRT equivalent of
`PreserveRoamableApplicationData`. All-users and Provisioned APIs have no production call path.
