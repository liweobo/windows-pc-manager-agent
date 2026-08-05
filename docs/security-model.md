# Security model

## Trust boundaries

Trusted deterministic code owns path authorization, schemas, risk, confirmation,
execution, verification, audit, and rollback claims. Model output, UI text,
filenames, file contents, documents, and webpages are untrusted.

## Risk policy

| Level | Meaning | MVP execution |
|---|---|---|
| R0 | Read-only | Allowed inside a confirmed, reviewed scope |
| R1 | Reversible data change | Framework only in stage 0 |
| R2 | Destructive but recoverable | Not implemented in stage 0 |
| R3 | High-risk system change | Denied in MVP 0.1 |
| R4 | Prohibited behaviour | Always denied and audited |

The only stage 0 tool is `file.scan`: R0, current-user read permission,
metadata-only, cancellable, no rollback required, maximum 100,000 files, and no
link following.

## Path controls

- An explicit approved root is mandatory.
- `..` traversal is rejected before resolution.
- The root must exist, be a directory, and not be a symlink/junction/reparse point.
- Child paths are checked lexically before metadata access.
- Reparse points are skipped rather than resolved.
- Other user profiles, browser profiles, SSH keys, password managers, Windows
  security databases, Personal Vault, the recycle bin, System Volume Information,
  and configured forbidden roots are skipped.
- The scanner revalidates at execution and does not read file content.
- Every discovered directory is bound to its volume/file identifier and checked
  again immediately before enumeration; changed identities stop that branch.
- Cancellation, timeout, and a hard file-count bound constrain resource use.

## Confirmation

Plan confirmation includes a SHA-256 digest of the full immutable plan. Runtime
confirmation additionally includes the step and a digest of its arguments. Every
request expires. Changing a plan or arguments invalidates existing approval.

## Credentials and external data

OpenAI credentials are read from `OPENAI_API_KEY`, wrapped as a Pydantic secret,
excluded from dumps, and never written to logs. No model is contacted when the
provider is disabled or incomplete. Stage 0 does not send chat text, paths,
filenames, or file contents to a provider.

Audit redaction recursively removes credential-like keys and common inline secret
assignments. `.gitignore`, pre-commit private-key detection, and CI secret scanning
provide additional controls; they do not replace review.

## Failure behaviour

- Unknown tools, invalid schemas, risk mismatch, rollback mismatch, stale
  confirmation, scope expansion, and R3/R4 steps fail closed.
- An unavailable audit database prevents tool execution.
- Scanner filesystem errors are recorded per object; they never broaden scope.
- The application never requests administrator privileges.
