# Threat model

## Protected assets

- User files, file metadata, and the meaning of “authorized directory”.
- Credentials, browser sessions, private keys, wallets, and Windows security stores.
- Windows integrity and the ordinary-user privilege boundary.
- Model API credentials, disclosure consent, and cost control.
- Audit/confirmation integrity and local result correctness.
- Repository, dependencies, CI, and release integrity.

## Threats, controls, and residual risk

| Threat | Example | Stage 1 controls | Residual risk |
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

## Security verification

Tests cover authorization lifecycle, protected paths, traversal, UNC/network and Windows
name ambiguity, scope mismatch, symlink/reparse behavior, changed directory/file identity,
permission errors, count/timeout/cancellation, plan and threshold mutation, unknown tools,
external payload mutation/expiry, provider schema errors, audit redaction/unavailability,
duplicate content and hash cancellation, report no-overwrite, GUI invalidation, and rollback
records. Overall core coverage is enforced at 85%; the combined high-risk boundary suite is
enforced at 95%. A real symlink test may skip where Windows denies symlink creation, while
deterministic reparse branches remain tested.

## Explicit exclusions

The MVP does not claim protection against a compromised kernel, an attacker already able
to modify this process, physical disk attacks, or malicious dependency infrastructure.
Installed-software inventory is not yet implemented. File mutation, recycle-bin actions,
system changes, arbitrary commands, browser automation, and privilege elevation remain
outside Stage 1 and cannot be triggered through placeholder interfaces.
