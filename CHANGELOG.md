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

### Changed

- Renamed the internal `platform` package to `platform_support` to distinguish
  operating-system adapters from Python's standard-library `platform` module.
- The chat page now routes file-analysis goals to the formal Stage 1 workflow;
  disabled model configuration falls back to deterministic manual planning.

### Security

- Default-deny protected paths, path traversal, symlink, junction, and reparse
  point handling.
- No permanent deletion, system mutation, elevation, arbitrary shell execution,
  or credential collection.
- External model calls require an expiring digest-bound confirmation. Planning
  sends opaque root IDs and labels only; explanation sends aggregates only.
- Local, canonical paths reject traversal, UNC/device/network roots, ambiguous
  Windows names, protected locations, symlinks, junctions, and reparse points.

### Fixed

- Use one Windows directory-identity API for discovery and execution-time
  revalidation, and make scanner timeout tests independent of clock resolution.
- Allow the secret-scanning job to read pull-request commit metadata without
  granting any repository write permission.
- Restore UTC information lost by SQLite datetime storage and treat zero-valued
  Windows directory-enumeration file IDs as unknown before duplicate hashing.
- Audit the exact registered tool on execution failure and preserve incomplete
  report artifacts instead of deleting any file in Stage 1.
