# Privacy model

## Default position

Windows PC Manager Agent processes local metadata for the user who launched it. Stage 7A does not add telemetry,
automatic crash upload, automatic diagnostic upload, advertising identifiers or background analytics. Production
logging is local, bounded and redacted. A local diagnostic ZIP is created only after the user selects an absent
target, reviews its exact member list and exclusions, and explicitly confirms the R1 export.

This is a privacy boundary, not a claim that pattern matching is complete data-loss prevention. Do not enter
passwords, API keys, MFA codes or private document bodies into goals, labels or error messages.

## Data-flow register

| Domain | Possible input | Destination | Default | Consent and minimization | Durable data |
|---|---|---|---|---|---|
| LLM planning/explanation | Bounded goal/labels, opaque IDs, approved aggregate facts | Configured model provider | Disabled in the private RC | Exact external-disclosure Preview and single-use consent are required where the domain supports disclosure; local paths and file content are excluded | Provider request ID and structural audit metadata only; API keys and payload bodies are excluded |
| Speech to text | One bounded Push-to-Talk PCM recording | Separately configured STT provider | Disabled in the private RC | Exact one-time upload consent; never inherited from LLM consent | Metadata-only voice journal; no PCM or transcript body |
| Text to speech | Finite safe summary | Separately configured TTS provider | Disabled in the private RC | Exact one-time disclosure consent | Metadata-only result; no generated audio body |
| Browser | Public HTTP/HTTPS page within a disposable session | Ephemeral isolated browser worker and target origin | Disabled in the private RC | Origin-bound plan/confirmation; no private-network access, local upload, profile, cookies or arbitrary requests | Structural IDs/digests only; page bodies are volatile |
| Office | Explicit user-selected document and selected spans | Local static parser; optional configured provider for selected spans | Disabled in the private RC | READ/OUTPUT grants are exact; provider disclosure has its own Preview and consent | Transaction metadata and digests only; document body and Diff are excluded |
| Memory | Closed low-risk preference keys | Local SQLite | Disabled in the private RC | Explicit user confirmation; scoped reads, TTL/versioning and physical value deletion | Only accepted preference values; never credentials, bodies or authority |
| Audit | Plans, decisions, tool/result summaries and stable error codes | Local SQLite | Enabled | Mandatory fail-closed writes around governed actions; recursive credential/content-key and inline-token redaction | Retained until the user clears local application data; recovery evidence must not be casually deleted |
| Application log | Structural event metadata and sanitized exception text | Local rotating JSONL files | Enabled | Central redaction hashes Windows paths and removes known credentials/content fields; production is INFO level | At most six files: current plus five 2 MiB rotations |
| Crash report | Exception type/message and at most 50 hashed frame references | Local crash-report directory | Enabled | No source lines, locals, hostname, username, document body or automatic upload | Newest 10 reports; older Agent-owned reports are pruned |
| Diagnostic bundle | Finite runtime manifest, error codes and structural fields from recent application logs | User-selected local ZIP | Manual only | Exact member Preview, explicit exclusions, expiry, one-use approval and exclusive no-overwrite creation | User-owned ZIP; recovery is MANUAL and the Agent never uploads or deletes it |
| Telemetry | None | None | `NOT_IMPLEMENTED` | No opt-in UI is presented because there is no telemetry implementation | None |

## Local storage and retention

The default application state lives below the current user's local application-data directory. The SQLite database
contains settings, audit and workflow metadata. Migration backups are retained deliberately because they are
recovery evidence. Logs rotate at a bounded size, crash reports keep only the newest ten, and volatile diagnostic
Preview bytes disappear on rejection, expiry, successful export or application exit.

Uninstalling the application must preserve local state by default. A future data-removal option needs a separate,
explicit design because deleting the database can destroy audit and recovery evidence. The Stage 7A installer must
not silently remove user state during upgrade, repair or uninstall.

## Diagnostic ZIP contract

The ZIP contains exactly `manifest.json` and `recent-logs.jsonl`. The manifest contains version/build/safe-mode,
enabled-feature identifiers, provider name, database schema/config versions and digest, generic OS/runtime facts,
signing status, and selected stable error codes. The log member retains only timestamp, severity, component,
event code, trace/task IDs and exception type. Message bodies, details and exception messages are dropped again
even though the source application log is already redacted.

The ZIP explicitly excludes API keys, passwords, cookies, MFA, authorization secrets, confirmation/Broker secrets,
raw audio, transcripts, page/document bodies, browser profiles, user files, absolute paths, SQLite/audit content and
migration backups. The export uses an absent local `.zip` target, never overwrites, verifies the member set and
bytes, records the confirmation/result in audit, and removes the newly created file if mandatory result audit fails.
It does not scan for malware and does not automatically send the ZIP anywhere.

## User controls and incident response

- Use Settings to review the active build mode and create a diagnostic bundle.
- Safe mode disables all capability domains and model providers while retaining bounded audit/diagnostic access.
- To share a ZIP, inspect it first and transmit it manually through a channel you trust.
- If a secret may have appeared in an input or third-party error, rotate that secret. Redaction is defense in depth,
  not a substitute for rotation.
- Do not delete `state.db` or migration backups while an operation or recovery question remains unresolved.

See [logging and diagnostics](release/logging-and-diagnostics.md), [database migration](release/database-migrations.md)
and [security model](security-model.md).
