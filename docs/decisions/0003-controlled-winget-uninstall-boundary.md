# ADR 0003: Controlled current-user winget uninstall boundary

- Status: accepted
- Date: 2026-08-24
- Stage: 4D2C1

## Context

Stage 4D2B can execute one exact trusted Vendor uninstaller, but a package-manager removal has a
different identity and trust problem. Human-readable `winget list` output is presentation text,
package names are ambiguous, configured sources may be untrusted, package scope may be unclear,
and `winget.exe` is normally exposed through an App Execution Alias rather than a conventional
fixed executable. A successful process exit also does not prove that the intended installed
software identity disappeared.

The Windows Package Manager documentation defines exact ID, source, version, scope, interactive and
non-interactive switches. Its export format provides structured package identifiers and versions,
but does not provide sufficiently strong scope evidence for this Agent on its own. Therefore a
false-negative bias is required: export evidence must be paired with a unique current-user
installed-software record that already carries structured package-manager metadata.

## Decision

Stage 4D2C1 adds only `software.uninstall.winget` and only for the official community source:

- Package identity is `(PackageIdentifier, installed version, source name, official source
  identifier, current-user scope)`; visible names never select a target.
- Structured export is parsed only from an Agent-owned temporary file. Source URLs and raw output
  are neither persisted nor sent to a model.
- The package must map one-to-one to a normalized current-user Software identity through a matching
  package-manager ID, Package ID and installed version. Missing or duplicate links block.
- `msstore`, custom sources, MSIX/AppX, machine scope, protected software classes and unknown classes
  remain unsupported.
- The trusted executable is the fixed Desktop App Installer alias under the current user's
  WindowsApps directory. The package family, alias reparse metadata, target name, file metadata and
  SHA-256 are revalidated before launch. There is no PATH lookup or executable fallback.
- The argument policy is code-owned and finite: `uninstall --id <id> --exact --source winget
  --version <version> --scope user --interactive --disable-interactivity`. The user, UI and model
  cannot add, remove or replace an argument.
- Execution requires a non-elevated Agent, complete process/service evidence, no active `winget`, no
  running related service, no competing MSI/Vendor/winget transaction, mandatory pre-launch audit,
  and two durable digest-bound confirmations.
- Cancellation before launch prevents dispatch. Cancellation after launch stops monitoring only;
  the Agent never kills, retries, resumes or redispatches the package manager.
- Success requires fresh complete official-source package inventory and fresh complete Software
  inventory to agree that both original identities are absent. Process exit is only evidence.
- Residual analysis performs one exact `lstat` of the known install location without following,
  enumerating or deleting content. Rollback is `NONE`; reinstall guidance is not an Undo record.

## Consequences

The design produces deliberate safe false negatives. Many normal `winget` packages will remain
analysis-only because Windows uninstall metadata does not expose the structured current-user link
needed by this boundary. That is preferable to guessing scope or identity. User interaction may
still occur in the vendor installer launched by winget, and stopping Agent monitoring does not stop
that UI or process.

This stage does not add package installation, upgrade, source management, package-manager repair,
MSIX removal, custom-source removal, machine-wide removal, elevation, automatic reboot or generic
command execution. Those require separate decisions and tests.

## References

- [Microsoft Learn: uninstall command](https://learn.microsoft.com/windows/package-manager/winget/uninstall)
- [Windows Package Manager export command](https://github.com/microsoft/winget-cli/blob/master/doc/windows/package-manager/winget/export.md)
- [Microsoft Learn: list command](https://learn.microsoft.com/windows/package-manager/winget/list)
- [Windows Package Manager troubleshooting](https://github.com/microsoft/winget-cli/blob/master/doc/troubleshooting/README.md)
