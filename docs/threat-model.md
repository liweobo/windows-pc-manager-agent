# Threat model

## Stage 4D2B additions

| Threat | Control | Residual risk |
|---|---|---|
| Malicious `UninstallString` injects CMD, PowerShell, script or chained command | Raw metadata is parse-only; direct local `.exe` and finite argv policy; exact array with `shell=False`; wrappers/loaders/scripts block | A trusted vendor executable can still contain vendor defects or malicious behavior |
| PATH/working-directory/network hijack substitutes another executable | Literal absolute drive path, no expansion/PATH search/UNC/device path, explicit executable and cwd, fixed-volume/reparse checks | A same-user attacker may still race after the final check; immediate hash/metadata recheck narrows the window |
| File is replaced after Preview | Volume/File ID, size, times and SHA-256 bind identity; runtime re-inspection and adapter-boundary recheck invalidate approval | Windows lacks an atomic “verify signature/hash then execute this exact open handle” abstraction in this implementation |
| Signed unrelated executable is accepted | Valid offline Authenticode is necessary but not sufficient; conservative Publisher match and install-location relation are also mandatory | Publisher metadata and certificate organizations may legitimately differ, causing safe false negatives |
| Quiet flags delete data or hide choices | QuietUninstallString and quiet/passive/restart/data/path/response switches are unsupported; tokens are never rewritten | Vendor UI may still offer destructive choices that the user must evaluate personally |
| LLM or UI changes arguments | No public argument input exists; exact parsed tuple and fingerprint bind plan/Preview/confirmation/guard | Malicious same-user registry metadata remains untrusted input and therefore often blocks |
| Protected software has a trusted uninstaller | Stage 4D1 class policy precedes executable trust; driver/security/Windows/shared/enterprise/Agent/unknown classes block | Complete dependency knowledge is unavailable even for allowed developer software, hence R2_HIGH_IMPACT |
| Uninstall silently kills apps or stops services through Agent capabilities | Vendor preflight graph has read-only diagnostics only; no process/service executor is injected or auto-confirmed | The vendor executable itself may request or perform lifecycle changes under the user's own interaction |
| Child environment leaks API keys/tokens | Child receives only a small Windows runtime allow-list; secret-like and non-allow-listed variables, including PATH, are dropped | A vendor process can read other data already accessible to the same Windows user |
| UAC is actively bypassed or requested by the Agent | No runas/ShellExecute elevation; machine-wide scope blocks; CreateProcess elevation-required becomes evidence only | A vendor executable manifest may cause Windows to return an elevation-required failure; current stage does not elevate |
| Parent exits while bootstrapper child remains | Descendants are observed; final process result waits while a known child is alive | Rapid reparenting or process identity reuse can make best-effort observation incomplete |
| Stop-monitoring is mistaken for cancellation | UI and transaction say monitoring stopped; no terminate/kill call exists and final verification is deferred | Uninstaller may continue outside Agent observation |
| Exit code 0 is reported as success | Fresh exact Installed Software inventory is authoritative; present/replacement/partial evidence remains failed or unverified | Registry update can be delayed or unavailable, requiring a later manual refresh |
| Residual cleanup follows a junction or deletes user data | Exact known-location `lstat` only; no enumeration, follow, registry cleanup or delete API | Report cannot identify every residual and intentionally cannot reclaim space |
| Crash causes duplicate execution | Active dispatch/monitoring becomes `INTERRUPTED`, approvals expire, and no transition redispatches | Vendor UI/process may still be running and requires manual observation |
| MSI and Vendor flows overlap | Both durable repositories inspect the other's active table before reservation | External uninstallers outside the Agent are not globally serialized |
| Audit/persistence failure hides a launch | Durable record and mandatory pre-start audit are required; failure aborts before adapter | Post-launch storage failure may lose final details, so the action is never automatically retried |

## Stage 4D2A additions

