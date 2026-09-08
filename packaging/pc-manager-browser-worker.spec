# PyInstaller specification for the separate non-elevated disposable Browser Worker.

from pathlib import Path

project_root = Path(SPEC).resolve().parent.parent
source_root = project_root / "src"
manifest = project_root / "packaging" / "manifests" / "pc-manager-browser-worker.exe.manifest"
version_file = project_root / "build" / "generated" / "pc-manager-browser-worker-version.txt"

if not version_file.is_file():
    raise SystemExit("Generate build version resources before running PyInstaller")

analysis = Analysis(
    [str(source_root / "pc_manager_agent" / "browser" / "worker.py")],
    pathex=[str(source_root)],
    binaries=[],
    datas=[],
    hiddenimports=["playwright.sync_api"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PySide6",
        "openai",
        "pc_manager_agent.agents",
        "pc_manager_agent.broker",
        "pc_manager_agent.memory",
        "pc_manager_agent.office",
        "pc_manager_agent.providers",
        "pc_manager_agent.ui",
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="pc-manager-browser-worker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
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
    name="pc-manager-browser-worker",
)
