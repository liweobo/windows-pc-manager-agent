# Threat model

## Protected assets

- User files and metadata.
- Credentials, tokens, browser sessions, private keys, and wallet material.
- Windows integrity and user privilege boundary.
- OpenAI API credentials and cost controls.
- Audit integrity and confirmation meaning.
- Git repository and release pipeline integrity.

## Principal threats and mitigations

| Threat | Example | Stage 0 mitigation | Residual risk |
|---|---|---|---|
| Prompt injection | A filename asks the Agent to delete data | Scanner never interprets names; tool execution is registry-only | Future content analysis needs strict data separation |
| Scope escape | `..`, symlink or junction targets another directory | Traversal rejection, root resolution, lexical child checks, reparse skip, directory file-ID recheck | A very narrow check/open race remains; no write occurs |
| Confused deputy | Model invents `shell.run` | Unknown tools fail safety review and registry lookup | Provider availability does not grant execution authority |
| Stale consent | Arguments change after approval | Full-plan and argument SHA-256 binding with expiry | In-memory confirmations disappear on restart |
| Credential leakage | API key enters audit data | Secret environment loading, excluded field, recursive redaction | User can still paste secrets into ordinary text; inline patterns are best effort |
| Resource exhaustion | Huge or cyclic directory tree | No link following, timeout, file cap, cancellation | Very slow filesystem calls can delay cooperative cancellation |
| Audit bypass | Corrupt or unwritable SQLite | Initialization and append failures stop execution | Device-level compromise is outside this personal MVP |
| Privilege abuse | Tool requests administrator rights | No elevation API and R3 denial | Future privileged broker requires a separate review |
| Supply-chain drift | Action tag changes remotely | CI actions pinned to commit SHA, locked Python dependencies | Dependencies still require periodic vulnerability review |

## Explicitly excluded adversaries

Stage 0 does not claim protection against a compromised Windows kernel, an
attacker already running as the user who can modify this process, physical disk
attacks, or malicious dependency infrastructure. These are documented residual
risks, not reasons to weaken application-level controls.

## Security test expectations

Tests cover traversal, protected roots, reparse classification, scope expansion,
unknown tools, schema failures, risk mismatch, confirmation mutation/expiry,
audit redaction/unavailability, cancellation, limits, and malformed provider
output. A real Windows reparse integration test may skip where the runner cannot
create a symlink; deterministic attribute-path tests must still run.
