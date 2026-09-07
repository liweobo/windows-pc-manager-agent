# Threat model

## Stage 5E additions

| Threat | Control |
| --- | --- |
| Model/page/document expands a long task | Closed domain enum, explicit user revision and separate scope-expansion acknowledgement |
| Task-plan consent approves later writes | Consent contains only R0 node IDs and fixed false write-authority field |
| Duplicate UI/restart dispatch | Durable unique node dispatch reserved before handoff; writes never auto-retry |
| Crash replays an uncertain action | Active task becomes interrupted; dispatch becomes reconciling; result schema forbids replay |
| Old PID/DOM/file/service identity is reused | Object-specific staleness policy and owning-domain Fresh resolution |
| One domain claims another's success | Receipt binds exact task, node, graph version and domain; verified success requires evidence |
| Notification or voice says “yes” | Attention/notification schema cannot authorize; confirmations remain visual and domain-owned |
| Global Undo overstates recovery | Summary fixes global Undo false and preserves each domain rollback level |
| Checkpoint restores a secret/confirmation | Checkpoint contains metadata only and schema rejects restored authority |
| Long task exhausts resources | Immutable bounded budgets pause coordination and request user review |

Residual risk: local malware running as the same user may tamper with application storage, and known-pattern secret
screening is not complete DLP. Digest and schema checks detect accidental/simple payload changes but are not a
hardware-backed trust boundary. Production signing and installation hardening belong to Stage 7A.

## Stage 5D coordination threats

| Threat | Enforced control / residual risk |
|---|---|
| Agent or model impersonates another role | Runtime creates identity; manifest/Prompt digest revalidation; Planner role claims ignored |
| Child asks for wider tools or loops forever | Capability intersection, allowed child roles, task goal boundary, depth/count/model/context budgets |
| Agent message acts as confirmation | Schema contains no authority; sender/recipient/task/node/goal/issuer/single-use checks; domain confirmation remains separate |
| Web/document prompt injection spreads between Agents | Source-required trust labels, taint union, cross-domain classification and opaque-reference reduction |
| Document content is exfiltrated through Browser | Document/user data external transmission defaults BLOCK; browser upload remains absent |
| Secret reaches model/Memory/audit | Credential/Secret classification and known-pattern block, closed Memory keys, value/body-free audit; pattern screening is not complete DLP |
| Saved preference lowers risk or removes confirmation | Memory has no risk/permission fields; unsafe directives BLOCK; each domain performs Fresh safety and confirmation |
| Stale recent object is executed | In-memory TTL + conversation binding + mandatory Fresh domain resolution; restart forgets hints |
| Concurrent Agents modify one resource | Process-local read/write lease conflict; underlying domain locks/Fresh checks remain required |
| Majority model vote claims success | Fixed evidence precedence; model `*_VERIFIED` is not accepted as verified |
| Retry or restart repeats a write | Agent retry is proposal-only; no Agent action executor; startup marks active coordination INTERRUPTED |
| User cancels and assumes prior changes were undone | UI says “cancel future work”; completed domain effects require their own Undo/manual recovery |

## Stage 5C browser threats

| Threat | Enforced control / residual risk |
|---|---|
| Page prompt injection requests secrets/tools/approval | All content is untrusted, advisory signal detection, closed actions and independent policy/confirmation; deceptive display still requires user judgment |
| SSRF, localhost, private host or redirect escape | HTTP(S), standard ports, no credentials, IDNA, fresh all-public A/AAAA checks at planning/routing/redirect; application policy is not an OS network sandbox and DNS races remain |
| Stale/ambiguous element activates a different target | Session/page/navigation binding, semantic fingerprint, exact-one role/name match, fresh observation; highly dynamic sites may fail safely |
| Page triggers purchase, message, account or terms side effect | Transaction/account/communication markers and unsupported methods/actions BLOCK; classifier vocabulary cannot understand every language, so allowed actions stay narrowly structural |
| Confirmation replay after page change/takeover/restart | Plan/action/origin/generation/expiry binding, atomic SQLite consume, startup/session invalidation; same-user database tampering is outside the MVP trust boundary |
| Browser child receives provider secrets | `-I`, no shell, credential-like environment removal, ephemeral profile; same-user malware can still read resources allowed by the user token |
| Download traversal, overwrite or type disguise | Leaf-name/ADS/reserved/bidi checks, finite MIME/extension/magic/size, O_EXCL commit, SHA-256; format validation is not malware scanning |
| Download recovery overwrites newer work | Current path/size/hash must equal recorded result and recovery target must be absent; recovery refuses drift and does not securely erase data |
| Audit/model leaks page or credentials | Aggregate origin/digest/count audit, model disabled by default, future explicit minimized disclosure; user-visible page data still exists in GUI/process memory |
| Compromised Playwright/Chromium escapes | Managed current runtime and browser sandbox requested; dependency/browser vulnerabilities remain and require patching/release review |

