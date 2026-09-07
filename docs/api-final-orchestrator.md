# Stage 5E API reference

This document describes every Stage 5E production function and method. Models are immutable Pydantic objects;
validation errors mean the requested state must not be persisted or dispatched. “Reference” means an opaque local
identifier, never a command or confirmation capability.

## `config/tasks.py`

### `FinalTaskLimits.require_consistent_worker_limits()`

Runs after settings validation. It ensures `max_concurrent_reads` cannot exceed the total background-worker limit.
It returns the validated immutable settings object or raises `ValueError`. It has no I/O.

## Domain models

### `TaskPolicySnapshot.canonical_digest()`

Serializes the exact policy/tool-registry/Agent-capability/schema/application versions in canonical sorted JSON and
returns SHA-256. The digest binds a plan, confirmation and checkpoint to the policy version reviewed by the user.

### `TaskBudgetUsage.exceeded(budget)`

Compares each monotonic usage counter with its immutable ceiling. It returns stable
`TASK_BUDGET_EXCEEDED_*` reason codes for every exceeded field. Equality is allowed; a value strictly above a limit
pauses coordination. It performs no scheduling itself.

### `TaskProgress.require_truthful_counts()`

Rejects completed/failed/blocked totals greater than planned steps and requires an observed-unit count and label to
appear together. It prevents fabricated percentages or unlabeled counters.

### `ComputerTask.require_consistent_lifecycle()`

Validates creation/start/completion ordering and requires the task schema version to equal its recorded policy
schema version. It does not validate a transition; `TaskStateMachine` owns that check.

### `TaskPlanConfirmation.require_live_unique_scope()`

Requires at least one unique R0 node, a future expiry, and consistent pending/resolved timestamps. The model fixes
`domain_write_authorized` to false, so task-plan consent cannot encode write authority.

### `TaskPlanConfirmation.canonical_digest()`

Returns the SHA-256 of the complete canonical confirmation record, including task/graph/policy/scope bindings,
node IDs, state and expiry. It is used for storage/audit integrity, not as a bearer token.

### `ComputerTaskEvent.require_safe_event_metadata()`

Requires unique detail codes and unique 64-character reference digests. It prevents raw identifiers or duplicate
ambiguous evidence from entering the content-free event journal.

### `PersistedTaskGraph.require_exact_graph_membership()`

Requires non-empty unique node IDs and dependency endpoints that exist in the same graph version. Raw goal text is
not a field on this model.

### `TaskCheckpoint.require_unique_content_free_state()`

Rejects duplicate node/result/dispatch/attention references, unknown completed nodes and any
`authorization_restored=true` claim. A checkpoint can restore knowledge only.

### `TaskNodeDispatch.require_consistent_dispatch()`

Rejects backwards timestamps and more than one attempt for a write-capable dispatch. Stage 5E production
dispatches are read-only high-level handoffs, but the stricter model also protects future adapters.

### `UserAttentionItem.require_safe_attention_state()`

Fixes `notification_can_authorize` to false and checks that only resolved/dismissed/invalidated items have a
resolution timestamp. An attention item is presentation state, never consent.

### `StructuredTaskSummary.prohibit_global_undo_and_duplicate_facts()`

Rejects `global_undo_available=true` and duplicate fact codes. Recovery remains a list of owning-domain recovery
summaries.

### `DomainPreparationResult.prohibit_execution_authority()`

Rejects any execution-authority claim and requires R1/R2/R3 preparations to retain their domain confirmation.
An R0 domain may still request a user review.

### `DomainResultReceipt.require_verification_consistency()`

Requires `COMPLETED_VERIFIED` to include `VERIFIED` status and deterministic verification reference. It rejects a
`COMPLETED_UNVERIFIED` result carrying a contradictory verified label.

### `DomainReconciliationResult.prohibit_replay()`

Rejects `action_replayed=true`. Crash reconciliation may observe current truth but cannot redispatch work.

## Safety policies

### `SafeTaskSummaryPolicy.summarize(goal, fallback="受控电脑任务")`

Normalizes whitespace/Unicode, checks known secret patterns, and returns either a maximum-240-character local
label or the generic fallback. It is display minimization, not comprehensive data-loss prevention.

### `TaskPlanConfirmationPolicy.request(task, graph, scope_digest, ttl_seconds, now=None)`

Requires the task to be awaiting confirmation and the exact graph version/digest to match. It extracts only R0,
non-execution node IDs and binds task, graph, goal, policy, scope and expiry. It raises
`FinalOrchestratorSafetyError` for stale, empty or malformed input.

