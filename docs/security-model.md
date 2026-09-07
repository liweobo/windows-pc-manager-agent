# Security model

## Stage 5E global coordination invariants

- The task plan confirms only exact R0 coordination nodes and always has `domain_write_authorized=false`.
- No task, graph, checkpoint, attention item, Agent/model message or domain preparation can authorize execution.
- Exactly eleven high-level domains are registered; their interface has no execute or confirm method.
- Every write-capable effect retains the original domain's Fresh resolution, Preview, confirmation and verifier.
- Restart/revision invalidate old task consent. Recovery may reconcile but `action_replayed` is schema-forbidden.
- Tray/notification actions navigate only. Voice/chat text cannot approve task or domain confirmation.
- `FULL_UNATTENDED`, Confirm All, global elevation, generic tool/command routing and global Undo do not exist.
- Durable task data is IDs, safe labels, digests, fixed codes, counts and opaque references; raw goal/domain bodies,
  credentials, confirmation/Broker secrets and model reasoning are excluded.

## Stage 5D Agent, Context and Memory boundary

Agent capability is default-deny and bound to a runtime-created identity, manifest SHA-256 and Prompt version.
All Agent manifests structurally deny action execution and confirmation requests; only listed existing R0 tools may
be proposed. Delegations are issuer-bound, expiring, single-use, capability-subset and task-goal confined. Message
payload identity and trust labels are validated, and no Agent message may claim system trust or act as confirmation.

Context is selected item by item. Credential/secret classification and known secret assignments fail closed;
document/web/model taint survives summarization and cross-Agent messages. Memory accepts only closed low-risk keys,
never authorization or unstable execution identity. Explicit safe writes require confirmation; inferred preferences
remain ephemeral. Agent cancellation only stops future coordination. Original domain safety, confirmation,
privilege and verification remain authoritative. See [Context](context-governance.md) and [Memory](memory-model.md).

## Stage 5C untrusted web boundary

Every remote URL, redirect, title, text, accessible name, href, filename and error is untrusted data. The Main
process applies exact HTTP(S)/standard-port/credential-free URL parsing, IDNA normalization and fresh all-
public A/AAAA resolution; the Worker repeats checks for intercepted requests and permits only GET/HEAD while
Agent-controlled. Application checks do not claim OS network isolation.

Only session/page/navigation-bound accessibility references and the finite action enum may execute. Generic
click/selectors/scripts/CDP/coordinates, transactions, account/messages/posts, credentials, uploads and private
network access are structurally absent or blocked. Prompt-injection detection is advisory and never adds an
action. Every plan confirmation is durable, expiring and single-use; page generation drift, takeover, cancel,
close, restart, audit/SQLite failure or DNS/origin change fails closed.

Downloads are R1 and single-file: finite extension/MIME/magic checks, default 50 MiB, non-symlink staging,
SHA-256 and exclusive no-overwrite commit. Recovery is FULL only if the committed file remains byte-identical;
it moves rather than deletes. There is no malware-safety claim. Audit contains only origins, digests, enums,
counts and file hash/size/type—not page bodies, query values, local paths, names, cookies or passwords.
See [the complete safety contract](browser-automation-model.md).

## Stage 5B untrusted speech boundary

Explicit visible user activation, standard-user token check, fixed-format input, bounded recording,
mandatory pre-capture audit, exact single-use outbound consent, final review, atomic request consumption
and unchanged domain safety checks are separate gates. Speech recognition never authenticates a person.
ALL business approvals remain visual. Fresh identity, object scope, risk, UAC and recovery remain domain-owned.
Background/inactive UI and cancellation stop native audio before journal operations. Cancelled or stale
callbacks cannot route text or restart playback. Restart interrupts pending input/disclosures.

Known-secret detection blocks text but is not a complete privacy filter; untranscribed raw audio cannot
be inspected for secrets before a cloud upload. The upload dialog states the actual endpoint/model,
duration/bytes, potential fees and cloud retention caveat. API secrets, PCM and transcript bodies are not
journal fields. Shared request routing supplies canonical voice goals to legacy audited planners.
Safe speech uses aggregate facts only, never arbitrary UI/document/chat text or raw provider errors.
Speech dispatch refuses OpenAI/httpx/httpcore DEBUG logging because SDK request-option logging can
contain multipart audio. It does not alter the user's global logging configuration.