| Threat | Control | Residual risk |
|---|---|---|
| Malicious/quoted UninstallString starts shell, PowerShell or another executable | Execution model has no command field; strict local ProductCode is revalidated and fixed `msiexec` arguments are code-generated with `shell=False` | MSI custom actions are installer-controlled and may themselves have defects |
| Display name resolves to the wrong application | Source-qualified identity plus exact version/publisher/scope/architecture and Windows Installer registration; ambiguity requires explicit selection | Registry/MSI metadata can be inaccurate or maliciously altered by same-user malware |
| Software upgrades between Preview and execution | Full identity/capability/ProductCode/policy/preflight revalidation; changed invariant invalidates confirmation | Change can occur after final revalidation; durable exact guard narrows but cannot eliminate OS-level races |
| User/model supplies ProductCode or extra flags | Schema accepts only target query; ProductCode comes from local raw inventory and API; adapter accepts typed product only | Compromised local inventory/API boundary remains trusted Windows evidence |
| Confirmation is replayed or double-clicked | Durable expiry, plan/runtime parent binding and atomic single-use consumption | Database corruption stops write actions rather than recovering availability |
| Protected MSI is treated as safe because it is MSI | Safety class precedes mechanism; shared runtime/driver/security/Windows/Agent/unknown classes block | Complete software dependency knowledge is unavailable |
| Related application/service is silently stopped | Preflight is read-only and blocks; no process/service-control dependency exists in this service graph | MSI custom actions may request their own application/service handling |
| Machine MSI triggers elevation or UAC | Machine/managed context blocks; elevated Agent process blocks; no runas/ShellExecute broker | Some current-user MSI custom actions may still return privilege-required |
| Installer hangs | Finite monitor window returns `MONITORING_DETACHED`, keeps process alive and transaction `WAITING`; no early verification | User may need to inspect/close installer UI manually |
| Success exit is false success | Fresh registry and Windows Installer API verification determines final state | Inventory can be temporarily unavailable; outcome then remains unverified |
| Residual cleanup deletes user data or follows a junction | Analyzer performs only exact-path `lstat`, no recursion/follow/delete | It cannot quantify all residuals |
| Crash causes duplicate uninstall | Startup marks active work `INTERRUPTED`; confirmations expire; no automatic retry | Installer may have continued outside the crashed Agent |
| Audit failure hides a mutation | Pre-start failure aborts; post-start failure warns and suppresses retry | A post-start local disk failure can still lose some final evidence |

## Stage 4D1 additions

| Threat | Control | Residual risk / truthful limitation |
|---|---|---|
| A malicious uninstall string asks the Agent to run shell or hidden commands | Raw metadata is untrusted; the parser has no execution primitive and rejects wrappers, relative/UNC/missing/non-EXE targets | A signed vendor uninstaller can still be unsafe; Stage 4D1 never invokes it |
| Similar display names select the wrong product | Source-qualified identity plus exact filters; substring results require explicit candidate selection and a new plan | Registry metadata can be incomplete or wrong, so warnings remain visible |
| Software changes after plan confirmation | Fresh inventory/identity/metadata/capability validation at each boundary; changed digests stop | A race remains after the last read, but this stage performs no write |
| UI or model invents `software.uninstall.execute` | Exact five-tool registry, independent plan review and zero-execution source guard | A future execution stage cannot reuse this acknowledgement |
| Raw command/path data leaks | Separate raw/normalized models; raw fields excluded from serialization/repr; audit stores digests/counts | Registry metadata remains local OS/user-controlled input |
| “I understand” is mistaken for uninstall consent | Preview-bound acknowledgement has no execution capability and ends in explicit STOP | Users may still misread third-party metadata; UI states nothing was uninstalled |
| Impact analysis claims complete dependency knowledge | Known path correlation is separate from heuristic name evidence; unknown impacts are explicit | Proprietary plug-ins, licenses and user data cannot be fully discovered |

## Stage 4C2 additions

