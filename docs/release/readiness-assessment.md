# Stage 7A production readiness assessment

Assessment date: 2026-09-08. Target: `1.0.0-rc.1` unsigned private candidate for Windows 11 x64.

## A. Feature freeze matrix

| Domain | Source maturity | Private RC state | Reason |
|---|---|---|---|
| Files | PARTIAL | analysis READY; all writes DISABLED | R0 scan/report is enabled; move/rename/Bin are not |
| System diagnostics | READY | ENABLED | finite R0 collectors |
| Processes | PARTIAL | inventory via diagnostics; actions DISABLED | lifecycle change needs later manual validation |
| Startup | PARTIAL | inventory via diagnostics; actions DISABLED | registry/folder writes excluded |
| Services | PARTIAL | inventory via diagnostics; actions DISABLED | SCM/Broker path excluded |
| Software uninstall | PARTIAL | analysis ENABLED; execution DISABLED | identity report only |
| Residual cleanup | EXPERIMENTAL | DISABLED | R2 write domain |
| Privileged Broker | NOT PRODUCTION READY | DISABLED | signing and real UAC validation absent |
| Optimization | PARTIAL | analysis ENABLED; actions DISABLED | advice is non-executable |
| Office | EXPERIMENTAL | DISABLED | source regression only |
| Voice | EXPERIMENTAL | DISABLED | real devices not validated |
| Browser | EXPERIMENTAL | DISABLED | Worker built/tested but visible sites not validated |
| Memory | EXPERIMENTAL | DISABLED | source regression only |
| Multi-Agent | EXPERIMENTAL | DISABLED | coordination is not authority |
| Final Orchestrator | EXPERIMENTAL | DISABLED | source regression/crash recovery only |

## B. Release blockers

- **CRITICAL:** no open finding in the completed local full regression and security checks.
- **HIGH:** signing is not configured; remote installer/lifecycle workflow and real UAC/Broker validation are pending.
- **MEDIUM:** clean Windows matrix, accessibility/DPI/tray/sleep-resume validation and Main dependency/size reduction
  are incomplete.
- **LOW:** Chinese-first UI has no complete localization/English fallback system.

Any data loss, confirmation bypass, privilege escalation, dangerous replay or secret disclosure finding becomes an
immediate critical/high blocker and cannot be moved to Known Issues.

## C–D. Packaging and installer

PyInstaller 6 produced and inspected distinct Main, Broker and Browser Worker onedir artifacts locally. Main uses the
lightweight bootstrap, Qt/PySide6 and SQLite; Browser is a disposable Playwright Worker; Office/Voice dependencies
remain in the Main static graph even though their private-RC routes are disabled. Artifact inspection and Broker xref
checks are implemented.

Inno Setup 6 source provides Program Files installation, Start Menu entry, optional desktop shortcut, safe reinstall,
uninstall and version/publisher metadata. User data remains in LocalAppData. There is no repair command, auto-start,
portable mode, downgrade, silent update or destructive app-data removal. Local compiler: unavailable. Ephemeral CI:
prepared, result pending.

## E–K. Audit plans and implemented controls

- Security: full domain regression, global AST invariants, Bandit, Gitleaks, artifact/Broker inspection, manual UAC.
- Dependencies: `uv.lock`, `pip-audit`, runtime license closure, SPDX SBOM, static graph minimization review.
- Performance: existing scan/Office/browser/system suites plus 100-task stress and managed Worker cleanup; installed
  cold/warm/idle measurements remain manual.
- Privacy: provider/Voice/Browser/Office/Memory/Audit flow table; telemetry absent; local-only redacted diagnostics.
- Logging: rotating JSONL, centralized redaction, hashed paths, sanitized local crash records and Safe Mode.
- Migration: schema/config version 1, adjacent transactions, verified backup, corruption/interruption/downgrade guards.
- Recovery: no installer deletion of LocalAppData; migration copy is non-overwrite; domain recovery levels remain exact.

## L. Release test matrix

Local evidence is 1,798 passed, 6 environment/opt-in skips and 86.94% coverage; the 12-test performance suite and
2-test managed Playwright suite also passed. Frozen Main, Browser Worker fail-closed input and Broker no-authority
smokes passed. The installer lifecycle remains restricted to the manual release workflow. Real UAC, signed binary
tamper, clean VM, accessibility, devices, visible Browser and Office tests remain manual and are `NOT_RUN` until
recorded.

## M. RC build plan

Generate version resources → build three isolated binaries → inspect contents/xref → build installer → generate SPDX
SBOM/notices/hashes → install/reinstall/ACL/smoke/uninstall on ephemeral Windows → run deterministic private gate →
upload 14-day unsigned private evidence. No release tag and no public publishing occur in Stage 7A.

## N. Git/GitHub plan

Work remains on `codex/stage-7a-production-hardening`. Logical commits are release policy, migrations, observability,
packaging/installer, and final regression/docs. Each is pushed without force. Remote CI and the manual RC workflow must
be inspected before private readiness is claimed. Safe code rollback uses `git revert <commit>`.
