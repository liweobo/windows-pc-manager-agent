# Private RC installation

## Supported target

The current claim is Windows 11 x64 only. The development build host is Windows 11 x64 build
10.0.26200; other Windows 11 builds still require Stage 7B validation. Windows 10, ARM64, macOS and
Linux are not supported by this RC.

1. Obtain the private RC installer and `SHA256SUMS.txt` from the trusted private release workflow.
2. Compare the installer's SHA-256 with that file through a trusted channel.
3. Close any running Agent instance.
4. Run the installer. Installer UAC is expected because trusted binaries go under Program Files.
5. Leave the optional desktop shortcut unchecked unless desired.
6. Launch the Agent normally. The installed Main application remains standard-user.

The installer creates a Start Menu shortcut and places Main under
`%ProgramFiles%\Windows PC Manager Agent`; Broker files are under its `broker` child. User settings,
Audit, Memory and recovery state remain under `%LOCALAPPDATA%` and never belong in Program Files.

Code signing is currently **NOT CONFIGURED**. Windows may show a SmartScreen warning. Do not disable
SmartScreen, Defender or UAC to install this build. Treat it as an internal private RC, not a public
release. There is no portable installation and no automatic startup entry.