| Threat | Control | Residual risk |
|---|---|---|
| LLM/UI asks for Disabled, delayed, driver or protected-service change | Finite action enum, strict Pydantic plan, Stage 4C1 base policy and separate Stage 4C2 policy reject before confirmation | Windows policy or publisher evidence can change later; execution re-reads and fails closed |
| A ServiceName is reused or binary/account changes | Stable identity digest binds type, binary fingerprint and account; every Preview and adapter call revalidates it | An attacker with stronger SCM rights may race after the final read; post-write read-back exposes mismatch |
| Configuration changes between Preview and write | Source/target configuration, state, impact and permission digests bind both confirmations; adapter revalidates on the exact handle | Windows provides no multi-field compare-and-swap; the boundary minimizes the last-check/write interval |
| A configuration change unexpectedly starts/stops a service | Adapter never calls runtime control and verifies the runtime state is unchanged after `ChangeServiceConfig` | The service or another administrator may independently change state; reported as verification failure |
| Broad SCM API misuse changes binary/account/dependencies | Platform accepts a typed request and supplies `SERVICE_NO_CHANGE`/null for every field except start type; no generic passthrough | pywin32/Windows defects remain platform dependencies |
| Missing ordinary-user permission is bypassed with UAC | Elevated process is blocked; permission probe and write require existing DACL rights; access denied has no elevation path | Most services will correctly remain unavailable to an ordinary user |
| Backup is corrupt, substituted or disclosed through audit | Current-user DPAPI, payload digest, immediate decrypt verification, separate tables and audit minimization | Loss of the Windows profile/DPAPI material can make restore unavailable |
| Restore overwrites a later administrator/user decision | Restore requires current identity and config to exactly equal the Agent-written value, then uses a new plan, backup and two confirmations | A conflict requires manual review; the Agent intentionally does not merge or overwrite |
| Crash causes a hidden retry | Active records become `INTERRUPTED`; confirmations are not reconstructed and nothing auto-resumes | A write dispatched immediately before failure may need manual observation |
| Confirmation replay or plan drift | Parent-child, expiry, canonical plan/Preview/argument/object/backup bindings and atomic one-time consumption | None within one intact local database; database corruption disables the write boundary |

## Stage 4C1 additions

| Threat | Control | Residual risk |
|---|---|---|
| Natural language targets the wrong service | Exact ServiceName resolution; ambiguous DisplayName and partial matches block | A user can still choose the wrong exact row; Preview names it explicitly |
| System/security service is stopped | Default-deny type/account/path/name/publisher/description policy plus protected lists | Vendor naming evolves; unknown classification is therefore blocked |
| TOCTOU swaps configuration after approval | Digests bind service type, binary fingerprint, account and start type; re-read before execution and before Restart START | Windows can change state immediately after a read; handle-scoped control and post-read reduce but cannot remove all races |
| Dependency cascade expands impact | Live dependency/dependent graph is bound to confirmation; any blocker stops the action; no cascade API | SCM/service behavior can have undocumented external effects |
| Privilege is silently escalated | Elevated-process blocker, least-rights handle probes, no UAC/runas/shell fallback | Existing service DACLs can still grant the ordinary user control rights |
| Restart Stop succeeds but Start fails | Explicit two-step transaction, fresh identity check, `PARTIALLY_COMPLETED`, actual-state verification | Service remains stopped and requires informed manual action |
| Cancellation is mistaken for Undo | Cancellation prevents only future controls; dispatched SCM request is allowed to finish bounded verification | A control already delivered cannot be recalled |
| Restart or crash replays a control | Write-ahead ordered steps; active work becomes `INTERRUPTED`; confirmations are one-time; no auto-resume | User must inspect current service state and create a new Preview |
| Model invents a service tool or ServiceName | Model output is an untrusted hint; registry has only start/stop; resolver obtains local identity | A compromised local SCM/configuration remains outside the model boundary |
| Audit leaks executable or credentials | Allow-listed digest/ServiceName events; no path/command/password/binary payload | ServiceName itself may reveal installed-product metadata |