Automated tests use fakes and a synthetic managed-Chromium route. They do not authorize or validate real
logins, CAPTCHA, SSO, payments, messages, internal sites or malicious-document safety.

## Stage 5B speech threats

| Threat | Enforced control / residual risk |
|---|---|
| Ambient speech, accent, homophone or hallucinated final text | Explicit bounded PTT and mandatory editable review; confidence absent means UNKNOWN |
| Replay, duplicate final callback, double click or edited replay | Session revision CAS and one durable request UUID; restart never replays |
| Spoofed voice saying yes or requesting administrator | No voice identity/approval primitive; original visual confirmations and Windows UAC unchanged |
| Hidden/background recording or TTS feedback into mic | One owner; explicit activation; hide/inactive/quit stop; reset output before PTT |
| Exfiltration to unexpected provider | Exact independent expiring digest-bound destination/model/payload consent; official adapter never inherits legacy proxy |
| Sensitive dictation | Warn before upload; known-secret transcript/output block; raw audio privacy needs user judgment |
| Slow provider or late callback | Finite timeout, cancellation event, request ID comparison, no automatic retry; sent data cannot be recalled |
| Malicious transcript, file name or fake command | Finite shared preparation router and independent domain validation; no shell/tool execution in voice |
| Changed selection/target after recording | Context epoch invalidation and fresh original-domain resolution; vague references require explicit selection |
| Corrupt journal or missing audit | Fail closed before capture/dispatch/request delivery; hardware stop does not depend on database success |
| False spoken success/recovery | Aggregate readback with consumed confirmation lineage; unsupported/incomplete receipts remain UNVERIFIED |

Python/Qt/OS may retain temporary memory copies; this implementation makes no secure-erasure or
cloud-zero-retention guarantee. It also provides no resistance to malware already controlling this user process.

## Stage 5A document threats

| Threat | Control / remaining limitation |
|---|---|
| Document/file-name prompt injection | Data-only context, no tools/code, finite local validation, source-bound quotes, independent consent |
| Macro, DDE, external connection, embedded content | Static inspection only; risky structures read-only or rejected; no native Office activation |
| ZIP/XML/PDF resource abuse | ZIP limits, defused XML, bounded private parser/serializer process and Job memory/process limits |
| Same-path replacement, junction or race | Exact grants, pinned ancestors, exclusive source lease, full hash/ID checks, no-replace handle rename |
| Save clobbers user work | Default absent-target Save As, immutable Preview, revalidation, preserved original, verified encrypted backup |
| Confirmation replay or stale UI | Exact digest/purpose/expiry binding, atomic SQLite single-use consumption, restart invalidation |
| Restore overwrites a later revision | Current identity must equal Agent result; retained original identity and backup must remain exact |
| Credential/body leakage | Explicit selected spans only, known-secret denial, no body/Diff audit, safe error codes and separate provider endpoint display |
| Excel/CSV changes data types or calculations | Tagged scalars, finite formula policy, text escaping, Decimal transformations, semantic reopen verification |
| Crash between renames | Journal first, retain all material, INTERRUPTED without auto-commit; no claim of a globally atomic swap |

