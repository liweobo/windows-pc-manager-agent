# Windows release packaging

## Build strategy

Stage 7A uses three isolated PyInstaller onedir specifications and Python 3.13.1 for release CI.
Dependencies are resolved only through the committed `uv.lock`. The package version comes from
`src/pc_manager_agent/version.py`; generated Windows resources bind the same product version to all
executables.

| Component | Entry | Output | Privilege | Private RC shipment |
|---|---|---|---|---|
| Main | `pc_manager_agent.__main__` | `dist/pc-manager-agent` | asInvoker | Yes |
| Privileged Broker | `pc_manager_agent.broker.__main__` | `dist/pc-manager-privileged-broker` | asInvoker before explicit UAC launch | Installed but runtime-disabled |
| Browser Worker | `pc_manager_agent.browser.worker` | `dist/pc-manager-browser-worker` | asInvoker | Built/inspected, not installed because Browser is disabled |

Main excludes the Mock Broker modules. Broker excludes GUI, model/provider, Browser, Voice, Office
and Mock code. The Browser Worker excludes the Main UI, providers, Office and Broker. The artifact
inspector rejects source, tests, databases, logs, environment files, private-key material and known
synthetic credential markers, then records an aggregate inventory digest.

The build host used by Codex exposes an unrelated Poppler ICU 78 DLL through its environment. That
DLL is incompatible with Qt's Windows ICU forwarder. The Main spec explicitly removes the ambient
`icuuc.dll`/`icudt78.dll`, and artifact inspection rejects their reintroduction. This is a tested
supply/build-isolation rule, not a manual cleanup step.

## Commands

```powershell
uv sync --all-groups --locked
./scripts/build-release-binaries.ps1
uv run python scripts/generate_third_party_notices.py THIRD_PARTY_NOTICES.md
```

The binary build prints SHA-256 hashes and always reports `SIGNING_NOT_PRODUCTION_READY` until a real
certificate-store identity is configured. `scripts/sign-artifacts.ps1` accepts a certificate
thumbprint, uses SHA-256 timestamping and verifies Authenticode after signing. It never accepts a PFX,
password or private key file.

## Current limitations

The Main onedir remains large because the existing runtime composition statically imports some
disabled Office/Voice dependencies. Fixed feature guards prevent access, but package-size and cold-
start optimization remain a Stage 7B blocker review. PyInstaller reports optional SQLAlchemy driver
and tzdata hidden-import warnings; the application uses SQLite and does not claim those drivers.
These warnings must be reviewed on every clean build and may not be silently suppressed.
