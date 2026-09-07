# Stage 5E Final Orchestrator

## Purpose

Stage 5E adds a durable coordination layer for long, multi-domain tasks. It records a safe task label,
versioned graph structure, progress, attention items, dispatch identity, owning-domain receipts and checkpoints.
It does not become a global Executor. File, process, startup, service, software, residual, cleanup, Office and
browser modules retain their existing target resolution, safety policy, Preview, confirmation, execution,
verification and recovery boundaries.

## Authority boundary

The task plan confirmation covers only the exact R0 coordination nodes in one graph version. It always stores
`domain_write_authorized=false`. A domain handoff carries IDs, the graph version, goal digest and optional safe
references; it carries no command, executable arguments, confirmation secret, Broker secret or generic payload.
The high-level `DomainWorkflow` interface deliberately has `prepare`, `reconcile` and `recovery_summary`, but no
`execute` or `confirm` method.

The production registry contains exactly these domains: File, System, Process, Startup, Service, Software,
Residual, Cleanup, Office, Browser and Optimization. Its default adapters only navigate to the existing owning
UI. Tests replace this high-level boundary with fakes; they do not fake or call Windows writers.

## Data flow

```text
User request/template
  -> deterministic task shape and bounded graph
  -> independent task-plan review (R0 coordination only)
  -> durable dispatch reservation
  -> owning-domain high-level preparation
  -> user attention / original domain UI
  -> original domain Fresh checks + Preview + confirmations + Executor + verification
  -> metadata-only DomainResultReceipt
  -> deterministic progress and summary
```

Raw goals are kept only for the active process. Durable state contains a bounded safe label and SHA-256 goal
digest. Domain bodies, document/web content, raw target paths, executable arguments, model reasoning, audio,
confirmation authority and Broker authentication material are excluded. Audit contains IDs, digests, counts,
fixed reason codes and policy versions.

## Scheduling and budgets

Graphs are directed and acyclic, use finite domain and node enums, and are validated by the existing Stage 5D
role/topology validator. Stage 5E graphs contain internal understanding/summary nodes plus one high-level domain
handoff per domain. They do not contain a global domain execution node. Hard limits cover task nodes, Agent/model
calls, high-level preparations, browser navigation, file observations, runtime and delegation depth. Exceeding a
budget pauses future scheduling and creates a user-attention item; it never relaxes a safety rule.

Only bounded R0 reads may ever be retried. V1 production UI handoffs are single-dispatch. Write-capable domain
actions remain non-retryable under their own original policies. There is no automatic fallback, “best effort”
writer, `FULL_UNATTENDED`, Confirm All, global admin mode or global Undo.

## User attention and UX

The Task Center shows safe task labels, state, progress and outstanding attention count. It supports one-task plan
review, pause, Fresh-review resume, future-only cancellation and interrupted-task inspection. High-risk attention
is serialized. A tray message can only open Task Center; it cannot approve a plan or operation. The Home page
creates versioned allow-listed task templates and leaves them unconfirmed.

The first templates are PC health check, disk-space analysis, startup review, health report, and web research to
Office report. They describe coordination shapes, not authority. A template update requires a new version and the
same graph/safety/test review as a custom task.

## Results and recovery

Only a receipt from the exact owning domain, task, node and graph version can change a domain node outcome.
`COMPLETED_VERIFIED` additionally requires deterministic verification evidence. Window closure, process exit,
absence of an exception, a model statement or an old transaction is not success.

The final summary aggregates fixed receipt status/count fields. Recovery remains per domain and preserves its
reported `FULL`, `PARTIAL`, `MANUAL` or `NONE` level. Stage 5E never advertises an atomic cross-domain Undo.
See [task lifecycle](task-lifecycle.md) and [crash recovery](crash-recovery.md).
