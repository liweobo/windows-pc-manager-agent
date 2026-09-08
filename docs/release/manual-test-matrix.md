# Windows private-RC manual test matrix

`NOT_RUN` means exactly that; automated fake/synthetic evidence is never recorded as a manual pass.

| Scenario | Target | Status | Required evidence |
|---|---|---|---|
| Clean install / standard-user launch / tray | Windows 11 x64 clean VM | NOT_RUN | screenshots, versions, process token, logs |
| Safe reinstall and old-RC upgrade | Windows 11 x64 clean VM | NOT_RUN | preserved sentinel/config/DB migration evidence |
| Uninstall and reinstall | Windows 11 x64 clean VM | NOT_RUN | binaries absent, user-state sentinel retained |
| Program Files and Broker ACL | standard user + administrator | NOT_RUN | SDDL/ACL output and replacement denial |
| UAC one-shot Broker | project-owned safe test service | NOT_RUN | exact action, prompt, authenticated result, exit |
| Browser visible site and child cleanup | disposable test account | NOT_RUN | PIDs before/after; no profile persistence |
| Voice microphone/output cleanup | test audio only | NOT_RUN | device indicator and threads before/after |
| Office read/Preview/backup | synthetic documents | NOT_RUN | no macro/external refresh; output verification |
| Crash recovery and Safe Mode | synthetic interrupted task | NOT_RUN | no replay; old confirmation invalid |
| 100/125/150/200% DPI and keyboard focus | one/two monitors | NOT_RUN | readable dialogs, focus order, confirmation labels |
| Explorer restart/tray recreation | Windows 11 x64 | NOT_RUN | tray recreated once; no duplicate process |
| Sleep/resume, clock/network change | active read/provider task | NOT_RUN | stale review/expiry/friendly failure |
| OneDrive, Unicode, long and locked paths | synthetic user tree | NOT_RUN | fail-soft report and no scope escape |
| Defender/EDR interaction | default Windows Security | NOT_RUN | no exclusions or protection disablement |

Automated managed-Chromium lifecycle, synthetic file/performance, cancellation, migration corruption,
crash injection and domain security tests are recorded separately and do not change this table.
