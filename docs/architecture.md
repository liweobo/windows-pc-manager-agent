# Architecture

## Objective

The stage 0 application establishes enforceable boundaries before adding file
mutation or system-management features. The core is usable without a GUI and no
business rule depends on OpenAI or another model supplier.

## Layers

```text
PySide6 UI / minimal CLI
        |
ApplicationRuntime (dependency composition)
        |
ScanOrchestrator
  |       |          |          |
Plan   Safety   Confirmation   Audit
  |       |          |          |
  +-------+------ ToolRegistry--+
                     |
               file.scan (R0)
                     |
                 PathPolicy
```

- `domain` owns immutable Pydantic models and classifications.
- `providers` converts an external provider response into a `TaskPlan`; it never
  executes tools. The OpenAI adapter uses the Responses API and an explicit model.
- `safety` independently validates paths, schemas, manifests, risks, rollback
  claims, confirmation requirements, and MVP restrictions.
- `tools` is an allow-list registry. Raw model tool names have no authority.
- `orchestration` implements plan → review → confirm → execute → verify → audit.
- `ui` displays state and delegates to orchestration. `QRunnable` keeps scans off
  the GUI thread.
- `platform` contains OS-specific single-instance behaviour.

## Runtime flow

1. User chooses one root directory.
2. `ApplicationRuntime` creates a new root-scoped `PathPolicy`, registry, scanner,
   and reviewer.
3. The deterministic orchestrator creates an immutable plan.
4. Safety review validates every step and argument.
5. Confirmation binds plan ID, full canonical digest, summary, and expiry.
6. Execution repeats review and confirmation checks, then invokes `file.scan`
   only through the registry.
7. The scanner revalidates the root and refuses reparse points.
8. The orchestrator verifies the report root/count and appends an audit result.

## Provider boundary

`LLMProvider.create_plan()` accepts only an explicit `PlannerRequest` and returns
`ProviderPlanResult`. The OpenAI implementation is not wired to the stage 0 chat
button because sending user text or local paths is an external-data action that
needs a dedicated consent design. Tests inject a fake client and make no API call.

## Persistence

SQLAlchemy stores append-only audit events in SQLite under the current user's
local application-data directory. SQLite uses foreign keys, WAL journaling, and
full synchronous writes. If initialization or an append fails, execution stops.

## Extension points

Future providers implement `LLMProvider`; future tools provide a complete
`ToolManifest` and deterministic implementation. A write tool must additionally
implement `OperationCommand`, a truthful `UndoRecord`, verification, and the
required confirmation level before registration can be considered.