The parser Job limits resources but is not a fully restricted-token/AppContainer sandbox. A malicious same-user
process able to tamper with the application, database or libraries is outside this MVP's trust boundary.
Keep dependencies patched. DPAPI does not protect against malware already executing as the same user.
Cloud sync and antivirus can cause safe denials; no lock bypass or download/hydration fallback is attempted.
Test fixtures are synthetic; no real user documents or host cleanup operations are used by automated tests.

## Stage 4E3 threats and mitigations

| Threat | Mitigation | Remaining limitation |
|---|---|---|
| Recommendation text injects a command | Typed enum policy, exact UUID lookup, no raw execution arguments or generic executor | Recommendation wording is still untrusted display data |
| Old report or selected row acts as authorization | Canonical source digest/expiry; single-use navigation; original domain Fresh selection and confirmations | No background event bus: external changes are caught by the domain's final checks |
| Recommendation routes personal data to direct cleanup | Stage 1 root provenance → Stage 1/2; exact Stage 4D3 context → Stage 4D3/4; protected candidates block | Unknown sources remain unsupported |
| PID is reused before process Preview | Fresh PID, creation time and executable-path comparison before existing Stage 4A gates | Original final identity checks remain necessary |
| Forged success through UI closure or old transaction | Bind before dispatch; read-only finite table reader; plan/confirmation/result validation | Long-running or privileged outcomes may remain only in the original domain UI |
| Cancel races with preparation/result | Revision-checked journal, bounded locks, cancellation token and one active review | Already-dispatched external uninstallers are not killed |
| Restart replays dangerous work | Active sessions become STALE, volatile handoff/result bindings discarded | User must explicitly start a new report/plan |
| Metric change advertised as optimization benefit | Minimal separately confirmed R0 sampling, completeness/comparability checks, UNKNOWN/LOW attribution | No measured boot-time or causal speed improvement claim |

## Stage 4E2 threats and mitigations

| Threat | Mitigation | Residual risk |
|---|---|---|
| Replay an old report/selection as cleanup authority | Session-only TTL lookup followed by Fresh discovery, new plan/Preview and two new durable confirmations | User can still manipulate files outside the Agent |
| Candidate path is replaced after scan | File identity and complete material digest are checked at assessment, runtime Preview, final batch and per-item Shell boundary | A kernel-level race after the final handle check is outside user-mode control; result becomes uncertain |
| Junction/symlink expands scope | Component reparse checks, no-follow identity inspection, exact-root containment and tree traversal rejection | Environments without synthetic-link permission rely on mocked branch tests plus production code review |
| Temp contains database/config/user data | Independent suffix/component/category protection signals block even under an allowed root | Novel sensitive formats may need additional protection rules |
| Locked/recent/in-use installer data is moved | Age threshold, DELETE-access probe and global uninstall-transaction exclusion | Another process can begin using an item after final validation; Shell failure stops the batch |
| UI silently drops blocked rows | Complete assessment retains all decisions; blocked rows cannot be checked; mixed-batch builder rejects all | User must create a new smaller selection |
| Raw path, force flag or model tool name reaches writer | `extra=forbid` reference-only Schema, exact four-tool registry and durable write guard | A future tool addition needs a new manifest/security review |
| Recycle operation fails then permanently deletes | Shared Recycle Bin primitive is the only adapter; source tests forbid delete APIs and there is no fallback | Some volumes may be unsupported, which safely blocks cleanup |
| Moving to Recycle Bin is reported as reclaimed space | Result separates bytes removed from original paths from verified reclaimed bytes (`None`) | Windows may later empty the Bin outside the Agent |
| Empty confirmation applies to changed/all-volume contents | Exact system-volume scope, complete count/size/age digest, reinspection, no null scope and one-use authority | Shell completion followed by inspection failure yields an irreversible uncertain result |
| Crash resumes partial mutation | Active transactions become `INTERRUPTED`, approvals expire, and no redispatch path exists | User must inspect audit and Recycle Bin manually |
| Audit leaks local names/content | Aggregate-only events and SHA-256 path digests; no content reads/model upload | Counts, timings and categories remain local security metadata |

## Stage 4E1 threats and mitigations

