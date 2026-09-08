# Stage 7A feature freeze

Candidate version: `1.0.0-rc.1` (private, unsigned; no final v1 tag).

Stage 7A changes release safety, data integrity, packaging, compatibility, performance,
accessibility and documentation only. It does not add a new business capability.

## Enabled in the first private RC

- File analysis: approved-root metadata scan, large/inactive/duplicate reports.
- System diagnostics: read-only system, CPU, memory, disk, process, startup, service and
  installed-software observations.
- Software analysis: read-only identity and uninstall-capability report; no dispatch.
- Optimization analysis: read-only storage/performance observations and non-executable advice.
- Audit and settings needed to support those R0 workflows.

All enabled R0 workflows still require their existing plan review and visual confirmation.
The feature allow-list is not an execution permission.

## Disabled in the first private RC

- File move/rename, Recycle Bin operations and direct system cleanup.
- Process lifecycle changes, startup changes and service lifecycle/configuration changes.
- MSI, Vendor, winget and MSIX uninstall execution.
- Residual cleanup and Recycle Bin emptying.
- The real and Mock privileged Broker.
- Office, Voice and Browser automation.
- persistent Memory, multi-Agent dispatch and Final Orchestrator entry surfaces.
- automatic update, portable mode, browser extension, Windows service and elevated Main mode.

Disabled means that the tab or route is hidden and the runtime composition guard rejects direct
preparation. It does not mean that existing source code or its safety tests are deleted.

## Experimental

Office, Voice, Browser, controlled uninstall/cleanup, privileged actions, Memory, multi-Agent and
long-task orchestration remain available only in source development builds for Stage 7A regression.
Development visibility never changes their existing Safety, Preview, confirmation, Fresh identity,
verification, audit or recovery requirements.

## Known limitations

- Signing is **NOT CONFIGURED**. An unsigned private RC cannot enable the privileged Broker.
- The first RC target is Windows 11 x64 only. Other versions/architectures require recorded tests.
- There is no automatic update. Users install an explicitly downloaded newer installer.
- Disabled actions cannot be enabled through environment-variable feature lists.

## Deferred

New tools, wider Broker actions, unattended execution, silent update, Windows 10, ARM64, macOS,
Linux, generic browser actions and any new system mutation are deferred beyond v1.0.

Changing this freeze requires a reviewed code change, updated release evidence and a new release
candidate. An LLM, model response, Memory value, webpage or document cannot change it.
