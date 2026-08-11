# Architecture

## Objective and boundary

Stage 1 delivers one complete read-only vertical slice without granting the model
filesystem authority. Core logic runs without Qt and without a provider. Stage 1
never moves, renames, deletes, hydrates, overwrites, or edits a user file and never
requests administrator privileges.

## Layer ownership

```text
Chat / FileAnalysisTab / headless caller
                 |
       FileAnalysisPlanner (optional LLM intent)
                 |
       FileAnalysisPlanCompiler (deterministic)
                 |
       FileAnalysisSafetyValidator
                 |
     digest-bound plan confirmation
                 |
       FileAnalysisOrchestrator
                 |
            ToolRegistry
       /          |           \
 file.scan   large/inactive   duplicates
       \          |           /
       AnalysisResultRepository
                 |
      paged GUI / CSV-JSON export
                 |
 aggregate-only explanation (optional LLM)
```

- `domain` owns immutable Pydantic intent, plan, progress, metadata, candidate,
  duplicate, confidence, report, risk, and rollback models.
- `authorization` owns explicit authorized/favorite roots and custom forbidden
  roots. Plans refer to opaque root IDs; only deterministic code resolves paths.
- `providers` is replaceable. OpenAI is one adapter and does not appear in domain,
  scanner, analyzer, or safety code.
- `safety` canonicalizes local paths, rejects protected and redirected paths, and
  independently compares the semantic plan with every executable step.
- `confirmation` binds plan or external payload digests to purpose and expiry.
- `tools` is the only execution allow-list. There is no shell or dynamic-code tool.
- `orchestration` repeats review and confirmation before execution, verifies typed
  outputs and terminal reports, and emits plan/tool/result audit events.
- `persistence` stores authorizations, audit events, and bounded analysis batches in
  SQLite. Candidate rows are paged; all discovered metadata is not kept in memory.
- `ui` only changes presentation state and starts `QRunnable` workers. It never
  executes a scanner or analyzer directly.
- `platform_support.windows` contains mapped-drive/atime checks, Explorer selection,
  single-instance behavior, and future Windows-specific adapters.

## Data flow

1. The user explicitly adds a local authorized root and optional forbidden roots.
2. Manual controls or an LLM produce `FileAnalysisIntentDraft`. The provider sees
   the goal, labels, opaque root IDs, allowed analyses, and registered tool names;
   it does not see paths or files.
3. `FileAnalysisPlanCompiler` resolves IDs locally, rejects overlapping roots,
   divides global file/time limits, derives exclusions, and creates only R0 steps.
4. `FileAnalysisSafetyValidator` combines the generic reviewer with Stage 1 checks:
   exact scope, session ID, thresholds, match mode, analysis set, and read-only impact.
5. Confirmation binds the complete `TaskPlan` digest. UI control changes discard it.
6. `file.scan` revalidates each root and streams metadata batches to SQLite. Progress,
   cancellation, timeout, per-object error continuation, and maximum count are explicit.
7. Selected analyzers page stored metadata. Duplicate analysis alone reads content,
   using size → quick hash → SHA-256 → optional byte comparison with identity checks.
8. SQL calculates ALL/ANY membership and serves validated filtering, sorting, paging,
   category summaries, and streaming export.
9. Optional explanation sends only typed aggregate totals after a second external-data
   confirmation. Provider observations cannot contain digits; measured numbers are
   rendered by deterministic code.

## Persistence and recovery

The three SQLAlchemy subsystems share the application SQLite path but have isolated
declarative bases. Foreign keys, WAL, full synchronous writes, and bounded queries are
configured centrally. A stale `RUNNING` analysis session is application-owned temporary
data and is removed at the next initialization. Corrupt audit storage fails closed.

Authorized-path changes produce FULL inverse records. Report export is R1 and uses
exclusive creation; existing files are never overwritten. Export cleanup is deliberately
manual because Stage 1 never deletes even an incomplete report. User-file analysis is R0
and truthfully declares rollback `NONE` because it changes nothing.

## Extension rules

New providers implement `LLMProvider`. New tools require strict input/output models,
a complete `ToolManifest`, deterministic implementation, cancellation/bounds, safety
validation, audit, and tests. A future write tool additionally needs `OperationCommand`,
a truthful `UndoRecord`, conflict rules, verification, and the correct confirmation tier.
R2/R3 tools remain unregistered in Stage 1.