| Threat | Mitigation | Residual risk |
|---|---|---|
| A prompt asks for one-click cleanup | Finite five-tool R0 registry; no clean/fix/boost/apply or existing writer | A future stage needs a new threat model and authority path |
| Old report or selected UUID is replayed | All artifacts are non-executable and the authority guard always rejects them | Users may manually act outside the Agent |
| Filename/document prompt injection expands scope | File contents are never read; names are untrusted display data; roots derive locally from authorization IDs/allowlists | A malicious name may still be visible locally in the report |
| Junction or symlink reaches credentials/another user | `lstat` entry revalidation, reparse rejection and exact-root containment | Concurrent external replacement yields partial/failed analysis |
| Browser cache scan reaches passwords/cookies/sessions | Only exact cache leaf allowlists; sensitive component denylist; no profile-content scan | Non-default profiles can be omitted and reported as incomplete |
| System-managed storage is overestimated | Update/DO/WinSxS/Installer Cache return protected or unavailable without reliable API evidence | Windows may provide less detail to a standard user |
| Momentary CPU/process load is called a root cause | Multiple samples, multi-factor thresholds, explicit limitations and confidence | Short sampling cannot diagnose intermittent workload |
| Audit leaks paths or file lists | Aggregate counts/bytes only; free-form Stage 4E1 request is redacted | Explicit user report export intentionally contains local report data |
| Analysis silently modifies state | Query-only interface, isolated registry, zero-modification tests and no Broker/shell imports | OS counter access can update unrelated system telemetry outside Agent control |

## Stage 4X3 threats and mitigations

| Threat | Mitigation | Residual risk |
|---|---|---|
| Turn Broker into an admin shell | Separate strict payloads, immutable manifests, concrete-handler requirement, no generic command/registry/SCM/uninstall API | A coding defect in a narrow adapter still needs release review and manual testing |
| Elevation overrides a safety block | Existing policy executes before routing; Main and Broker rebuild safety; only `REQUIRED` routes | Local metadata can be incomplete, so incomplete evidence blocks |
| Service config or runtime TOCTOU | Stable service identity, exact config/runtime/impact/backup digests, pre/post-consumption Fresh checks, runtime invariant readback | External change after dispatch can yield uncertain failure; no retry |
| HKLM 32/64 view substitution or restore overwrite | Explicit view in identity/Payload/result, encrypted exact backup, transacted API, absence/conflict checks | External registry writers may race; final readback prevents false success |
| ProductCode or software substitution | Fresh software + MSI registration, version/publisher/scope/architecture/source digests and complete preflight | MSI custom actions remain third-party privileged code; only vetted classes are eligible |
| Provider or IPC secret leaks to MSI | Allowlisted child environment excludes PATH and secret-like keys; DEVNULL streams | Windows Installer itself may access machine/user state under Windows rules |
| SYSTEM/TrustedInstaller misuse | Broker requires elevated HIGH integrity and rejects every other integrity level; no token manipulation | Compromise outside this process boundary is out of scope |
| Confirmation replay or stale Preview | Durable parent/child confirmations, expiry, manifest/schema/policy and state digests, atomic single consumption | A post-dispatch crash can require manual reconciliation |
| Misleading success | Typed action evidence, Broker verification and independent Main readback | MSI rollback remains NONE; successful removal cannot restore application state |

Machine Vendor uninstall remains deferred because executable/argument/signature trust under elevation needs
a separate threat model and cannot reuse the current-user Vendor authorization.

## Stage 4X2 threats and mitigations