### `TaskPlanConfirmationPolicy.resolve(confirmation, task, approved, now=None)`

Resolves exactly one pending confirmation. Expired consent becomes `EXPIRED`; binding drift fails closed;
otherwise the result is `APPROVED` or `REJECTED`. It does not start a task or approve a domain action.

### `ActionContinuationPolicy.decide(preparation, task_plan_confirmed)`

Returns one finite routing decision: stop, continue an already-confirmed R0 read, wait for the user, or wait for
the owning domain's confirmation. A write preparation that omits confirmation raises a safety error.

### `ActionContinuationPolicy.may_retry(risk, read_only, attempts, limit)`

Returns true only for an R0, side-effect-free operation below its bounded retry limit. It never retries a write.

### `GlobalSafetyInvariantGuard.require_guided_autonomy(level)`

Accepts only `EXPLAIN_ONLY`, `PLAN_AND_ANALYZE` and `GUIDED_EXECUTION`. There is no unattended level.

### `GlobalSafetyInvariantGuard.validate_preparation(result)`

Independently rejects execution authority or a write-confirmation downgrade in an adapter result.

### `GlobalSafetyInvariantGuard.validate_receipt(receipt, task_id, node_id, graph_version)`

Requires exact task/node/version ownership before a receipt may affect progress. Domain and graph checks are
performed again by `FinalOrchestrator.record_domain_receipt()`.

### `canonical_scope_digest(values)`

Requires unique scope references, sorts them, joins with an unambiguous NUL separator and returns SHA-256. It lets
the confirmation bind scope without persisting the underlying sensitive paths or selections.

### `TaskRevisionValidator.validate(task, request, current_domains)`

Requires exact task/version ownership, explicit user provenance, known unique domains and separate scope-expansion
approval. A no-op goal revision is rejected. It returns the validated `DomainType` tuple.

### `TaskStalenessPolicy.assess(kind, domain, reference_created_at, now=None, for_write=False)`

Returns object-specific Fresh requirements. PID/DOM references invalidate immediately; file/document/startup/
service/cleanup references require Fresh identity; optimization reports require a new analysis; bounded system/
software observations may remain context only. `for_write=true` forces Fresh resolution.

### `TaskTemplateRegistry.__init__()` / `get(code)` / `list()`

The constructor creates the closed versioned template set. `get` returns one allow-listed template or raises a
safety error. `list` returns stable declaration order. There is no runtime register/replace API.

## High-level domain workflows

### `DomainWorkflow.domain`

Identifies exactly one owning domain. Implementations cannot claim several domains.

### `DomainWorkflow.prepare(request)`

Enters or describes the owning domain's high-level preparation boundary. Its output cannot carry execution
authority. Implementations must not call low-level tools through this interface.

### `DomainWorkflow.reconcile(request)`

Freshly observes an interrupted domain transaction and returns structured current truth without replay.

### `DomainWorkflow.recovery_summary(transaction_ref, rollback_level)`

Returns truthful domain-owned recovery metadata. It does not perform recovery and cannot create global Undo.

### `DomainWorkflowRegistry.__init__(workflows)`

Seals the registry and requires exactly one adapter for all eleven `DomainType` values. Duplicates, missing domains
or extras fail construction.

### `DomainWorkflowRegistry.require(domain)` / `domains()`

`require` returns the exact adapter or raises `DomainWorkflowError`. `domains` returns stable enum order. Neither
method accepts arbitrary tool names.

### `UserInterfaceHandoffWorkflow.__init__(domain)` / `domain`

Creates one production-safe UI-only adapter and exposes its fixed domain.

### `UserInterfaceHandoffWorkflow.prepare(request)`

Checks domain ownership and returns `READY_FOR_REVIEW` plus finite `OPEN_<DOMAIN>_WORKFLOW`. It does not open a
process, execute a tool or confirm an action.

### `UserInterfaceHandoffWorkflow.reconcile(request)`

Checks domain ownership and returns `REQUIRES_FRESH_PREPARATION`. It cannot infer whether an old action succeeded.

### `UserInterfaceHandoffWorkflow.recovery_summary(transaction_ref, rollback_level)`

Preserves the owning domain's rollback level and produces only an `OPEN_<DOMAIN>_RECOVERY` navigation code when
recovery exists. Empty transaction references fail.

### `build_default_domain_workflow_registry()`

Builds the complete immutable production registry using UI-only adapters. It introduces no Windows writer.

## Graph, lifecycle, attention and summary services

### `FinalTaskGraphBuilder.__init__(validator, safe_read_retry_limit)`

Injects the existing deterministic graph validator and bounded R0 retry limit.

