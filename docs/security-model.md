# Security model

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