| Threat | Control | Residual risk |
|---|---|---|
| Main application runs permanently elevated | Runtime rejects an elevated Main token; only an independent `runas` Broker elevates | A user can separately alter their local installation; production signing/install policy must detect it |
| Malicious/changed Broker binary is launched | Absolute literal path, no PATH search/reparse, stable hash/file evidence, asInvoker manifest; production signing and trusted install location | Repository development builds are unsigned and intentionally cannot enter production mode |
| Fake pipe server captures the request | Unpredictable endpoint, current-user DACL, reject-remote/first-instance, launched Broker PID/session check before request disclosure | A same-user attacker may race the endpoint and cause fail-closed denial of service |
| Different client talks to elevated Broker | Broker impersonates pipe client and compares actual SID/session/PID/process identity with opaque launch expectation and Hello | Compromise of the exact Main process is outside the IPC boundary |
| UAC credentials switch to another account | Caller and Broker SID/session must match; otherwise Broker rejects without consuming/executing | V1 cannot support over-the-shoulder credentials by design |
| Frame confusion, truncation or JSON ambiguity | Length prefix and pre-allocation bound, exact sequence/routing, strict UTF-8/JSON, duplicate/non-finite rejection, payload digest | Local resource exhaustion may still make the operation unavailable |
| Handshake or result is substituted | Fresh challenges, transcript binding, 30-second session key, HMAC and constant-time checks on authenticated frames | The key exists in both short-lived process memories during one exchange |
| UAC cancel or transport failure triggers repeat prompts | One dispatch attempt, durable invalidation/interruption and no retry/resume | User must create a new plan if they intentionally try again |
| Request replays or double-clicks | Unique durable request/nonce, atomic confirmation consumption and active-request lock | SQLite unavailability denies execution |
| Target changes while UAC is open | Broker repeats identity/state/config/dependency/safety checks and a final TOCTOU check | Kernel-level races after the final SCM check are outside user-mode control |
| Broker executes another privileged capability | Private registry and handler accept only exact service Start/Stop; other enums have no handler | Adding a future handler requires a separate stage/security review |
| Service control return is reported as success | Broker postcondition plus independent Main-process SCM readback | If standard-user query rights disappear afterward, result is conservatively unverified |
| Broker becomes a long-lived admin daemon | One endpoint/request, bounded natural-exit wait, no loop/retry/resume; timeout is unverified and Main never kills/relaunches it | Abrupt OS termination may leave the service transition outcome needing manual inspection |
| Audit leaks SID, service name or session secret | Events store UUIDs, lifecycle and digests; raw SID, pipe name, service payload, nonce and key are omitted | Timing and action type remain local security metadata |

## Stage 4X1 threats and mitigations

| Threat | Control | Residual risk |
|---|---|---|
| Model/user injects a command or executable | Strict per-action payloads, `extra=forbid`, no generic fields, separate non-LLM registry | New action types still need separate review |
| AccessDenied is mistaken for authority | Resolver requires completed action-specific evidence; otherwise UNKNOWN | Windows ACL interpretation remains platform-specific future work |
| Request is changed in transit | Canonical request digest plus HMAC-SHA-256 over exact bytes | Ephemeral in-process key is not a production IPC trust model |
| Duplicate JSON keys or parser ambiguity | Pre-parse size bound, strict UTF-8, duplicate-key rejection, exact version and schema | A future transport must preserve the same bytes |
| Old confirmation reused after change | Plan/Preview/payload/target/risk/privilege digests, UTC expiry and single-use consumption | User comprehension still depends on clear Preview text |
| Replay or concurrent double dispatch | Unique request digest/nonce fingerprint and atomic SQLite claim | Filesystem/SQLite availability can deny service, safely |
| Target changes after approval | Fresh validation before consumption and a second final TOCTOU gate | Real Windows handles/ACL semantics are not implemented yet |
| Main Agent or Broker crashes | Active work becomes INTERRUPTED and request becomes CONSUMED; no auto-resume | Manual diagnosis is required |
| Audit or database is unavailable/corrupt | Typed fail-closed errors before authority/execution | Availability is sacrificed for integrity |
| Defined future action accidentally executes | Mock registry contains only service Start/Stop; all other action types reject | Registry changes require security review and tests |
| Stage 4X1 is mistaken for real elevation | Disabled default, Mock-only type/Preview/UI/result wording, no privileged OS adapter | A developer can still misunderstand a synthetic test without docs |

## Stage 4D4 threats and mitigations

