# ADR 0002: Trust-bound direct Vendor uninstaller execution

## Status

Accepted for Stage 4D2B.

## Context

Windows uninstall registry entries may contain arbitrary, malformed or malicious command text.
Executing `UninstallString` directly—even with `shell=False`—would let local metadata select an
unexpected executable and arguments. Vendor uninstallers also have nonstandard UI, child processes,
exit codes and data-removal behavior.

## Decision

Support only one exact current-user, direct local `.exe` whose fresh software identity, install
location, file identity, bounded SHA-256, offline Authenticode signature, conservative Publisher
match and finite interactive argv policy all pass. Parse raw text with Windows argv semantics but
never persist or execute it. Bind the resulting `VendorUninstallerIdentity` to two durable,
short-lived confirmations and an atomic one-shot write guard.

The adapter uses an absolute executable and exact argv array, explicit executable/cwd, sanitized
environment, DEVNULL streams and `shell=False`. It never elevates, retries, reboots, controls vendor
UI, terminates processes, stops services or cleans residuals. Fresh inventory—not exit code—decides
removal. Rollback is NONE.

## Consequences

- Many legitimate unsigned, oddly signed, wrapper-based, relative, machine-wide or complex Vendor
  uninstallers are intentionally unsupported.
- False negatives are preferred to command injection, PATH hijacking or hidden quiet data removal.
- Long-running/stopped monitoring remains an active unknown transaction and blocks duplicate Agent
  uninstall requests; restart marks it interrupted without redispatch.
- Future package-manager/MSIX execution requires a separate Stage 4D2C design and cannot reuse this
  executable boundary.
