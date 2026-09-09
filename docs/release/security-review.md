# Stage 7A security boundary review

| Boundary | Expected property | Automated evidence | Manual evidence | Current status |
|---|---|---|---|---|
| File | Approved roots, no reparse widening, no permanent-delete fallback | path/file/security suites | Long path, OneDrive, locked file | AUTOMATED_PASS / MANUAL_PENDING |
| Process/startup/service/software/cleanup | Fresh identity, exact Preview, confirmation and no fallback | domain unit/integration/security suites | Real safe targets/UAC | SOURCE_REGRESSION_ONLY; disabled in RC |
| Privilege | Standard-user Main, separate one-shot allow-listed Broker | protocol, replay, trust/version and package-xref tests | Signed install + UAC | AUTOMATED_PASS / MANUAL_PENDING / disabled |
| Office | Selected-file scope, static transforms, no macro/COM/shell | Office 95% boundary suite | Real Office samples | SOURCE_REGRESSION_ONLY; disabled |
| Voice | PTT only, visual review, no voice confirmation | Voice 95% boundary suite | Real mic/output | SOURCE_REGRESSION_ONLY; disabled |
| Browser | disposable Worker, URL/origin policy, no generic action | Browser boundary + managed lifecycle | Visible sites | SOURCE_REGRESSION_ONLY; disabled |
| Memory/Agent | low-risk hints only; no authority or secret context | Stage 5D boundary suite | UX/data reset | SOURCE_REGRESSION_ONLY; disabled |
| Final Orchestrator | no replay/global execution/Confirm All | Stage 5E invariant/crash suites | crash/resume UX | SOURCE_REGRESSION_ONLY; disabled |
| Installer | trusted Program Files placement and ordinary-user non-write ACL | ephemeral CI lifecycle PASS (`34303873949`) | clean/upgrade/uninstall | AUTOMATED_PASS / MANUAL_PENDING |

Static AST release checks block `eval`, `exec`, `os.system`, unsafe pickle loading and
`subprocess(..., shell=True)`. Bandit, pip-audit and Gitleaks are release blocking. Fixed subprocess
adapters remain covered by their exact executable/argv/environment/timeout/verification tests.

Threat actors reviewed include malicious local/same-user processes, another logged-in user,
malicious model/web/document/download content, stale state, compromised dependencies, installer or
Broker replacement, IPC replay and user mistakes. Same-user malware and unrecognized sensitive text
remain residual risks; code signing and real-device validation are still blockers.
