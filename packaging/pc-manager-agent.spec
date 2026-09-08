# PyInstaller specification for the standard-user production Main application.

from pathlib import Path

project_root = Path(SPEC).resolve().parent.parent
source_root = project_root / "src"
manifest = project_root / "packaging" / "manifests" / "pc-manager-agent.exe.manifest"
version_file = project_root / "build" / "generated" / "pc-manager-agent-version.txt"
production_hook = project_root / "packaging" / "runtime_hooks" / "production_mode.py"

if not version_file.is_file():
    raise SystemExit("Generate build version resources before running PyInstaller")

analysis = Analysis(
    [str(source_root / "pc_manager_agent" / "__main__.py")],
    pathex=[str(source_root)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(production_hook)],
    excludes=[
        "pc_manager_agent.privileged.mock_broker",
        "pc_manager_agent.privileged.registry",
        "pc_manager_agent.privileged.revalidation",
        "pytest",
        "ruff",
        "mypy",
    ],
    noarchive=False,
    optimize=1,
)
# The host build environment can place Poppler's incompatible ICU 78 DLLs on PATH.
# Qt 6 on Windows binds the operating-system ICU forwarder instead. Never let ambient
# build tools inject those unrelated DLLs into the trusted application directory.
ambient_icu_names = {"icuuc.dll", "icudt78.dll"}
analysis.binaries = [
    entry for entry in analysis.binaries if Path(entry[0]).name.casefold() not in ambient_icu_names
]
pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="pc-manager-agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=True,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    manifest=str(manifest),
    version=str(version_file),
)

distribution = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="pc-manager-agent",
)