### `FinalTaskGraphBuilder.build(goal, domains, kind, version=1)`

Builds `UNDERSTAND -> domain handoff(s) -> SUMMARIZE`. Analysis tasks use `READ`; guided tasks use
`PREPARE_ACTION`. All are coordination R0 nodes; actual domain execution is absent. It validates uniqueness,
topology, role ownership, graph size and depth before returning.

### `FinalTaskGraphBuilder.persistable(graph, graph_id)`

Converts a volatile graph into a raw-goal-free `PersistedTaskGraph` while retaining exact nodes, dependencies,
timestamps and digest.

### `domain_type_for_node(node)`

Maps a finite `TaskDomain` to the Stage 5E `DomainType`; internal General/Memory nodes return `None`.

### `TaskStateMachine.transition(task, target, now=None)`

Validates the explicit transition table, rejects terminal/skipped transitions, advances the immutable revision and
timestamps, sets first start time, and clears current node on terminal states. Persistence remains the caller's job.

### `TaskStateMachine.is_terminal(state)`

Returns whether the state cannot transition further.

### `UserAttentionQueue.__init__(repository)` / `enqueue(item)`

Injects durable storage. `enqueue` writes a pending metadata-only item and attempts safe presentation; it never
resolves the underlying plan or domain confirmation.

### `UserAttentionQueue.activate_next()`

Keeps one active prompt and serializes high-risk presentation. It returns the active item or raises
`TaskAttentionError` when none exists.

### `UserAttentionQueue.resolve(attention_id, dismissed=False, now=None)`

Closes an active/pending UI item with compare-and-swap semantics. “Resolved” means the prompt was handled, not that
an operation was approved.

### `UserAttentionQueue.pending(task_id=None)`

Returns unresolved metadata globally or for one task.

### `TaskSummaryBuilder.__init__(workflows)` / `build(task, receipts)`

Injects the sealed domain registry. `build` counts exact receipt statuses and object counts, de-duplicates fixed
fact codes and asks domains for recovery descriptions. It does not use model prose or infer success.

## `FinalOrchestrator`

### `__init__(repository, workflows, graph_builder, limits, policy, audit)`

Injects every mutable/security dependency, creates no global state, keeps raw goals in a locked in-memory map and
has no Executor, shell, Broker or confirmation service from any business domain.

### `create_task(goal, domains, root_request_id, kind, autonomy, conversation_id=None)`

Checks autonomy, builds/validates the graph, creates safe summary/digests/budget/progress, atomically stores the
root and graph, writes the initial checkpoint, transitions through planning to awaiting confirmation, and retains
the raw goal only in memory. It performs no domain handoff.

### `request_plan_confirmation(task_id, scope_references=())`

Reconstructs and integrity-checks the active volatile graph, hashes scope references, creates durable short-lived
R0-only consent, and queues a visible per-task attention item.

### `resolve_plan_confirmation(confirmation_id, approved)`

Loads exact durable consent, resolves it once, closes its attention item, and moves the root to `READY`,
`CANCELLED` or `BLOCKED`. It does not call a domain.

### `start(task_id, confirmation_id)`

Requires `READY`, exact graph binding and approved consent; atomically marks the consent consumed before moving to
`RUNNING`. Reusing the confirmation fails.

### `advance(task_id)`

Advances at most one node. It pauses on exceeded budget; internal nodes update checkpoints; a domain node reserves
its unique dispatch before calling only `DomainWorkflow.prepare`; then it waits for the owning UI/domain. When all
nodes are terminal it returns a deterministic `StructuredTaskSummary`. It never calls a business Executor.

### `record_domain_receipt(receipt)`

Requires exact active node/domain/task/version and one active dispatch. It stores the immutable receipt, advances
dispatch state to result-received/resolved, updates checkpoint/progress, resolves the matching attention item, and
returns to `RUNNING`. Duplicate/stale/cross-task receipts fail.

### `pause(task_id)`

Stops future scheduling and records `pause_requested`; it does not cancel dispatched external work.

### `resume(task_id)`

Clears the pause flag but intentionally stops at `WAITING_FOR_USER` with a Fresh-review attention item. It never
restores an old confirmation, target or DOM/PID reference.

### `cancel(task_id)`

Transitions through cancelling when needed, marks future coordination cancelled and persists the cancellation
request. It does not undo completed changes or kill an external uninstaller.

### `register_domain_transaction(task_id, node_id, transaction_ref)`

Correlates one opaque, restricted-character owning-domain transaction ID with the exact active dispatch and writes
it to the next checkpoint. It must be called only while the task is waiting on that node. It does not approve,
launch or retry the transaction; the reference exists solely for result correlation and crash reconciliation.

