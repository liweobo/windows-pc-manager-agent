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

### Changed

- Renamed the internal `platform` package to `platform_support` to distinguish
  operating-system adapters from Python's standard-library `platform` module.

### Security

- Default-deny protected paths, path traversal, symlink, junction, and reparse
  point handling.
- No permanent deletion, system mutation, elevation, arbitrary shell execution,
  or credential collection.

### Fixed

- Use one Windows directory-identity API for discovery and execution-time
  revalidation, and make scanner timeout tests independent of clock resolution.
- Allow the secret-scanning job to read pull-request commit metadata without
  granting any repository write permission.
