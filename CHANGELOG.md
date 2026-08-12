# Changelog

All notable changes are documented here. The project follows semantic versioning
once release tags are introduced.

## [Unreleased]

### Added

- Secure stage 0 Windows desktop and tray foundation.
- Replaceable LLM provider contract and OpenAI Responses API adapter.
- Structured plans, risk review, registered-tool execution, confirmation, audit,
  rollback contracts, and a metadata-only directory scanner.
- Unit, integration, security, GUI, CI, and project documentation foundations.
- Detailed API reference covering every production function and method.
- Stage 1 authorized/favorite/forbidden directory management backed by SQLite.
- Streaming, cancellable metadata scanning with progress, timeout, file limits,
  typed partial outcomes, central file classification, and paged result storage.
- Configurable large-file and cautious inactive-file analysis with confidence and
  evidence, plus staged duplicate verification using quick hashes, SHA-256, and
  optional byte comparison.
- Deterministic file-analysis plan compiler, independent semantic validator,
  exact plan confirmation, aggregate-only explanation, and per-tool audit events.
- Stage 1 GUI for plan review, progress, cancellation, result filtering/sorting,
  Explorer selection, and exclusive-create CSV/JSON export.
- 10,000-file performance benchmark and expanded unit, integration, GUI, security,
  cancellation, audit, provider, and export tests.
- Stage 2A immutable operation plans, finite rename/organization rules, local source
  resolution, real filesystem Preview, conflict and same-volume checks.
- Registered R1 `file.mkdir`, `file.move`, `file.rename`, and rollback-only empty
  created-directory tools using verified Windows file identities and no-overwrite APIs.
- Persistent operation/item state machines, write-ahead Undo journal, one-time
  Preview-bound confirmation, fail-safe executor, interrupted transaction recovery,
  reverse-order rollback Preview and independent rollback confirmation.
- Stage 2A GUI for checked analysis results, manual/natural-language planning,
  operation Preview, progress, stop-future behavior, history, and rollback.
- Stage 2A unit, Windows integration, security, failure, recovery, rollback, and GUI tests.
- Stage 2B deterministic `TrashPlan`, directory-tree snapshots, local fixed-NTFS
  capability checks, and the registered R2 `file.trash` tool using Windows
  `IFileOperation` with per-item result callbacks.
- Two independent one-time R2 confirmations, additive SQLite confirmation/recovery
  records, crash reconciliation to UNKNOWN, MANUAL recovery guidance, and an
  independent Windows Recycle Bin GUI page.
- Stage 2B unit, integration, security, COM progress-sink, persistence, cancellation,
  and GUI tests, including a production-source permanent-delete API guard and an
  opt-in disposable real-Windows Recycle Bin probe.

### Changed

- Renamed the internal `platform` package to `platform_support` to distinguish
  operating-system adapters from Python's standard-library `platform` module.
- The chat page now routes file-analysis goals to the formal Stage 1 workflow;
  disabled model configuration falls back to deterministic manual planning.
- Chat now routes move/rename/organize/rollback goals to Stage 2A; concrete paths and
  file selection remain local and deterministic.
- Chat refuses permanent-delete/empty-Recycle-Bin wording locally and only routes
  Recycle Bin intent to a page where the user must explicitly select objects.
- Informational Windows copy-engine success HRESULTs now use COM `SUCCEEDED` semantics;
  positive recycle evidence remains mandatory.

### Security

- Default-deny protected paths, path traversal, symlink, junction, and reparse
  point handling.
- No permanent deletion, system mutation, elevation, arbitrary shell execution,
  or credential collection.
- External model calls require an expiring digest-bound confirmation. Planning
  sends opaque root IDs and labels only; explanation sends aggregates only.
- Local, canonical paths reject traversal, UNC/device/network roots, ambiguous
  Windows names, protected locations, symlinks, junctions, and reparse points.
- All R1 writes require a persisted transaction capability matching plan, Preview,
  operation, registered tool, and exact arguments. Preview approval is expiring,
  one-time, and invalid after any binding change or restart.
- Move/rename/create revalidate authorization, reparse components, source identity,
  target absence and volume immediately before use; overwrite and cross-volume copy
  behavior are absent. Rollback applies the same checks in reverse order.
- Stage 2B blocks protected/system/application-data roots, authorized roots themselves,
  links/reparse points, system/offline objects, network/removable/non-system volumes,
  overlapping selections, stale directory trees, and unavailable Recycle Bins.
- Recycle execution requires persisted PLAN approval plus consumed RUNTIME approval,
  writes MANUAL recovery evidence before the Shell call, never invokes a legacy or
  permanent-delete fallback, and stops the batch on an ambiguous result.
- Permanent-delete, Recycle Bin bypass, and empty-bin requests are refused and audited
  as R4; an ambiguous first result also marks its parent transaction UNKNOWN.

### Fixed

- Pin uv 0.11.32 in CI so setup does not depend on a latest-release API lookup.
- Prevent the file-analysis plan button's checked state from being passed to the
  goal text field as a boolean value.
- Use one Windows directory-identity API for discovery and execution-time
  revalidation, and make scanner timeout tests independent of clock resolution.
- Allow the secret-scanning job to read pull-request commit metadata without
  granting any repository write permission.
- Restore UTC information lost by SQLite datetime storage and treat zero-valued
  Windows directory-enumeration file IDs as unknown before duplicate hashing.
- Audit the exact registered tool on execution failure and preserve incomplete
  report artifacts instead of deleting any file in Stage 1.
- Preserve the precise reparse-point denial instead of relabeling it as a generic
  unavailable-path error on Windows runners that can create symbolic links.
- Flush parent operation transactions before child reservations inside the same SQLite
  commit, and preserve conflict-only rollback Previews without creating a confirmation.
- Account for earlier reverse steps when assessing transaction-created directory
  emptiness, while still blocking unmanaged contents.