## Stage 5A Office controls

Exact READ/OUTPUT UUID grants, finite structured operations, full file identity/hash revalidation,
reparse/network/protected-path exclusion, format preflight, resource-limited parsing, explicit Preview,
verified current-user encrypted backup, atomic single-use approvals and no-replace commits form an
independent security boundary. Existing document replacement requires R2 confirmations. R1 new files
support conditional Undo by retaining the unchanged output, never deleting it. Restore refuses newer files.
No macros/VBA/COM/shell, Office process control, elevation, forced overwrite or automatic resume exists.
Model disclosure is separate and source-bounded; audit excludes bodies, Diff, keys and provider payloads.
See [detailed policies and real limitations](office-automation-model.md), including the non-atomic rename gap.

## Stage 4E3 review controls

- Source report and recommendation UUIDs resolve locally under a canonical digest and bounded expiry.
  Text, paths, PIDs, commands, arbitrary tool names, administrator flags and confirmation tokens cannot
  become E3 input authority. Legacy untyped recommendations default to manual review.
- A sealed finite capability registry prepares navigation only. Services are REVIEW_ONLY. A handoff is
  single-use, time-limited and domain-bound, never a domain Preview or execution authorization.
- Existing domains repeat target discovery/selection, policy, Fresh Preview, risk, confirmations and
  privilege decisions. A blocked object cannot be made safe by clicking an optimization checkbox.
- Sessions serialize reviews, audit aggregate identifiers/digests only, fail on journal corruption and
  invalidate source context after domain outcomes. Cancellation/restart never dispatches a next action,
  kills an external uninstaller, retries, or performs automatic recovery.
- Only a transaction created after handoff may supply a result. The read-only receipt reader checks
  immutable plan binding, consumed domain confirmation lineage and domain-specific verification;
  partial/unverified results remain distinct. It cannot execute or sign approval.
- Existing Stage 4X can only be invoked by its original business workflow. E3 adds no Broker action and
  does not aggregate a privileged result as verified. All original seven-action restrictions remain.

The full [routing matrix and recovery limits](optimization-action-routing.md) are part of this boundary.

## Stage 4E2 controlled-cleanup controls

- Old Stage 4E1 plans, confirmations, reports, candidate IDs and selections grant no mutation authority.
  Reports live only in a bounded session TTL store; restart/clear forces a new R0 analysis.
- Direct sources are a finite current-user known-root map. Only exact children are considered; roots,
  siblings, parent widening, free-form paths, Stage 1 personal files and Stage 4D3 residuals cannot enter
  the direct writer.
- Fresh checks independently bind file identity, complete bounded material digest, deterministic category,
  protection signals, recent metadata, ordinary delete access, active installer status and exact-volume
  Recycle Bin capability. Any missing or changed evidence blocks regardless of confirmation.
- The GUI defaults every Fresh item to unchecked and has no Select All. Only ELIGIBLE rows can be selected;
  mixed eligible/blocked selections fail as one batch in the deterministic builder.
- R2/R2_HIGH_IMPACT plans require durable plan and runtime confirmations. Both bind plan/Preview/item-set,
  identity/material/classification/protection/eligibility/adapter/recovery/risk/count digests, expire and
  are consumed once atomically before mutation.
- The item writer accepts references only. The guard resolves SQLite state and reserves exactly one call;
  final identity/tree TOCTOU checks precede the shared Recycle Bin primitive. Failure stops later items.
- Ordinary cleanup never claims permanent deletion or freed disk space. Verified results mean the original
  identity disappeared and Shell evidence identifies a Recycle Bin item; recovery is truthful MANUAL.
- Recycle Bin emptying has a separate exact-volume plan, Preview and confirmations, always
  R2_HIGH_IMPACT/recovery NONE. Aggregate and namespace inventories must remain identical. No null/all-drive
  call, fallback, retry or recovery record exists.
- Main remains a standard-user process. No Broker, UAC, PowerShell/CMD, generic executable, registry write,
  process/service control, unlock, uninstall or permanent-delete API is reachable.
- Audit stores IDs, digests, categories, counts, bytes, result and recovery truth. Paths are hashed and file
  contents/names are excluded. Untrusted filenames are local display only.

## Stage 4E1 zero-modification controls

