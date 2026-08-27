# Stage 4X2 manual UAC validation

This procedure is intentionally excluded from automated tests because it displays secure-desktop UAC and
changes a real Windows service state. Use a disposable Windows 11 virtual machine and a purpose-built,
signed, non-critical current-user test service. Never use a system, Microsoft, security, network, login,
storage, update, enterprise or Agent service.

## Preconditions

1. Run the Main application as a normal user, not “Run as administrator”.
2. Install a release-signed Broker under a trusted Program Files directory with an adjacent `asInvoker`
   manifest. Record its SHA-256 and signer fingerprint through the release process.
3. Ensure the test service is own-process, third-party signed, uses the current user account, has no running
   dependents and passes Stage 4C1 policy. Grant the test user query rights but not the chosen Start/Stop
   control right.
4. Back up the VM or create a snapshot. Capture the initial service state with a read-only tool.
5. Configure `PC_MANAGER_PRIVILEGED_BROKER_MODE=windows`, the exact Broker path/hash and production trust.
   Do not set a custom `PC_MANAGER_DATA_DIRECTORY`.

## Expected successful path

1. Open Services in the app and choose exactly one Start or Stop action.
2. Verify the ordinary Preview says safety passed but standard-user control permission is missing.
3. Approve the new R3 plan. Confirm that UAC has not appeared yet.
4. Review the fresh immediate confirmation and click the explicit UAC button once.
5. Verify Windows identifies the expected signed Broker publisher and exact action context; approve UAC.
6. Confirm the UI reports success only after both Broker and Main readbacks agree.
7. Confirm the Broker process exits and no elevated Agent/Broker process remains.
8. Review local audit events. They should correlate IDs/digests and lifecycle without raw SID, service name,
   pipe/rendezvous ID, nonce, request payload, HMAC key or provider secret.

## Required negative checks

- Cancel UAC: no service change, no second prompt, request/confirmations unusable.
- Change Broker bytes or hash: fail before UAC.
- Use unsigned/untrusted Broker in production mode: `NOT_READY`, no UAC.
- Start Main elevated: route blocked.
- Change service identity, state, startup configuration or dependencies after immediate Preview: Broker
  rejects; there is no retry.
- Double-click dispatch: at most one Broker attempt.
- Disconnect/terminate the Broker during IPC: transaction becomes interrupted/consumed; no redispatch.
- Attempt Restart or another protocol action: no real handler exists and no UAC execution occurs.

## Recovery and evidence

Start/Stop recovery is `MANUAL`. Refresh current state and, if still safe, create a new opposite action with
new confirmations and a new UAC. Never reuse the old request. Save test screenshots and audit exports outside
the repository and remove them after review; do not commit user or security data.
