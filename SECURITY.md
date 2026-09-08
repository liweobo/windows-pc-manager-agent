# Security policy

## Supported release line

Security fixes currently target the `1.0.0-rc.x` release-candidate line. This repository has not
published a production-ready v1.0 release. The unsigned private RC exposes only the fixed R0
feature set documented in `docs/release/feature-freeze.md`.

## Reporting a vulnerability

Do not open a public issue containing exploit details, credentials, private files, audit records or
diagnostic bundles. Use GitHub's private security-advisory reporting for this repository, or contact
the repository owner through an already trusted private channel. Include the affected version,
stable error code, minimal reproduction and whether data or privilege boundaries may be affected.
Never attach real secrets or user documents; use synthetic examples.

Critical or high findings involving data loss, confirmation bypass, privilege escalation, replay,
secret disclosure or unsafe update/installer behavior block an RC. They are not accepted merely as
known limitations.

## Release security properties

- Main runs as the current standard user; packaging never elevates it.
- The privileged Broker is separate, one-shot and disabled in the unsigned private RC.
- R1/R2/R3 behavior retains its domain Preview, confirmation, Fresh identity, audit, verification
  and recovery boundary even when source-development features are enabled.
- There is no arbitrary shell, PowerShell, CMD, model-generated code execution, permanent-delete
  fallback, silent self-update or persistent elevated daemon.
- Logs, crash evidence and diagnostic bundles are local, bounded and redacted; none is uploaded
  automatically. Pattern redaction is defense in depth, not comprehensive data-loss prevention.

Do not work around SmartScreen, Defender, UAC, TLS validation or browser sandboxing. An unsigned
build is `SIGNING_NOT_PRODUCTION_READY`, regardless of whether it runs on a development machine.