- The Stage 4E1 registry contains exactly five R0, read-only, rollback-NONE tools. No existing writer or
  Broker is registered or injected.
- Each confirmed plan is a canonical dependency-complete subset with the smallest useful collector set;
  unrelated CPU/service/cache sources are not read merely because they exist in the registry.
- Plans require one digest-bound plan confirmation, declare zero system changes and cannot request runtime
  confirmation or elevation. Confirmation authorizes only current analysis.
- `CleanupCandidate`, finding, recommendation and report validators reject executable state. A dedicated
  authority guard rejects any Stage 4E1 report, selection or candidate ID offered to a write workflow.
- Personal storage is readable only through current Stage 1 authorization IDs. Known cleanup locations use
  a finite metadata-only policy. Roots are checked again at execution; traversal, network/unsupported,
  other-user, sensitive and reparse paths fail closed.
- Browser passwords, cookies, sessions, history and complete profiles are never read. Dumps, logs,
  databases, configuration and user documents are not opened.
- Windows Update, Delivery Optimization, WinSxS and Installer Cache are not estimated from raw directory
  size. Missing reliable evidence becomes protected or unavailable.
- Access failures are partial results, not zero size. Audit stores aggregate counts/bytes only and redacts
  the free-form request because it may contain local paths.
- Export is a separate user-selected report creation, uses `open("x")`, refuses network/existing targets
  and does not grant cleanup authority.

## Stage 4X3 privileged capability controls

- Real execution requires both an immutable R3 manifest and a concrete typed handler. The allowlist is
  exactly service Start/Stop, service startup change/restore, HKLM Run disable/restore, and machine MSI.
- Every request binds protocol version 2, action Schema version, safety-policy version, manifest digest,
  source transaction, exact target and state, Plan/Preview, two confirmations, caller and expiry.
- Safety runs before privilege and again in Main and Broker. `Safety BLOCK + Administrator` remains BLOCK;
  AccessDenied alone is insufficient evidence. Broker integrity must be exactly HIGH, never SYSTEM.
- Service startup permits only non-delayed Automatic ↔ Manual and must preserve runtime state. Restore needs
  the Agent-owned encrypted backup and unchanged Agent-written configuration.
- HKLM startup permits only one exact ordinary third-party Run value in an explicit 32/64 view. Backup,
  identity, view and absent/present state are checked again immediately before a transacted mutation.
- Machine MSI needs complete fresh software and Windows Installer identity, protected-class approval,
  process/service preflight and global uninstall exclusion. It uses fixed `msiexec` arguments,
  `shell=False`, a sanitized environment and no process/service/reboot/retry action.
- Typed result evidence must match its action. Broker verification is followed by independent Main
  readback. Uncertainty, persistence/audit failure, UAC cancellation, replay or drift never retries.
- Payloads and audit contain no raw uninstall string, command, script, arbitrary arguments, registry value
  bytes, credentials, HMAC material, nonce or provider key.

## Stage 4X2 elevated Broker controls

- **Main stays standard:** an elevated Main Agent is rejected before composition. UAC applies only to the
  independent one-shot Broker, never to the desktop process.
- **Narrow entry:** only a Stage 4C1-safe exact Start/Stop whose complete permission evidence says ordinary
  rights are insufficient may enter. Safety/dependency/identity/state blocks cannot be overridden.
- **Binary trust:** exact absolute non-reparse EXE, adjacent `asInvoker` manifest, SHA-256 and file identity
  are inspected before UAC. Production also requires a trusted install root, valid Authenticode and pinned
  signer identity; missing release signing means `NOT_READY`.
- **Opaque bootstrap:** `runas` receives only Broker/rendezvous/Agent identifiers, protocol version and
  expected caller PID. It never receives a service name, request body, command, path to data or arguments.
- **OS endpoint trust:** the pipe DACL grants the exact current user, denies remote clients and uses the
  first-instance flag. Broker impersonation reads the actual client token; both sides bind SID, session,
  PID, process creation, image/hash, Broker/Agent IDs and versions.
- **Authenticated finite transport:** four-byte bounded framing, strict JSON, duplicate/non-finite rejection,
  fixed six-message ordering, challenges, transcript digest, 30-second session key and HMAC on every
  post-handshake frame prevent message substitution and replay inside the established channel.
