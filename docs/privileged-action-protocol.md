# Stage 4X1 Privileged Action Protocol

## Status and non-goals

Stage 4X1 implements a versioned authorization protocol and a complete in-process Mock Broker. It proves
that an exact operation can be represented, confirmed, authenticated, consumed once, revalidated,
simulated, verified and audited without giving an LLM or UI direct execution authority.

It does **not** implement a Windows elevated Broker, UAC, an administrator process, an IPC endpoint, SCM
writes, HKLM writes or machine MSI removal. The main application remains a standard-user process. The
only state mutation is an injected in-memory fake service used by tests.

## Permission resolution

`PrivilegeRequirementResolver` runs after action-specific safety policy. Its result is one of:

| Status | Meaning | Routing |
|---|---|---|
| `NOT_REQUIRED` | Current standard token already has the explicitly checked access | Use the existing ordinary Stage 4C/4D path |
| `REQUIRED` | Complete preflight says the safe action specifically needs Administrator | May prepare a Stage 4X1 Mock request |
| `UNSUPPORTED` | SYSTEM or TrustedInstaller-like authority is needed | Stop |
| `BLOCKED` | Safety denied the target or the main Agent is elevated | Stop and audit |
| `UNKNOWN` | Evidence is incomplete or only an access failure is known | Stop; do not guess elevation |

Safety has precedence over privilege. A denial cannot be converted to `REQUIRED`. Error 5 / AccessDenied
alone does not identify the cause and therefore cannot authorize escalation.

## Protocol action language

Protocol version 1 defines these finite action types:

| Action | Payload | Stage 4X1 execution status |
|---|---|---|
| `SERVICE_START` | exact service identity, expected STOPPED/config/dependency evidence | Mock allow-listed |
| `SERVICE_STOP` | exact service identity, expected RUNNING/config/dependency evidence | Mock allow-listed |
| `SERVICE_RESTART` | exact service identity and expected evidence | Defined only; rejected |
| `SERVICE_STARTUP_TYPE_CHANGE` | exact service identity, current config, Automatic/Manual target, backup/impact digests | Defined only; rejected |
| `STARTUP_MACHINE_DISABLE` | exact HKLM Run identity and state digests | Defined only; rejected |
| `STARTUP_MACHINE_RESTORE` | exact identity/state and backup digests | Defined only; rejected |
| `MSI_UNINSTALL_MACHINE` | exact ProductCode, normalized software and registration digests | Defined only; rejected |

Each action has a separate strict Pydantic model with `extra=forbid`. No payload has a `command`,
`script`, `shell`, `executable`, `executable_path`, `args`, raw registry value or generic argument mapping.
Adding an enum member does not make it executable: a separately reviewed manifest and handler are also
required in the private privileged registry.

## Canonical request

One `PrivilegedActionRequest` contains:

- protocol/request/action identifiers;
- one discriminated typed payload and its SHA-256 digest;
- exact target identity digest;
- canonical Plan ID/hash and Preview ID/hash;
- plan and immediate confirmation IDs;
- object-summary, risk and privilege bindings;
- Agent instance and out-of-band caller-context references;
- a 32-byte URL-safe random nonce;
- explicit UTC creation and expiry timestamps, with a maximum 10-minute lifetime.

All digest fields accept lowercase 64-character SHA-256 hex only. JSON uses UTF-8, sorted keys, compact
separators, no NaN and one deterministic model representation. Before parsing, the Broker applies a
configurable 1 KiB–1 MiB byte bound (32 KiB default), rejects invalid UTF-8, duplicate keys, a non-object
root, unknown/old/new protocol versions, unknown fields and invalid model values.

The envelope stores SHA-256 of the canonical request plus a `RequestIntegrity`. The Stage 4X1 injected
implementation signs canonical bytes with HMAC-SHA-256 and verifies with constant-time comparison. Its
random key is process-local, never persisted and never logged. This is a Mock implementation seam—not a
claim of secure authentication between real standard-user and elevated processes.

## Confirmation and replay lifecycle

1. The Main side persists a canonical R3 plan and Mock-only fresh Preview.
2. A plan confirmation is created, shown and resolved.
3. The target is inspected again and a new Preview is persisted.
4. A separate short-lived runtime confirmation is created, shown and resolved.
5. Only an unexpired, exact approved pair permits request construction and registration.
6. The Broker authenticates and validates without consuming the request.
7. One SQLite transaction changes request `CREATED -> CONSUMING` and both confirmations
   `APPROVED -> CONSUMED`.
