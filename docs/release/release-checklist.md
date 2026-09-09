# Stage 7A release checklist

## Automated gate

- [x] Feature freeze and fixed private-RC allow-list.
- [x] Single version source: `1.0.0-rc.1`; no final v1 tag.
- [x] Locked dependency install and runtime-only third-party notices.
- [x] Production config, migration/backup, logging, crash and diagnostic tests.
- [x] Main/Broker/Browser Worker isolated specs and artifact inspector.
- [x] Release-blocking global invariant test and executable evidence gate.
- [x] Standard Main/Broker manifests remain `asInvoker`.
- [x] Installer source, safe reinstall/uninstall semantics and ACL test script.
- [x] Remote release workflow, SBOM, installer build and ephemeral lifecycle pass (run `34303873949`).
- [x] Local Ruff, mypy, pytest/coverage, performance, Playwright, Bandit and pip-audit evidence recorded.
- [x] Remote Gitleaks and release-workflow evidence recorded for commit
  `e1bf5f785d4c1c11668e5079f7bd1e90daa56189`.

## Manual/signing gate

- [ ] Authenticode certificate and verified Main/Broker/installer signatures (`NOT_CONFIGURED`).
- [ ] Clean install, upgrade, uninstall, reinstall and standard-user launch on a clean Windows 11 VM.
- [ ] Real UAC/Broker safe-target test.
- [ ] Accessibility, DPI, multi-monitor, Explorer restart, sleep/resume and network/clock-change matrix.
- [ ] Visible Browser, real Voice-device, Office and crash-recovery checklists where those features are
  considered for a later RC.
- [ ] Branch-protection evidence and reviewed RC release notes.

`scripts/evaluate_release_gate.py` accepts only the closed check/status vocabulary and exits nonzero
when any required evidence is absent, failed, not run or not configured. The release workflow reaches
the private-RC gate only after prior steps succeed. Public RC additionally requires signing, manual
Windows/UAC and branch-protection evidence; V1 also requires final regression and a release tag.