| Threat | Control | Residual risk |
|---|---|---|
| Old report/acknowledgement is treated as deletion authority | D4 request is UUID intent only; new Fresh models, new plan and independent durable confirmations | User can select the wrong report row, so the Preview must remain specific |
| LLM or UI injects a path, command or force-delete action | Write schema is reference-only; local repository resolves exact path; finite action enum has only Recycle Bin | Same-process compromise is outside this boundary |
| Owned configuration/database/user data is removed | Eligibility is separate from ownership; protected class matrix blocks regardless of HIGH confidence or user wording | Classification rules may be conservative and block safe items |
| Old path is recreated with new content | Old `lstat` identity plus fresh File ID/type/size/mtime and complete tree digest compared on three scans | A kernel-level race after the last check is out of scope |
| Parent/sibling scope is silently added | Request contains explicit candidate UUIDs; selected path must remain under exact evidence root; plan paths equal selected rows only | Selecting a directory intentionally includes its scanned descendants |
| Forbidden data is hidden inside an eligible directory | Every descendant is metadata-classified/protected; any forbidden class/protection/reparse blocks the whole tree | Classification cannot prove semantic value without reading content, so unknown blocks |
| Shared/recent/network/removable data is treated as recoverable | Independent path/activity/capability policies fail closed | Windows/filesystem capability reporting remains a platform dependency |
| Mixed batch silently cleans the eligible subset | Assessment retains all rows and Preview compiler requires `all_eligible` | User must deliberately create a new reduced selection |
| Confirmation replay/double click or evidence drift | Expiring parent+runtime records bind all evidence digests and are atomically consumed once | Corrupt audit/transaction storage disables execution rather than recovering authority |
| Recycle Bin failure falls back to permanent delete | Shared executor has one RecycleBinPlatform dependency; AST/source tests reject permanent/registry/shell APIs | Windows may fail after partial internal work; verification reports UNKNOWN/FAILED |
| Shell success is falsely reported as cleanup success | Post-call identity inspection distinguishes old identity, absent path and new object at same path | A post-verification concurrent recreation may occur later and is not the removed residual |
| Cancellation is described as Undo | Per-item durable state reports completed/failed/skipped; cancellation checks only before future calls | An in-flight Windows Shell operation cannot be recalled |
| Crash replays remaining work | Startup marks active transactions INTERRUPTED and approvals EXPIRED; no resume edge | Outcome of an abrupt crash during Shell activity may require manual inspection |
| Audit leaks local paths or Recycle Bin identifiers | D4 audit stores UUIDs, counts, path/evidence digests and booleans; raw paths/contents/Shell text excluded | Local software names and aggregate sizes remain sensitive metadata |
| Restore overwrites a new object | D4 provides no automatic restore API; MANUAL Windows Recycle Bin recovery is explicit | Windows/user must resolve restore conflicts manually |

## Stage 4D3 additions

| Threat | Control | Residual risk |
|---|---|---|
| Model/user requests full-drive or name-based search | Scope is compiled only from durable exact uninstall evidence; reviewer rejects changed/unregistered tools | External uninstall without captured context cannot receive broad analysis |
| Same/similar name is mistaken for ownership | Name-only evidence is LOW and is never used to discover new paths | Windows has no complete application-data ownership database |
| HIGH ownership is mistaken for safe deletion | Protection is an independent deterministic policy; every recommendation is REPORT/PROTECT/REVIEW | A report still requires user judgment and may contain false positives |
| Junction/symlink escapes into credentials or another profile | Root/component reparse checks, `lstat`, no-follow traversal and protected-root policy | Same-user races after the last identity observation remain possible, so this stage never writes |
| File name contains prompt injection | Names remain local untrusted table data; no string becomes a tool or command | A user may still misunderstand a malicious-looking name |
| File/database/config/log contents leak to model/audit | Collectors read metadata only; model payload paths are redacted; audit stores aggregates/digests | User-initiated local export intentionally contains full local paths |
| Huge or inaccessible tree freezes UI | Worker thread, shared object/time/depth bounds, fail-soft issues and cooperative cancellation | Large reports can still take time up to the explicit budget |
| Stale report is reused for cleanup | Report binds plan/context/identity digests; no cleanup tool/token exists; Stage 4D4 must use a fresh flow | Future Stage 4D4 requires a separate threat review |
| Analysis silently calls delete/trash/registry cleanup | Registry has only three R0 tools; destructive-call and source-boundary tests verify zero paths | None inside the implemented Stage 4D3 graph |