Stage 4C1 tests use Fake SCM for every mutation path. The real-Windows adapter test enumerates
and inspects only; it never opens a control right with the intent to mutate and never calls
StartService or ControlService.

## Stage 4B additions

| Threat | Boundary/control | Residual risk |
|---|---|---|
| Model invents a registry path or bulk action | Fixed source enum, reference-only tool schema, max batch 1 | User can still select the wrong ordinary entry; Preview must be read |
| Entry changes after Preview | Full identity/state/approval/backup digests and immediate reread | A privileged concurrent attacker can still race after the last check |
| Protected/security startup is disabled | Default-deny name/path/publisher/scope/source classification | Publisher metadata is auxiliary and can be stale or spoofed; unknown blocks |
| Backup leaks a command or shortcut | Current-user DPAPI, separate vault, audit stores digests only | Same-user malware may access process or DPAPI context |
| Restore overwrites a new object | Original location must be absent; exact disabled material must match | Manual external changes can make automatic recovery unavailable |
| StartupApproved binary is misinterpreted | Read-only evidence; no write path | Windows may add formats that become UNKNOWN/read-only |
| GUI freeze or shutdown race | COM-initialized bounded workers; shutdown waits for the pool | A stuck OS call can delay cooperative shutdown |
| Crash leaves uncertain mutation | Durable states; no automatic resume; verification/audit | Manual inspection may be required after abrupt process termination |
| “Disabled” is treated as a launch guarantee | Result says configuration absent/present only | Other startup mechanisms or application self-repair may still launch it |

Stage 4B does not authorize HKLM/RunOnce/common Startup writes, service changes, software
uninstall, elevation, arbitrary registry editing or shell commands.

## Stage 4A additions

| Threat | Boundary/control | Residual risk |
|---|---|---|
| Model invents PID or broad kill | Local resolver, exact enum query, ambiguity/batch denial | User can still select the wrong ordinary app; Preview must be read |
| PID is reused after Preview | Creation time/path/SID/session digest plus same-handle recheck | Compromised kernel/process memory is out of scope |
| System/security/service is targeted | Critical/protection/SID/session/name/path/SCM default-deny policy | Security-product naming can evolve; unknown protection blocks |
| Graceful silently becomes force | Separate action/risk/tool/transaction and two new confirmations | User may explicitly choose force after reading warning |
| Browser helper changes membership | Exact application-group digest at confirmation/execution; fresh force plan uses remaining members | Highly dynamic apps may require repeated Preview |
| GUI blocks during service/process inspection | All resolution, Preview, revalidation, wait and execution run in workers | A platform call can still delay cooperative cancellation |
| Cancel is mistaken for Undo | UI labels “stop waiting/future objects”, rollback NONE everywhere | An already sent WM_CLOSE/TerminateProcess remains effective |
| Crash creates misleading status | Additive transaction state; active work becomes INTERRUPTED, never auto-retried | Actual outcome can require manual inspection |
| Audit failure hides action | Mandatory Preview and pre-execution audit fail closed | Post-action audit failure cannot reverse an executed process exit |
| Command line leaks secrets | Command line has no domain field and is never queried/audited | Executable path and username remain locally sensitive |

## Stage 3 additions

| Threat | Control | Residual risk |
|---|---|---|
| Model invents a system-changing tool | Finite enums, local compiler, registered R0 manifests, independent review | Invalid output fails closed |
| Confirmation applies to changed sampling | Plan ID and complete canonical digest with expiry | User must read the displayed plan |
| Process command lines expose secrets | Command lines are never requested or represented | Executable paths/usernames remain locally sensitive |
| Uninstall command executes or leaks | Only display metadata is read; uninstall strings are absent | Registry metadata may be stale |
| Query becomes mutation | `KEY_READ` and SCM enumerate/query handles; no mutation API registered | Library defects remain possible |
| One access error hides other evidence | Per-collector outcomes and partial reports | Categories have different timestamps |
| Load is called a root cause | Multi-sampling, confidence, evidence, disclaimer | Short windows can miss intermittent behavior |
| Startup/file names inject prompts | Untrusted display data, excluded from provider payload | User may misread deceptive names |
| Audit leaks inventory | Counts/status/timing and sanitized errors only | Original request is locally retained |