8. Every later outcome leaves authority consumed. There is no retry or re-open path.

The database has unique constraints on request ID, request digest and nonce fingerprint. It stores only a
SHA-256 nonce fingerprint, not the nonce itself. Concurrent Broker calls race on an atomic conditional
update; exactly one can claim the request. On application startup, active `SIGNED`, `VALIDATING`,
`CONSUMING`, `EXECUTING` or `VERIFYING` transactions become `INTERRUPTED`, and an associated request
becomes `CONSUMED`. Restart never redispatches.

## Broker validation order

The Mock Broker performs this fixed order:

1. size, JSON, duplicate-key, version and schema validation;
2. canonical digest and integrity verification;
3. request expiry;
4. durable replay/transaction state;
5. private action allow-list and exact payload-model match;
6. out-of-band caller/Agent-instance match;
7. durable Plan, Preview and both confirmation bindings;
8. fresh action-specific target identity/state/config/dependency, safety, risk and privilege checks;
9. mandatory pre-consumption Broker validation audit;
10. atomic request/confirmation consumption;
11. final action-specific TOCTOU validation;
12. mandatory pre-execution Mock audit;
13. finite fake service Start or Stop;
14. fresh fake-state postcondition verification;
15. terminal persistence, verification audit and authenticated result.

Any failure before consumption denies execution. Any failure after consumption remains terminal. Audit or
authorization persistence failure maps to `PERSISTENCE_UNAVAILABLE`. The Broker never asks a model for a
decision and never falls back to an ordinary tool, shell or another action type.

## Audit and privacy

Four event types separate authority and outcome:

- `MAIN_AUTHORIZATION_EVENT`
- `BROKER_VALIDATION_EVENT`
- `BROKER_EXECUTION_EVENT`
- `BROKER_VERIFICATION_EVENT`

Events contain protocol/action/request IDs, target/Plan/Preview/request digests, confirmation IDs, risk,
privilege, times and the nonce fingerprint. They exclude the typed payload, service name, caller SID/session
fingerprints, raw nonce, HMAC/key material, commands and model content. Mandatory write failure stops the
current authorization path.

## Runtime configuration

| Environment variable | Default | Allowed values / bound |
|---|---:|---|
| `PC_MANAGER_PRIVILEGED_BROKER_MODE` | `disabled` | `disabled`, `mock` only |
| `PC_MANAGER_PRIVILEGED_REQUEST_TTL_SECONDS` | `120` | 15–600 |
| `PC_MANAGER_PRIVILEGED_RUNTIME_CONFIRMATION_TTL_SECONDS` | `60` | 15–300 |
| `PC_MANAGER_PRIVILEGED_MAX_REQUEST_BYTES` | `32768` | 1024–1048576 |

Mock runtime composition requires the main token to be non-elevated and the caller to inject
`FakePrivilegedSystemState`; there is no factory that creates a real Windows privileged adapter. UI text
and results always state that no UAC or real system operation occurred.

## Verification

The dedicated CI gate runs unit, persistence, Broker, integration, security and offscreen GUI tests with
at least 95% combined coverage over the Stage 4X1 critical modules. Tests include tampering, duplicate
keys, size/version/schema errors, wrong integrity key, expired/stale confirmations, replay and concurrent
dispatch, crash recovery, corrupt database JSON, audit outage, prompt-injection-shaped names, defined-only
actions, fresh evidence changes, final TOCTOU changes, fake executor/verification failures and forbidden
process/LLM imports.

## Requirements before a real Broker

A future stage needs a new threat model and decision record for:

- Broker executable installation, code signing, update and downgrade resistance;
- IPC transport, endpoint ACL, message framing and denial-of-service bounds;
- trusted caller process, binary, user SID, logon session and desktop verification;
- cross-process key establishment or Windows-native message authentication;
- UAC prompt ownership, cancellation, timeout and Broker lifetime;
- secure desktop/session changes and fast-user-switching;
- one separately reviewed Windows adapter per action;
- production audit correlation without leaking privileged data;
- recovery semantics and real-Windows disposable integration tests.

Until all of these are designed and tested, `real` is not an accepted runtime mode.
