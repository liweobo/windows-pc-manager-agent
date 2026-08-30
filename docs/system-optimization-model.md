# Stage 4E1 analysis and Stage 4E2 controlled-cleanup model

## Stage 4E2 authority hand-off

Stage 4E1 output remains evidence only. `CleanupSourceReference` records one of `KNOWN_LOCATION`,
`STAGE1_REPORT` or `STAGE4D3_REPORT` so Stage 4E2 can deterministically choose Fresh direct analysis or an
existing safe hand-off. The source reference never contains a path command and never grants write access.

`SystemCleanupRequest` carries only a session-local report UUID and explicit candidate UUIDs. A Fresh
assessment discovers exact children and emits `CleanupExecutionCandidate` rows containing separate
identity, material, path-safety, activity, protection, eligibility, adapter and recoverability evidence.
Only `ELIGIBLE + RECYCLE_BIN_ITEM + MANUAL` can enter a `CleanupExecutionPlan`; blocked/deferred rows remain
visible and mixed selected batches fail as a whole.

The plan and runtime Preview bind all item digests, totals and R2/R2_HIGH_IMPACT classification. Durable
confirmations are non-interchangeable by scope and tier, expire and are atomically consumed. The write
request contains only durable UUID references. Result models deliberately separate bytes moved out of the
original location from verified reclaimed disk bytes, which remain `None` while objects stay in the Bin.

`RecycleBinEmptyPlan` and `RecycleBinEmptyPreview` use a different transaction kind, action, confirmation
scope and recovery value. A complete non-empty exact-volume snapshot needs count, size, oldest/newest
deletion times and a canonical digest. The resulting irreversibility record is not an Undo record.

## Boundary

Stage 4E1 is a strictly read-only evidence and reporting stage. It does not clean, optimize, boost, fix,
move, rename, recycle, delete, terminate, stop, disable, uninstall, edit the registry, invoke a shell or
request elevation. The only registered tools are:

1. `optimization.snapshot`
2. `optimization.storage.analyze`
3. `optimization.cleanup_candidates.analyze`
4. `optimization.performance.analyze`
5. `optimization.recommendations`

Every tool is R0, read-only, requires the confirmed plan, has no runtime confirmation and declares rollback
`NONE` because there is no state change. The registry always contains exactly this list, while each plan
contains only a canonical, dependency-complete subset. For example, a disk-space request collects disk and
storage evidence without CPU/services; a slow-PC request collects CPU/memory/disk/process evidence without
scanning caches. Both the Pydantic plan and an independent safety validator check that scope.
An explicit quick check uses CPU, memory and disk counters only; a comprehensive check uses all sources.

## Data flow

```text
user goal + optional authorized-root UUIDs
  -> local finite goal classification
  -> resolve UUIDs through AuthorizedPathService
  -> immutable OptimizationPlan + canonical digest
  -> isolated-registry and Fresh authorization review
  -> expiring plan confirmation
  -> goal-scoped SystemSnapshot collectors
  -> optional bounded StorageObservation list
  -> optional deterministic CleanupCandidate classification
  -> optional deterministic PerformanceFinding list
  -> evidence-linked OptimizationRecommendation list
  -> SystemOptimizationReport(changes_performed=false)
```

The model provider is not required. If a provider later explains the report, it may receive only bounded,
redacted structured values. It cannot add roots, categories, candidates, evidence, tools or actions.

## Storage scope

Personal files are included only when a stored Stage 1 authorization UUID resolves to the exact current
path. The plan stores both UUID and locally resolved path and safety review compares them again before each
run. Free-form path text never reaches the storage tool.

Known system/user locations are a finite adapter allowlist: current-user Temp, Windows Temp, DirectX shader
cache, current-user crash dumps, Windows Error Reporting archive, and default Edge/Chrome cache leafs. The
walker reads `lstat` metadata only and has a shared object/time budget. It never follows reparse points and
does not open file content.

Recycle Bin totals use documented `SHQueryRecycleBinW`; `$Recycle.Bin` is not enumerated. Windows Update,
Delivery Optimization and Installer Cache are returned as protected/unavailable unless a future reliable,
ordinary-user query source is independently implemented. WinSxS directory size is never used as reclaim
evidence.

## Candidate meaning

`observed_size_bytes` is metadata or Windows API evidence. `potential_reclaim_bytes` is optional and is
never present for protected, blocked or unknown data. A non-null potential value is not a promise that the
space can or should be reclaimed. Large/inactive/duplicate personal files remain caution items and receive
no automatic reclaim estimate because the user must choose what to retain.

Ownership, safety and protection are independent:

- ownership answers whether the source association is reliable;
- safety answers whether a future review might be reasonable;
- protection answers whether data/system semantics require stronger exclusion;
- confidence states how complete the current evidence is.

`stage4e1_executable` is always false. `reject_stage4e1_execution_authority()` rejects any report, candidate,
selection or ID supplied as future write authority.

## Performance findings

CPU requires elevated average and peak across multiple samples. Memory requires both high percentage and
low absolute availability. Disk capacity requires both high used percentage and low absolute free bytes.
Process findings are observations from bounded sampling, not long-term blame. Startup count is low-confidence
because V1 does not measure per-item boot impact. Disk-I/O findings are omitted when reliable counters are
unavailable. An explicit `NO_CLEAR_BOTTLENECK` result prevents silence from being misread as a failure.

## Partial results and cancellation

Access denial, missing roots and Windows query errors become partial, skipped or unavailable sources. They
are never converted to zero size. Cancellation is cooperative: it stops remaining reads, returns only
evidence already observed where possible and never requires rollback.

## Audit and export

SQLite audit retains goal enums, tool names, limits, counts, aggregate bytes, partial/skipped counts, result
IDs, version and commit. It excludes free-form request details, roots, candidate paths, filenames and raw
inventories. An explicit JSON/CSV export can contain local report detail; it uses a user-selected existing
local directory, exclusive creation and no overwrite. The export file is not execution authority.

## Deferred work

Stage 4E2 V1 directly supports only the three documented current-user known locations. Browser cache,
system Temp, Windows Update, Delivery Optimization and other Windows-managed maintenance require separate
supported APIs and safety reviews. Permanent deletion, automatic Bin restore and generic optimization
remain prohibited/deferred.