### `recover(task_id)`

Moves an interrupted task to recovering, reconciles only dispatches that have domain transaction references,
rejects any replay claim, then stops at user review. Dispatches without enough evidence remain unknown.

### `revise(request)`

Validates explicit user provenance and scope expansion, builds a new graph ID/version, invalidates old task-plan
consent, preserves prior history, resets progress/checkpoint, keeps the new raw goal volatile, and returns to plan
confirmation.

### `list_recent(limit=100)` / `summary(task_id)` / `pending_attention()`

These read-only methods return bounded safe task rows, deterministic receipt aggregation and notification-safe
attention metadata respectively.

### `close()`

Clears volatile raw goals and disposes the task repository. It does not delete history or domain recovery evidence.

### Internal helper methods

`_require_volatile_graph` reconstructs and digest-checks an active graph; `_task_budget` copies configured ceilings;
`_initial_checkpoint` creates authority-free node snapshots; `_transition` performs CAS persistence plus audit;
`_save_same_revision_payload` updates non-state fields with CAS; `_event` writes task and independent audit records;
`_enqueue_attention`, `_resolve_attention_kind` and `_resolve_attention_for_node` manage presentation metadata;
`_next_ready_node` applies dependency order; `_complete_internal_node` and `_set_node_status` update progress and
checkpoints; `_workflow_request` creates a reference-only handoff; `_wait_for_domain` maps preparation to an exact
waiting state; `_finish` chooses completed/partial state from recorded node outcomes; `_node_status_for_receipt`
maps the finite domain receipt enum without inference.

## Persistence

### `_encode(value)` / `_decode(payload, digest, model)`

`_encode` emits Pydantic JSON plus SHA-256. `_decode` verifies SHA-256 before schema validation and raises
`ComputerTaskStoreError` on tampering or incompatible data.

### `ComputerTaskRepository.__init__(database_path)` / `initialize()`

The constructor creates an independent SQLAlchemy engine/session factory. `initialize` runs SQLite `quick_check`,
creates the additive schema, rejects newer schema versions, marks active tasks interrupted, invalidates task-plan
consent and marks in-flight dispatches for reconciliation. It returns interrupted task IDs.

### `create_task(task, graph)` / `get_task(task_id)` / `save_task(task, expected_revision)`

`create_task` inserts matching root/graph records in one transaction. `get_task` validates payload digest/schema and
duplicated indexed columns. `save_task` requires a consecutive immutable revision and uses compare-and-swap.

### `save_graph(graph)` / `get_graph(task_id, graph_version)`

Appends or reads an immutable graph version. Existing versions are never overwritten; duplicate task/version
fails.

### `list_recent(limit=100)`

Returns at most 500 integrity-checked task summaries ordered by update time.

### `save_checkpoint(checkpoint)` / `latest_checkpoint(task_id)`

Appends and immediately reads back a checkpoint for verification; `latest_checkpoint` returns the newest valid
record or fails when none exists.

### `reserve_dispatch(dispatch)` / `get_dispatch(dispatch_id)`

`reserve_dispatch` requires pristine state and atomically inserts the unique task/version/node row as
`DISPATCHING` with attempt one. The uniqueness constraint prevents duplicate UI/restart dispatch. `get_dispatch`
verifies its payload.

### `save_dispatch(dispatch, expected)` / `unresolved_dispatches(task_id)`

`save_dispatch` compare-and-swaps the expected lifecycle state. `unresolved_dispatches` returns every non-resolved
record for result collection or recovery; it never resets attempts.

### `save_plan_confirmation` / `get_plan_confirmation` / `update_plan_confirmation`

These methods insert, load and compare-and-swap one task-plan confirmation. Duplicate or replayed resolution fails.

### `invalidate_plan_confirmations(task_id, now=None)`

Converts pending/approved confirmations for one task to `INVALIDATED` and returns the exact count. Used by graph
revision; startup performs the same protection globally.

### `save_attention` / `update_attention` / `list_attention`

Insert, compare-and-swap and list metadata-only attention for one task.

### `list_all_attention(include_resolved=False, limit=200)`

Returns a bounded cross-task queue so the UI can serialize presentation. It grants no confirmation authority.

### `save_receipt(receipt)` / `list_receipts(task_id)`

Persists one immutable task/node receipt and returns task receipts in observation order. Duplicate node receipt is
rejected.

### `append_event(event)` / `close()`

`append_event` writes content-free lifecycle metadata. `close` releases database connections without deleting
task history.