- **Consume before mutation:** the Broker validates persistent Plan/Preview/confirmations, request expiry,
  allow-list, caller and Fresh service evidence, writes mandatory audit, atomically consumes authority,
  repeats final TOCTOU checks and only then dispatches one SCM Start or Stop.
- **Truthful completion:** exact Broker readback and an independent standard-user Main readback are both
  required. Cancellation, timeout, disconnect, mismatch and audit/storage failure are terminal and never
  retried automatically.
- **Bounded natural exit:** Main waits for the one-shot Broker exit code after one result. Timeout is
  `COMPLETED_UNVERIFIED`; Main closes only its handle and does not terminate or relaunch the Broker.
- **Small Broker:** no GUI, provider, model, shell, script, generic process launcher, arbitrary executable,
  service configuration, installer or registry adapter is packaged or registered.

The session key is shared only after OS-derived peer checks on the private local pipe. Same-user process
compromise and administrator/kernel compromise remain outside this boundary; denial of service by another
same-user process is handled by failing closed, not by broadening access or retrying.

## Stage 4X1 privileged protocol controls

- **No real privilege boundary:** Stage 4X1 does not elevate, call UAC, create an admin process or touch
  SCM/registry/MSI machine state. `mock` modifies only an injected `FakePrivilegedSystemState`.
- **Safety before privilege:** permission routing receives a completed deterministic safety result.
  Safety-blocked input remains `BLOCKED`; SYSTEM/TrustedInstaller is `UNSUPPORTED`; incomplete or generic
  access-denied evidence is `UNKNOWN`.
- **Finite language:** seven action types have separate strict payloads. Only service Start/Stop are in
  the Mock registry. There is no generic command runner or caller-controlled executable/arguments.
- **Exact authorization:** Plan and immediate confirmations bind action, canonical Plan/Preview,
  payload, target, object summary, R3 risk, Administrator requirement and expiry. A request additionally
  binds caller context, Agent instance, nonce and protocol version.
- **Integrity and parsing:** the Broker enforces a byte limit before parsing, strict UTF-8 JSON, duplicate
  key rejection, exact protocol version, Pydantic `extra=forbid`, lowercase SHA-256 fields, explicit UTC
  and canonical sorted JSON before verifying HMAC-SHA-256.
- **Replay and crash safety:** request digest and hashed nonce are unique; a SQLite transaction consumes
  both approvals and the request once. Concurrent losers, expired requests and restart recovery cannot
  retry or resume.
- **Fresh Broker checks:** allow-list, authenticated caller, durable bindings, service identity,
  state/config/dependencies, safety, risk, privilege and final TOCTOU checks all run again locally.
- **Mandatory audit:** authorization, Broker validation, Mock execution reachability and verification are
  separate events. Missing required audit or persistence denies progress. Audit stores IDs and digests,
  not payloads, secrets, raw nonces or HMAC values.

The process-local HMAC key proves implementation mechanics only. It is not claimed to authenticate across
a real standard-user/elevated process boundary; that residual design risk is deferred to a future stage.

## Stage 4D4 residual cleanup policy

Stage 4D4 uses default denial. HIGH ownership proves only that a path probably belongs to an uninstalled
application; it does not prove the data is safe to remove. Eligibility is recomputed locally from fresh
identity, complete bounded material metadata, classification, protection, path safety, post-uninstall
activity and recovery capability.

| Classification | Ownership required | Protection required | Additional V1 conditions | Execution | Risk |
|---|---|---|---|---|---|
| PROGRAM_RESIDUAL | HIGH | NONE/CAUTION | Exact original install/equally strong path; not shared/recent/reparse; full snapshot; Recycle Bin available | Conditional | R2/R2_HIGH_IMPACT |
| CACHE | HIGH | NONE/CAUTION | Exact app-specific install/known-data evidence; no user-data descendant | Conditional | R2/R2_HIGH_IMPACT |
| LOG | HIGH | NONE/CAUTION | Exact app-specific evidence; not a user library/shared log root | Conditional | R2/R2_HIGH_IMPACT |
| SHORTCUT | HIGH | CAUTION | Exact shortcut path and target captured before uninstall; target now absent | Conditional | R2/R2_HIGH_IMPACT |
| TEMPORARY_DATA | Any | Any | Deferred in V1 because temporary roots may be shared | Block | — |
| CONFIGURATION | Any | Any | Dedicated future purge design required | Block | — |
| USER_DATA / DATABASE | Any | Any | Residual cleanup is never a user-data deletion shortcut | Block | — |
| PLUGIN_OR_EXTENSION / LICENSE_DATA / APPLICATION_STATE | Any | Any | May contain separately installed or valuable state | Block | — |
| PACKAGE_USER_DATA | Any | Any | Includes MSIX LocalState/RoamingState/Settings | Block | — |
| UNKNOWN | Any | Any | Missing evidence is not guessed | Block | — |