## Stage 4D2C1 additions

| Threat | Control | Residual risk |
|---|---|---|
| LLM/user injects flags or a Package Name selects the wrong app | Tool schema has no args/name field; Package ID/version/source/scope are typed; adapter generates the only argv tuple | Package-manager/manifest metadata may itself be wrong |
| Custom or Store source is substituted | Exact official source name and identifier bind identity; Source URL is ignored; no source mutation API exists | Official source availability/cache can fail, producing safe false negatives |
| `winget.exe` is hijacked through PATH or an alias replacement | Fixed WindowsApps path, AppExecLink inspection, Desktop App Installer family binding, SHA-256 and revalidation | Same-user race after the final observation remains a platform residual risk |
| Machine-wide or elevated removal escapes least privilege | Current-user mapping/scope and fixed `--scope user`; elevated Agent blocked; no runas/ShellExecute | A vendor installer may independently request privilege and then fail/require user action |
| winget support bypasses protected software policy | Capability and safety are separate; shared/driver/Windows/security/network/Agent/enterprise/package-manager/unknown remain blocked | Dependency knowledge remains incomplete for allowed R2_HIGH targets |
| Existing apps/services are silently stopped | Read-only preflight; processes warning-only, running services block; no control adapters are injected | The underlying installer may control its own related processes |
| Environment leaks API keys or redirects behavior | Small allow-list drops keys/tokens/PATH/custom winget variables; DEVNULL streams | Same-user child can still read resources permitted by Windows ACLs |
| Exit code 0 is reported as success | Fresh Package and Installed Software inventories are independent; both exact identities must disappear | Inventory updates may lag, so a real removal can remain safely unverified |
| Cancellation kills an installer and corrupts state | Pre-launch cancellation only; after launch stopping monitoring never terminate/kill | User may manually close vendor UI; Agent cannot guarantee installer atomicity |
| Crash causes duplicate removal | Non-terminal transaction becomes INTERRUPTED and gates expire; there is no redispatch edge | External winget/installer process may remain running and needs observation |
| Concurrent MSI/Vendor/winget transactions interfere | All three repositories inspect additive active tables before reservation | External uninstall programs are outside this database lock |
| Residual cleanup deletes data or follows junction | Exact-path `lstat` only, no enumerate/follow/delete APIs | Report intentionally cannot identify all leftovers |
| Prompt injection in package metadata | IDs/names/content are untrusted data, never prompts/commands; UI escapes display text | Malicious display text can still be shown as inert data |

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
This historical Stage 2A boundary did not include installed-software inventory or browser automation.
Stage 2A file mutation remains limited to its documented R1 tools; it cannot trigger recycle-bin/permanent
deletion, overwrite, cross-volume move, system changes, arbitrary commands, Stage 5C browser tools or
privilege elevation through placeholder interfaces.
## Stage 4D2C2 threats and mitigations

| Threat | Mitigation |
|---|---|
| Display-name confusion or LLM-selected PFN | Display text only returns candidates; fresh local WinRT evidence selects exact identity. |
| Store auto-update between Preview and execution | Full Name/version/architecture and all digests are re-read; any change invalidates both approvals. |
| Framework/resource/system removal | Strong WinRT flags, signature kind, protected family policy and default-deny type matrix. |
| All-users or Provisioned scope expansion | Adapter contains only current-user methods; no option/API/model field can request broader scope. |
| Dependency collateral damage | Direct and reverse relationships must be complete; known dependents and possible orphan removal block. |
| Prompt/file-name injection | Package metadata stays untrusted local data and is never interpreted as instructions or arguments. |
| Hidden process/service control | Preflight is read-only; uninstall approval grants neither Stage 4A nor Stage 4C authority. |
| False success from WinRT return | Fresh current-user inventory distinguishes present, removed, and same-family replacement. |
| Crash/replay/double click | Durable single-use confirmations and transaction restart recovery mark active work interrupted, never retry. |
| User-data overreach | No recursive enumeration/deletion; fixed roaming-preservation option and explicit LocalState warning. |
