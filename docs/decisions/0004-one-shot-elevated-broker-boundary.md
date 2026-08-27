# ADR 0004: One-shot elevated Broker for exact service control

- Status: Accepted for Stage 4X2; production deployment pending release signing
- Date: 2026-08-27

## Context

Stage 4C1 can prove that one exact third-party service Start or Stop is safe while the current standard-user
token lacks only the requested SCM control right. Elevating the full GUI would unnecessarily expose model,
provider, UI and broad application code to administrator privileges. Passing commands or payloads on an
elevated command line would also create an unsafe general execution channel.

## Decision

Keep the Main application standard-user and launch a separately packaged, `asInvoker`, one-shot Broker only
through an explicit `runas` request after independent R3 plan and immediate confirmations. Bootstrap with
opaque instance/rendezvous identifiers only. Authenticate both OS endpoints over a current-user-only local
Named Pipe, establish a short-lived transcript-bound HMAC session, consume the durable request once, repeat
all safety/identity/state checks, execute one allow-listed SCM Start or Stop, verify, return one authenticated
result and exit.

Development trust may use an explicit SHA-256 pin. Production must also require a trusted install location,
valid Authenticode and an expected signer identity. Until a release signing/install pipeline supplies these,
production mode remains unavailable. No generic action, shell, arbitrary executable, retry, daemon,
over-the-shoulder account, Restart or automatic rollback is permitted.

## Consequences

- The privileged attack surface excludes GUI/model/provider code and is finite enough for action-specific
  review and testing.
- UAC cancellation, transport interruption and stale evidence require a new user workflow; availability is
  sacrificed for integrity.
- Service Start/Stop recovery is MANUAL and needs a fresh opposite plan rather than replay or Undo.
- The Main and Broker must use the fixed per-user authorization database path.
- Each future privileged action requires a new ADR/threat model/handler and cannot reuse this decision as
  blanket approval.

## Rejected alternatives

- Running the entire desktop application elevated.
- A resident administrator service or auto-start Broker.
- Commands, executable paths, service names or request JSON on the Broker command line.
- Generic RPC action dictionaries, Shell, PowerShell, CMD or subprocess fallbacks.
- Trusting payload-supplied SID/PID/session evidence without Windows token and pipe verification.
- Automatically retrying UAC, IPC or SCM operations.