Independent hard blockers include MEDIUM/LOW/UNKNOWN ownership, PROTECTED/STRONGLY_PROTECTED/UNKNOWN
protection, shared or system location, post-uninstall modification, another user, sensitive/protected root,
network/unsupported/removable capability, symlink/junction/reparse, incomplete snapshot, budget excess and
identity/material change. A user's “force” wording cannot override them.

The only action is `MOVE_TO_RECYCLE_BIN`. The manifest advertises maximum R2_HIGH_IMPACT and accepts only
R2/R2_HIGH_IMPACT plans. Both confirmations bind plan, Preview, exact item set, identity, material,
classification, ownership/protection, eligibility/path/activity, counts/bytes, risk and recovery capability.
They expire and are atomically single-use. Any change requires a new selection, plan and both confirmations.

Default hard limits are 20 selected items, 10,000 contained objects and 50 GiB. A batch remains R2 only at
or below 5 selected items, 100 contained objects, 1 GiB total and 512 MiB for every single selected item;
otherwise it is R2_HIGH_IMPACT while still inside the hard limits. Exceeding a hard limit blocks the whole
batch. Validated local configuration may change these values, and the exact effective values enter Preview.

The final write boundary accepts no path or command. It resolves an internal item reference after durable
authorization, writes recovery evidence and mandatory audit, repeats identity/tree checks and calls the
Windows Recycle Bin once. Failure never falls back to permanent deletion. Verification requires original
identity disappearance plus a non-aborted, successful, explicitly recycled and VERIFIED_RECYCLED Shell
result with a Recycle Bin item identifier.

Cancellation before dispatch terminates the transaction; during a batch it stops future items only. A crash
marks active work `INTERRUPTED` and expires approvals. Automatic restore is intentionally absent; recovery is
MANUAL, so occupied original paths must be handled by Windows/the user and are never overwritten by Agent code.

## Stage 4D3 residual-analysis controls

- Exactly three tools exist: `software.residuals.analyze`, `software.residuals.report` and
  `software.residuals.inspect`. All are R0, read-only, rollback `NONE`, cancellable where relevant,
  and have no write guard or cleanup counterpart.
- Analysis requires an eligible durable Agent uninstall context. Verified removal is preferred;
  `completed_unverified` is allowed only with a persistent lower-confidence warning. Failed,
  interrupted, missing or pathless contexts are blocked.
- Scope is generated locally from no more than 16 exact known paths. Relative/traversal/UNC/network,
  ambiguous, protected, other-user and sensitive roots are rejected before filesystem access.
- Every root is revalidated immediately before enumeration. Directory identities are checked again;
  symlink, junction and other reparse entries are reported but never traversed.
- Collectors use `lstat`/`scandir` metadata only. They do not open file, database, configuration, log,
  shortcut payload or Package user-data contents and do not read or write registry data.
- Name similarity is LOW evidence only. Exact pre-uninstall location, Package Family mapping and a
  captured shortcut target are structured evidence, but ownership never lowers protection.
- User libraries, Downloads, Saved Games, project/repository/virtual-environment paths, databases,
  Docker/WSL/browser/mail data, Roaming, configuration, plug-ins, LocalState and Unknown data are
  protected conservatively.
- A shared object/time limit and cooperative cancellation produce truthful PARTIAL/TRUNCATED/
  TIMED_OUT/CANCELLED reports. Access errors fail soft; scope escape and safety errors fail closed.
- Local JSON/CSV export uses exclusive creation, refuses overwrite and network targets, and is audited
  with a target digest rather than the path. Explorer receives one revalidated candidate and never
  executes it.
- Audit stores counts, classifications, confidence/protection aggregates, policy version and digests;
  it omits paths and contents. Model payloads use redacted path tokens.
- `deletion_performed` is structurally fixed to false. Stage 4D3 produces no R2 token and cannot call
  Stage 2B or authorize Stage 4D4.

