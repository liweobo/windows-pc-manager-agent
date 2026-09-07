# Stage 5E crash recovery

## Startup behavior

The task repository first runs SQLite `quick_check`, creates/migrates its additive schema, and fails closed on an
unsupported schema or corrupted digest. Every active root task becomes `INTERRUPTED`. Pending/approved task-plan
confirmations become `INVALIDATED`. `DISPATCHING` and `DISPATCHED` records become `RECONCILING`; they never reset
to “not dispatched”. No raw goal, Context body or authority is reconstructed.

## Checkpoint contents

A checkpoint stores task/graph/policy digests, node status, attempt counts, opaque result/transaction references,
pending attention IDs and an invalidated-confirmation count. It cannot store or restore confirmation authority.
The repository verifies a checkpoint after writing it and protects every JSON payload with a SHA-256 digest.

## Reconciliation

The user explicitly starts recovery inspection. For each unresolved dispatch with an owning-domain transaction
reference, Stage 5E calls that domain's high-level `reconcile` method. The method may report completed, not
executed, partial, changed, unverified, manual review or Fresh preparation. Its schema fixes
`action_replayed=false`. A production UI-only handoff therefore returns Fresh domain review rather than claiming
the previous action state.

Process IDs and browser DOM references are immediately invalid. Files, documents, startup items, services and
cleanup candidates require Fresh identity resolution. Optimization reports are context only. Software inventory
and system observations use short bounded lifetimes, but any write still requires Fresh resolution.

Recovery always creates a visible attention item and stops in `WAITING_FOR_USER`. The user may revise the plan,
cancel remaining steps, or re-enter an owning domain and complete its new independent workflow. There is no
automatic retry, redispatch, confirmation restoration, external uninstaller kill, Broker resume or global Undo.

## Failure handling

SQLite integrity, audit/storage, graph binding, receipt ownership or reconciliation errors stop the related step.
Existing domain evidence is retained. The application must report what is known, unknown and already changed; it
must not infer success from process exit or missing objects without the owning domain's required verification.
