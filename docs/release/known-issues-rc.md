# RC known issues and blockers

## Blocks public RC and v1

- Authenticode signing is `NOT_CONFIGURED` for Main, Broker and installer.
- Real-machine UAC/Broker, clean install, upgrade, uninstall/reinstall, Explorer restart, sleep/resume,
  multi-monitor/DPI and accessibility validation are not yet complete.
- The installer cannot be built locally on the current host because Inno Setup 6 is absent; the
  pinned GitHub Windows image path is prepared but its workflow result is pending.
- The private-RC installer chrome is English-only because the pinned Inno package omits unofficial translations;
  the installed application remains Chinese-first. A reviewed, vendored translation is deferred to Stage 7B.
- Only one Windows 11 x64 development build has been exercised. Windows 10 and ARM64 are not claimed.

## Private-RC limitations

- Only file analysis, system diagnostics, software analysis and optimization analysis are enabled.
- Broker and every write/action domain, Office, Voice, Browser, Memory, multi-Agent and Final
  Orchestrator entry surface are disabled by the production allow-list.
- Browser Worker is built and isolated for evidence but is not installed in the private RC.
- The measured Main onedir is approximately 180 MiB before installer compression because disabled-domain
  imports are still present in the static graph. This is a performance/size issue, not an enabled
  capability.
- No automatic updater, portable mode, repair command, app-data removal option or Windows auto-start.
- Telemetry is not implemented. Crash/diagnostic files remain local and are never automatically sent.

No confirmation bypass, privilege escalation, data-loss path or secret leak is accepted as a known
issue; finding one changes the release result to blocked.