## Stage 4D2C1 winget Package controls

- Exactly one new tool exists: `software.uninstall.winget`, one object, R2/R2_HIGH plan, two
  confirmations, batch size 1 and Rollback `NONE`.
- Only exact Package ID, installed version, official source name/identifier and current-user scope
  form an executable Package identity. Package Name, substring selection, custom/msstore sources,
  Source URL and machine scope never authorize execution.
- A Package must map to exactly one Installed Software identity by structured package ID, manager,
  version and scope. Display-name similarity is warning-only and cannot be upgraded by the LLM/UI.
- The executable is the fixed current-user WindowsApps alias. Direct Win32 reparse inspection must
  prove `IO_REPARSE_TAG_APPEXECLINK` and Desktop App Installer family; PATH search/fallback is absent.
- The fixed argument function is not caller-extensible. No override, silent, force, all, purge,
  custom source, source modification, installer args, restart or nested execution option exists.
- Existing software safety classification runs independently. Shared runtimes, drivers/hardware,
  Windows/security/network/Agent/enterprise/package-manager/unknown classes remain blocked even when
  winget reports support. Selected developer/runtime/server targets are R2_HIGH_IMPACT.
- Preflight has read-only diagnostics only. Related processes are warnings; running related services,
  winget busy, incomplete evidence or any active MSI/Vendor/winget transaction block.
- Elevated Agent processes are blocked. The adapter has no runas/ShellExecute/UAC fallback and passes
  `--scope user`; a child/installer privilege request becomes non-success evidence only.
- Plan and immediate gates bind Package, Software, mapping, alias, capability, safety, preflight,
  risk, plan and Preview digests. They are durable, expiring, parent-linked and atomically single-use.
- The child receives a small ordinary-Windows environment allow-list. API keys, token variables,
  PATH and winget custom configuration are not forwarded. Standard streams are DEVNULL.
- Cancellation before launch prevents process creation. After launch it only stops monitoring; no
  terminate/kill, service stop, retry or automatic restart occurs.
- Package-manager exit code is not final success. Both independent fresh inventories must be complete
  and both original exact identities absent. Contradictions stay visible and unverified.
- Residual analysis does one `lstat` on the known install location and never enumerates, follows or
  deletes program files, AppData, user data or registry entries.
- Durable reservation and mandatory pre-start audit are required. Audit stores digests/categories,
  not commands, Source URLs, executable paths, environment values or local software inventory dumps.

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

## Stage 7A observability and recovery boundary

Production logs are bounded local JSON records. Central redaction removes credential/content fields, common inline
secret shapes, URL queries and cleartext Windows paths before serialization. Crash reports keep only type, sanitized
message and hashed bounded frames; there is no automatic upload. Repeated unclean startup or corrupt health metadata
only removes capabilities by selecting a separate Safe Mode with no business-domain construction.

A diagnostic bundle is an R1 local file creation, not support transmission. The user reviews exact members,
exclusions, target and recovery level in a default-No dialog. Approval is digest/expiry-bound and single-use. Target,
member set and bytes are revalidated; overwrite/network/reparse/ambiguous targets fail closed. Mandatory audit failure
prevents authority or removes a newly committed ZIP. Telemetry and automatic update are not implemented.
## Stage 4D2C2 policy

MSIX removal is R2, single-object, current-user only and rollback `NONE`. An executable Preview needs
an ordinary healthy Store package with a current-user registration, `USER_MSIX_APP` type,
`USER_APPLICATION` safety class, complete inventory/relationships/preflight, no reverse dependents,
no direct dependency that could become an orphan, no running related service, no overlapping
uninstall, and a non-elevated Agent.

Framework, Resource, Bundle, Optional, System-signature, Windows/Security family, Provisioned,
Dependency and Unknown classes are blocked without override. The two confirmations bind plan,
Preview, exact Full Name, Family, version, architecture, scope, type, dependency, safety, preflight,
data-impact and risk digests. Any Store update or relationship change invalidates approval.

The fixed Windows option requests Roamable-data preservation. Windows may still remove
Package-managed LocalState and unused dependency packages; the immediate confirmation says so. The
Agent performs no additional file/registry/user-data deletion and never uses PowerShell, shell,
all-users removal, Provisioned removal, elevation, process termination or service stop.
