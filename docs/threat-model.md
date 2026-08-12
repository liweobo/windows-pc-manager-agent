# Threat model

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