Stage 4A authorizes only the two exact current-user process actions described above. No
interface authorizes service/startup changes, registry writes, uninstall, elevation,
firewall changes, arbitrary commands, or automated multi-capability system modification.

## Stage 2B additions

| Threat | Boundary | Mitigation |
|---|---|---|
| Model or filename selects a victim | Planner | Only explicit local selection enters `TrashPlan`; provider has no path field |
| First approval is mistaken for execution | Confirmation | Independent PLAN and short-lived RUNTIME confirmation services and dialogs |
| File/directory changes between dialogs | Preview/executor | File ID plus metadata and complete recursive tree digest revalidated twice |
| Parent and child are both selected | Safety reviewer | Overlap is rejected, avoiding duplicate/ambiguous Shell operations |
| Protected/system content is selected | `TrashPathPolicy` | R2-only Windows/program/application-data/root/attribute exclusions |
| Network/removable volume lacks safe bin | Capability adapter | System-volume fixed writable NTFS and successful Shell query required |
| Windows silently destroys instead of recycles | Shell callback | Explicit recycle flag, pre-delete recycle transfer flag, WANTNUKEWARNING, and non-null post-delete Recycle Bin item required |
| Crash after Shell call | Journal | PREPARED recovery first; restart marks item UNKNOWN and never retries |
| UI double click or stale dialog | Service/SQLite | One-time tokens, digest bindings, state transitions, durable PLAN+RUNTIME proof |
| User assumes automatic restore | Domain/UI | MANUAL level everywhere, no Undo button, exact Explorer recovery instructions |
| Developer adds permanent deletion | CI | AST security test rejects `unlink`, `rmtree`, and `os.remove`; adapter test rejects legacy/empty-bin APIs |

## Protected assets

- User files, file metadata, and the meaning of “authorized directory”.
- Credentials, browser sessions, private keys, wallets, and Windows security stores.
- Windows integrity and the ordinary-user privilege boundary.
- Model API credentials, disclosure consent, and cost control.
- Audit/confirmation integrity and local result correctness.
- Repository, dependencies, CI, and release integrity.

## Threats, controls, and residual risk

