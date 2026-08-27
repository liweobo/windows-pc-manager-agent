# Stage 4X1/4X2/4X3 Privileged Action Protocol

## Stage 4X3 current capability status

The authenticated one-shot transport remains version 1, while the inner privileged request/result Schema
is version 2. Version 2 adds action Schema version, safety-policy version, immutable manifest digest,
source-transaction binding and action-specific result evidence. Main and Broker must use the same manifest;
schema, policy or manifest drift fails before execution.

The real manifest/handler intersection contains exactly seven actions: Stage 4X2 service Start/Stop plus
service startup-type change/restore, HKLM Run disable/restore and machine MSI uninstall. Restart and machine
Vendor uninstall are not registered. No dispatch fallback exists.

For each Stage 4X3 action the Broker rebuilds its business evidence twice: once before consuming authority
and once immediately afterward. Service startup reloads identity/config/runtime/dependency impact/safety and
the encrypted backup/history. HKLM reloads exact identity/view/value or absent state, backup/history and
machine-startup policy. MSI reloads complete software inventory, Windows Installer registration,
protected-class policy, process/service preflight and global uninstall activity. Only then can the dedicated
narrow handler run.

Result evidence is discriminated by action: service runtime/config state, HKLM value presence plus view, or
MSI installer category and fresh registration/software presence. A `VERIFIED` result without matching typed
evidence is invalid. Main independently reads the final target and may downgrade Broker success.

## Stage 4X2 current real-Broker status

Stage 4X2 originally implemented a real Windows privilege boundary for exactly two actions:
`SERVICE_START` and `SERVICE_STOP`. Stage 4X3 leaves that handler unchanged and adds separate handlers.
It does not convert the Stage 4X1 Mock Broker into a generic privileged process. Instead, the
standard-user Main application retains plan construction, safety review, confirmation and independent
verification, while a separately packaged one-shot Broker owns the final authorization checks and one SCM
dispatch.

The real route is disabled by default and is never selected merely because an API returns AccessDenied.
The source Stage 4C1 Preview must already prove an exact signed third-party service, allowed safety class,
safe dependency impact, complete query evidence, a non-elevated Main token and insufficient ordinary rights
for only the requested Start or Stop. Restart still has no real handler.

### Pre-UAC trust and launch

The Main process inspects one configured absolute `.exe`: no PATH resolution, reparse point or alternate
extension is accepted. It binds file identity, SHA-256, product version, adjacent `asInvoker` manifest and
signature/install evidence. Development mode accepts an explicit hash pin; production mode additionally
requires a trusted installation root, valid Authenticode and pinned signer fingerprint. Missing release
signing is therefore a truthful `NOT_READY`, not a reason to weaken policy.

`ShellExecuteExW` uses the `runas` verb, exact executable and opaque arguments only:

```text
--broker-instance <UUID>
--rendezvous <43-character random ID>
--protocol-version 1
--caller-pid <expected Main PID>
--agent-instance <UUID>
```

The request, service name, database path, payload and action are never placed on the command line. The
Broker independently uses the fixed `platformdirs` current-user database location. Real mode rejects a
custom Main data directory before UAC.

### Named-pipe authentication

The pipe name is derived only from the opaque rendezvous ID. The server uses an explicit DACL for the exact
current user plus LocalSystem, rejects remote clients and requests the first pipe instance. A four-byte
little-endian length prefix is checked before allocation; strict JSON rejects duplicate keys, non-finite
numbers, unknown fields and wrong protocol versions.

The only successful sequence is:

| Sequence | Direction | Message | Authentication and purpose |
|---:|---|---|---|
| 0 | Broker → Main | `BROKER_READY` | Binds launched Broker PID/session/version/binary digest and challenge; later transcript-bound |
| 1 | Main → Broker | `CLIENT_HELLO` | Binds Agent instance, Main OS identity, launch-ticket digest, version and challenge |
| 2 | Broker → Main | `SESSION_GRANT` | One 32-byte/30-second HMAC session key after expected caller PID/SID/session checks |
| 3 | Main → Broker | `CLIENT_PROOF` | HMAC proof over both challenges/transcript |
| 4 | Main → Broker | `REQUEST` | Session-HMAC authenticated exact Stage 4X1 envelope and launch-ticket digest |
| 5 | Broker → Main | `RESULT` | Session-HMAC authenticated typed result and postcondition evidence |

The Broker impersonates the actual pipe client and compares the token SID/session, pipe PID and full process
identity with the expected standard-user Main. Main compares the pipe server PID/session with the exact
process returned by `ShellExecuteEx`. Same-user/same-session is mandatory; over-the-shoulder elevation to a
different account is deliberately unsupported.

### Broker authorization and execution order

After transport authentication, the Broker performs this fixed order:

1. Revalidate request digest/session integrity, protocol/action/payload allow-list and all route bindings.
2. Load the fixed SQLite authorization snapshot and require unexpired `SIGNED`/`CREATED` authority.
3. Revalidate Plan, Preview, both confirmations, caller/Agent, execution mode and target digests.
4. Query the exact service and repeat stable identity, expected state, startup configuration, dependency,
   Stage 4C1 safety and action-specific permission/relationship checks.
5. Write mandatory validation audit.
6. Atomically consume the request and both confirmations.
7. Repeat final service TOCTOU validation and write mandatory pre-execution audit.
8. Call exactly one typed `WindowsServiceControlPlatform.start()` or `.stop()` operation.
9. Read the exact post-state in Broker; send one authenticated result.
10. Main independently reads SCM and accepts success only when identity and expected state agree.
11. Main 有界等待并记录 Broker 自然退出码，再关闭 pipe/handle；超时不会 TerminateProcess，而会把
    结果降为未完全验证。No loop、queue、retry、resume 或 resident service 存在。

