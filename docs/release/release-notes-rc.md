# Windows PC Manager Agent 1.0.0-rc.1 (private candidate)

This candidate is for controlled Windows 11 x64 validation. It enables only approved-directory file
analysis, read-only system diagnostics, read-only software analysis and read-only optimization
advice. Every enabled workflow remains plan-first and audited.

Stage 7A adds fail-closed production configuration, safe-mode startup, versioned SQLite migration
with verified backup, redacted rotating logs, local crash evidence, reviewed diagnostic export,
separate frozen Main/Broker/Browser Worker builds, an Inno Setup installer pipeline, artifact/SBOM/
license checks and deterministic release gates.

Signing is not configured. All file/system writes, Broker execution, uninstall/cleanup, Office,
Voice, Browser, Memory, multi-Agent and Final Orchestrator surfaces are disabled in this private RC.
See `known-issues-rc.md` before installing. This is not v1.0 and no final release tag is created.
