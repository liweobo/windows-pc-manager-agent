# Architecture

## Stage 2B R2 Recycle Bin boundary

Stage 2B is deliberately parallel to, rather than hidden inside, the Stage 2A R1 service:

`explicit selection -> TrashPlanCompiler -> TrashSafetyValidator -> TrashPreviewEngine ->`
`PLAN confirmation -> fresh tree revalidation -> RUNTIME confirmation -> PREPARED recovery ->`
`ToolRegistry/transaction guard -> Windows IFileOperation -> callback verification -> audit`

`TrashPlan` and `TrashPreview` cannot claim FULL rollback. `TrashPathPolicy` composes the
ordinary authorization policy with system/application-data exclusions. Complete directory
snapshots hash relative path, file ID, kind, size, timestamps, and attributes; a changed
tree invalidates confirmation. The provider layer is absent from this data flow and cannot
select targets.

The shared transaction journal gains additive confirmation and recovery tables instead of
changing existing Stage 2A rows in place. PLAN and RUNTIME proof is checked again by the
registry write guard. `TrashRecoveryRecord` is separate from `UndoRecord`; restart turns
an in-flight trash item into UNKNOWN and never automatically resumes it.

`WindowsRecycleBinPlatform` runs one `IFileOperation` in a worker-thread STA per item. It
sets recycle/undo/early-failure flags and implements `IFileOperationProgressSink`.
Success requires a zero operation HRESULT, no abort, a recycle-capable transfer flag, a
non-null newly created Recycle Bin Shell item, and absence of the original path. No legacy
Shell API, command line, `unlink`, recursive deletion, or permanent fallback exists.

## Objective and boundary

Stage 2A preserves the complete Stage 1 read-only slice and adds the first narrow R1
write slice. Core logic still runs without Qt and without a provider. Only ordinary
directory creation, same-volume move, same-parent finite-rule rename, and verified
rollback are executable. Overwrite, cross-volume copy/delete, recycle bin, permanent
deletion, arbitrary commands, system mutation, and elevation remain unavailable.

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
  single-instance behavior, and checked Win32 identity/move/directory primitives.

## Stage 2A write boundary

```text
Natural language / checked Stage 1 rows / manual selection
                 |
    FileOperationPlanner (optional, intent only)
                 |
 FileOperationSourceResolver + PlanCompiler (local paths)
                 |
       FileOperationSafetyValidator
                 |
     OperationPreviewEngine (read-only live state)
                 |
       plan + preview digest confirmation
                 |
 FileOperationService -> OperationRepository write-ahead journal
                 |
 TransactionExecutor -> ToolRegistry + TransactionExecutionGuard
                 |
 file.mkdir / file.move / file.rename -> Win32 -> verify
                 |
        available Undo + audit + terminal report
                 |
 RollbackManager -> reverse live Preview -> separate confirmation
                 |
 registered reverse tools -> verify -> rollback terminal state
```

The model sees only goal text, non-sensitive root labels, opaque root IDs, and finite
enums. It cannot provide concrete paths, a command, Python code, risk, confirmation,
or execution policy. `FileOperationSourceResolver` discovers literal extensions only
inside those IDs. The compiler observes Windows file identity and computes every final
path, including modified-year directories and finite rename results.

Preview is a real filesystem snapshot. It classifies every item `READY`, `CONFLICT`,
or `BLOCKED`, counts directory-tree impact without following reparse points, checks
target presence and nearest-parent volume, and reports truthful FULL rollback counts.
Conflicts stay visible but are persisted as `SKIPPED`; only READY arguments are eligible.

`OperationConfirmation` binds transaction ID, plan ID/digest, preview ID/digest, item
counts, approval time, and expiry. Approval is held in memory, consumed once, and lost
on restart. `OperationRepository` separately reserves the exact tool and argument digest.
The registry requires both the one-time approval workflow and a durable RUNNING item
capability before invoking any write tool.

Immediately before mutation, tools repeat lexical/canonical scope checks, reject any
reparse component, re-open a handle to compare Volume Serial Number, 128-bit File ID,
size/timestamps/attributes, recheck target absence, and reject a volume change. The
Windows adapter uses `MoveFileExW` with only write-through; it does not request replace,
copy-across-volume, delayed reboot, or shell behavior.

## Stage 2A transaction and recovery model

Transactions use explicit states: `PREVIEWED → AWAITING_CONFIRMATION → CONFIRMED →
RUNNING → COMPLETED/PARTIALLY_COMPLETED/FAILED/CANCELLED`. Rollback uses `ROLLING_BACK →
ROLLED_BACK/PARTIALLY_ROLLED_BACK/ROLLBACK_FAILED`. Each item independently records
`PENDING/RUNNING/COMPLETED/FAILED/SKIPPED` and rollback states.

Before every write, the repository atomically changes the item to RUNNING and stores a
checksum-protected PREPARED Undo record. After Win32 returns, deterministic verification
must pass before the item becomes COMPLETED and Undo becomes AVAILABLE. Unexpected error
stops all later PENDING items. Cancellation means “stop future items”; it never kills an
active filesystem call.

At startup, stale RUNNING/ROLLING_BACK transactions become `INTERRUPTED` and are shown
to the user; they are never resumed. PREVIEWED/AWAITING_CONFIRMATION/CONFIRMED transactions
become `CANCELLED` because their memory-only authorization cannot survive restart.

Rollback reads only persisted Undo records, orders them by descending original sequence,
and evaluates live identity, modification, restored-path conflicts, authorization, and
created-directory contents. It does not ask a model to guess reverse paths. A directory
created by the transaction may be removed only after earlier reverse steps vacate its
managed children and no unmanaged entry remains. Rollback receives a new digest-bound
confirmation and traverses the same registry/transaction guard/verification boundaries.

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
R2/R3 tools remain unregistered in Stage 2A. Stage 2B recycle-bin work requires a
separate R2 design and is not implied by the rollback-only empty-directory primitive.