Any uncertainty fails closed. A failure before launch invalidates unconsumed authority; a failure after
consumption becomes terminal/interrupted. UAC cancellation never retries. Broker process exit or API return
alone is not success.

### Recovery, audit and production limitation

Start/Stop rollback is `MANUAL`: an opposite action needs a fresh service inventory, new Plan, two new
confirmations and a new UAC. The Agent never sends an automatic opposite control because service state or
dependencies may have changed.

Stage 4X2 audit separates lifecycle, validation, execution-start and verification. It records action and
correlation IDs, protocol/version、Plan/Preview/confirmation、risk/privilege、caller SID fingerprint、
Windows logon ID、Broker binary/version、IPC endpoint fingerprint、UAC/handshake/replay/safety/consumption/
execution/Broker verification/Main readback/result-integrity/exit/final-state 结论；不记录 raw SID、服务
name/payload、pipe name、nonce、HMAC/session key、command line 或 provider secret。Audit/storage failure
stops authorization or produces a conservative unverified outcome.

Automated tests use fake launch/SCM adapters plus a real non-elevated same-user Named Pipe. They never show
UAC or mutate a service. A production-ready release remains blocked until the separate Broker is installed
in a trusted location and signed by a pinned release signer.

## Stage 4X1 baseline and historical non-goals

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

Privileged request Schema version 2 defines these finite action types:

| Action | Payload | Real Broker status |
|---|---|---|
| `SERVICE_START` | exact service identity, expected STOPPED/config/dependency evidence | Stage 4X2 handler |
| `SERVICE_STOP` | exact service identity, expected RUNNING/config/dependency evidence | Stage 4X2 handler |
| `SERVICE_RESTART` | exact service identity and expected evidence | Defined only; rejected |
| `SERVICE_STARTUP_TYPE_CHANGE` | exact service identity, current config/runtime, Automatic/Manual target, backup/impact/safety digests | Stage 4X3 handler |
| `SERVICE_STARTUP_TYPE_RESTORE` | exact Agent change history, current/wanted config, runtime, backup/impact/safety digests | Stage 4X3 handler |
| `STARTUP_MACHINE_DISABLE` | exact 32/64-view HKLM Run identity, current state, safety and backup digests | Stage 4X3 handler |
| `STARTUP_MACHINE_RESTORE` | exact original disable/history/identity/view/absent-state and backup digests | Stage 4X3 handler |
| `MSI_UNINSTALL_MACHINE` | exact ProductCode, software/capability/registration/policy/preflight/source digests | Stage 4X3 handler |

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
| `PC_MANAGER_PRIVILEGED_BROKER_MODE` | `disabled` | `disabled`, `mock`, `windows`; intentionally no generic `real` |
| `PC_MANAGER_PRIVILEGED_REQUEST_TTL_SECONDS` | `120` | 15–600 |
| `PC_MANAGER_PRIVILEGED_RUNTIME_CONFIRMATION_TTL_SECONDS` | `60` | 15–300 |
| `PC_MANAGER_PRIVILEGED_MAX_REQUEST_BYTES` | `32768` | 1024–1048576 |
| `PC_MANAGER_PRIVILEGED_BROKER_PATH` | unset | Required absolute `.exe` only for `windows` |
| `PC_MANAGER_PRIVILEGED_BROKER_EXPECTED_SHA256` | unset | Required lowercase 64-character hash only for `windows` |
| `PC_MANAGER_PRIVILEGED_BROKER_TRUST_MODE` | `production` | `production` or explicit `development` |
| `PC_MANAGER_PRIVILEGED_BROKER_CONNECT_TIMEOUT_SECONDS` | `30` | 5–120 |
| `PC_MANAGER_PRIVILEGED_BROKER_MESSAGE_TIMEOUT_SECONDS` | `15` | 2–60 |

Mock runtime composition requires the Main token to be non-elevated and the caller to inject
`FakePrivilegedSystemState`; its UI text/results always state that no UAC or real system operation occurred.
Windows composition is separate, also requires a non-elevated Main token, fixed default user-data directory,
trusted Broker path/hash/policy, and exposes only the Stage 4X2 service Start/Stop bridge.

## Verification

The dedicated CI gate runs unit, persistence, Broker, integration, security and offscreen GUI tests with
at least 95% combined coverage over the Stage 4X1 critical modules. Tests include tampering, duplicate
keys, size/version/schema errors, wrong integrity key, expired/stale confirmations, replay and concurrent
dispatch, crash recovery, corrupt database JSON, audit outage, prompt-injection-shaped names, defined-only
actions, fresh evidence changes, final TOCTOU changes, fake executor/verification failures and forbidden
process/LLM imports.

## Requirements before production release or any broader privileged action

Stage 4X2 has implemented the transport and one narrow adapter, but a production release or any new adapter
still requires explicit evidence for:

- Broker executable installation, code signing, update and downgrade resistance;
- IPC transport, endpoint ACL, message framing and denial-of-service bounds;
- trusted caller process, binary, user SID, logon session and desktop verification;
- cross-process key establishment or Windows-native message authentication;
- UAC prompt ownership, cancellation, timeout and Broker lifetime;
- secure desktop/session changes and fast-user-switching;
- one separately reviewed Windows adapter per action;
- production audit correlation without leaking privileged data;
- recovery semantics and real-Windows disposable integration tests.

Stage 4X2 satisfies these items only for the narrow `windows` service Start/Stop route. It does not authorize
any other action, and production mode remains unavailable until release signing and trusted deployment are
provided. The runtime deliberately has no generic `real` mode.
