# PyInstaller specification for the isolated one-shot Stage 4X2 Broker.

from pathlib import Path

project_root = Path(SPEC).resolve().parent.parent
source_root = project_root / "src"
manifest = project_root / "packaging" / "manifests" / "pc-manager-privileged-broker.exe.manifest"

analysis = Analysis(
    [str(source_root / "pc_manager_agent" / "broker" / "__main__.py")],
    pathex=[str(source_root)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PySide6",
        "openai",
        "pc_manager_agent.agents",
        "pc_manager_agent.memory",
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
    name="pc-manager-privileged-broker",
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
)

distribution = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="pc-manager-privileged-broker",
)