| Threat | Example | Stage 2A controls | Residual risk |
|---|---|---|---|
| Prompt injection | Filename says “delete everything” | Names/content remain data; no delete or shell tool; registry only | Future content analysis needs the same separation |
| Model scope expansion | Provider returns `C:\` or invented tool | Provider schema accepts opaque root IDs; compiler resolves locally; validator requires exact registry/scope | A compromised process can attack in-memory objects |
| Traversal/redirect | `..`, symlink, junction, reparse target | Syntax rejection, canonical components, protected roots, reparse checks, directory identity | A narrow metadata check/use race remains; Stage 1 performs no write |
| File replacement during hashing | Candidate changes after scan | Revalidate authorized regular file and compare size/mtime/available IDs before and after | Same-size/same-time replacement on filesystems without stable IDs is a documented residual risk |
| False duplicate | Same name or size | Size only selects candidates; quick hash, full SHA-256, optional byte comparison prove grouping | Cryptographic collision is extraordinarily unlikely; byte verification is enabled by plan compiler |
| False “unused” claim | NTFS atime is disabled/deferred | Require old access and modification evidence, probe atime policy, lower confidence, say “疑似” | Timestamps cannot prove human use or value |
| External disclosure | Goal/path/results sent silently | Exact purpose/provider/payload confirmation; planning uses IDs/labels; explanation aggregates only | User goal/label may itself contain sensitive text; confirmation displays what is sent |
| Stale consent | Threshold changes after approval | Full plan/payload digest and expiry; UI invalidation; execution rechecks | In-memory confirmations are intentionally lost on restart |
| Resource exhaustion | Hundreds of thousands of files | Batch streaming, SQLite paging, count/timeout bounds, cancellation, no link cycles | A single slow filesystem call can delay cooperative cancellation |
| Offline hydration/cost | Hashing OneDrive placeholder downloads data | Offline attribute causes a skipped issue; no automatic hydration | Attribute reporting depends on filesystem/provider correctness |
| Audit bypass | Corrupt/unwritable SQLite | Startup health check, every event append can fail closed, high-risk work unavailable | User-level malware can alter app files/database outside this threat boundary |
| Report overwrite/deletion | Export replaces a document or removes partial output | Absolute local path, exact suffix, exclusive create, no cleanup deletion | Partial export can require manual removal |
| Privilege abuse | Tool elevates or edits system settings | No elevation API, R3/R4 denial, ordinary-user runtime | Future privileged broker requires a separate threat model |
| Supply-chain drift | Action tag or package changes | GitHub actions pinned by SHA, `uv.lock`, vulnerability/static/secret scans | Trusted registries and upstream maintainers remain dependencies |
| Preview/use race | Source is replaced or target appears after approval | Handle-based volume/File ID plus metadata and target/scope/reparse checks immediately before each write | A compromised process/kernel remains outside the boundary |
| Silent overwrite | Move or rollback targets an occupied name | STOP conflict policy, no replace flag, target recheck, exact Preview | User must resolve conflicts manually |
| Cross-volume data loss | Move becomes copy plus source deletion | Compare source and destination-parent volume; Win32 call omits copy-across-volume flag | Mapped/storage behavior depends on Windows reporting; failures stop |
| Forged write call | UI/model directly invokes a registered tool | Registry demands RUNNING transaction/item, matching IDs, tool, and exact argument digest | In-process memory compromise is excluded |
| Crash during write | App exits after filesystem mutation | PREPARED Undo persisted first; stale RUNNING becomes INTERRUPTED; never auto-resume | A crash between Win32 completion and final journal update requires user inspection |
| Unsafe rollback | Original path now occupied or result changed | Live reverse Preview, independent confirmation, identity/metadata checks, no overwrite, reverse order | Later user changes can reduce FULL to conflict/manual handling |
| Directory rollback deletes new data | User adds content to transaction-created folder | Remove only exact transaction-created identity after managed children reverse and directory is empty | User must manually handle unmanaged content |

## Security verification

Tests cover authorization lifecycle, protected paths, traversal, UNC/network and Windows
name ambiguity, scope mismatch, symlink/reparse behavior, changed directory/file identity,
permission errors, count/timeout/cancellation, plan and threshold mutation, unknown tools,
external payload mutation/expiry, provider schema errors, audit redaction/unavailability,
duplicate content and hash cancellation, report no-overwrite, GUI invalidation, and rollback
records. Stage 2A adds move/rename/mkdir, conflict, source/target mutation, one-time
confirmation, transaction interruption, failure-stop, persisted capability, reverse-order
rollback, rollback conflict, cancellation, and GUI tests. Overall core coverage is enforced
at 85%; the high-risk boundary target remains 95%. A real symlink test may skip where Windows denies symlink creation, while
deterministic reparse branches remain tested.

## Explicit exclusions

The MVP does not claim protection against a compromised kernel, an attacker already able
to modify this process, physical disk attacks, or malicious dependency infrastructure.
Installed-software inventory is not yet implemented. Stage 2A file mutation is limited to
the documented R1 tools. Recycle-bin/permanent deletion, overwrite, cross-volume move,
system changes, arbitrary commands, browser automation, and privilege elevation remain
outside this stage and cannot be triggered through placeholder interfaces.