### `_write_task_row(row, task)` / `_require_initialized()`

The first synchronizes indexed root columns and digest-protected payload during startup interruption. The second
fails every repository operation attempted before successful initialization.

## Audit, composition and UI

### `ComputerTaskAuditLogger.__init__()` / `lifecycle()` / `dispatch()`

The constructor records the audit repository and optional Git commit. `lifecycle` stores task/node IDs, state,
graph version/digest and fixed detail codes. `dispatch` stores dispatch/domain/attempt metadata. Both explicitly
record that raw goal/domain bodies, confirmation secrets and low-level tools were not saved.

### `FinalTaskServices.close()` / `build_final_task_services(settings, audit_repository)`

`close` delegates orderly task shutdown. The builder initializes durable storage, creates the closed workflow and
template registries, policy snapshot, validated graph builder, audit logger and Final Orchestrator, then returns the
interrupted-task count. It does not instantiate a Windows writer.

### `HomeTaskTab.__init__()` / `create_selected()` / `require_computer_task(value)`

The constructor displays the closed templates. `create_selected` creates an unconfirmed durable task and emits it
to Task Center. `require_computer_task` validates a Qt object handoff before UI use. None can confirm or execute.

### `TaskCenterTab.__init__(agents, final_tasks=None, parent=None)`

Builds one task-list surface. `agents` preserves the Stage 5D compatibility view; `final_tasks` enables Stage 5E
durable history and attention. The tab receives injected services and never owns a business-domain Executor.

### `TaskCenterTab.register_task(prepared)` / `register_computer_task(task)`

`register_task` adds a legacy Stage 5D coordination task. `register_computer_task` selects a newly created durable
Stage 5E task after refreshing the bounded list. Neither method approves or starts it.

### `TaskCenterTab.refresh()` / `_refresh_final()` / `_refresh_legacy()`

`refresh` chooses the available service. `_refresh_final` loads bounded safe task rows and attention metadata;
`_refresh_legacy` renders the existing volatile Agent tasks. None loads goal bodies, confirmation secrets or domain
payloads.

### `TaskCenterTab.review_selected_plan()`

Requests and displays one exact task-plan confirmation, records the user's explicit decision, and starts only the
confirmed R0 coordination graph. It cannot approve an owning-domain R1/R2/R3 operation.

### `TaskCenterTab.pause_selected()` / `resume_selected()` / `recover_selected()` / `cancel_selected()`

Invoke the corresponding Stage 5E root operation for exactly one selected task and refresh the view. Resume and
recover stop at Fresh review. Cancel affects future coordination only.

### `TaskCenterTab._apply_final_action(action)`

Resolves the selected task ID, invokes one injected root action and converts validation failures to a beginner-
friendly dialog. The callable is supplied by local code, never by model or document text.

### `TaskCenterTab._selected_task_id()` / `_require_final()` / `_require_legacy()`

The first parses the selected durable UUID or returns `None`. The service guards return the configured service or
raise a clear runtime error; they prevent accidental mixing of Stage 5D and Stage 5E paths.

### `SystemTrayController.__init__(window, quit_callback)`

Creates the finite tray menu: open the window, open Task Center, or request orderly quit. It stores no task
confirmation service.

### `SystemTrayController.is_available()` / `show()` / `hide()` / `show_window()`

Expose Qt tray availability and presentation lifecycle. `show_window` restores/focuses the main window only.

### `SystemTrayController._on_activated(reason)`

Restores the window for a supported tray activation reason. Activation never resolves attention or consent.

### `SystemTrayController.notify_attention(title, message)` / `_open_task_center()`

`notify_attention` displays minimized non-authorizing text. `_open_task_center` emits a navigation signal after
showing the window. There is no tray approval, Confirm All or global Undo action.

### `MainWindow._register_home_task(value)` / `_show_task_center()`

The first validates a Qt-carried `ComputerTask`, registers it in Task Center and navigates there. The second only
refreshes and selects the Task Center tab.

### `MainWindow._open_task_handoff(action_code)`

Maps a finite `OPEN_<DOMAIN>_WORKFLOW` code to an existing owning-domain UI. Unknown codes fail visibly. Opening a
surface is not confirmation and does not execute a tool.

### `_task_domain_for_request(domain)`

Maps the finite shared text/voice request domain to one Stage 5E `DomainType`; unsupported request domains raise
instead of falling back to a generic executor.

### `_task_shape_for_request(request, route)`

Selects a finite task kind, autonomy level and high-level domain tuple for a routed request. It never copies model-
claimed capabilities or creates low-level tool authority.
