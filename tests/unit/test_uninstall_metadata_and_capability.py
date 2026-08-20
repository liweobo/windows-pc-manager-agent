from __future__ import annotations

import ctypes
from pathlib import Path
from types import SimpleNamespace

import pytest

from pc_manager_agent.domain.software_uninstall_analysis import (
    CapabilitySupport,
    RawInstalledSoftwareEntry,
    SoftwareSource,
    UninstallCapabilityType,
)
from pc_manager_agent.domain.system_diagnostics import SoftwareArchitecture, SoftwareScope
from pc_manager_agent.orchestration.software_capability import UninstallCapabilityResolver
from pc_manager_agent.orchestration.software_inventory import normalize_raw_entry
from pc_manager_agent.platform_support.windows import uninstall_metadata
from pc_manager_agent.platform_support.windows.uninstall_metadata import (
    _command_line_to_argv,
    parse_windows_uninstall_metadata,
)
from tests.fixtures.software_analysis import msi_entry, vendor_entry


def test_msi_requires_agreeing_windows_installer_and_product_code() -> None:
    raw = msi_entry()
    item = normalize_raw_entry(raw)
    assert item is not None
    capability = UninstallCapabilityResolver().resolve(item, raw)
    assert capability.capability_type is UninstallCapabilityType.MSI
    assert capability.support is CapabilitySupport.METADATA_SUPPORTED


def test_vendor_parser_accepts_existing_absolute_unicode_executable(tmp_path: Path) -> None:
    executable = tmp_path / "卸载 程序.exe"
    executable.write_bytes(b"synthetic")
    raw = vendor_entry(executable)
    item = normalize_raw_entry(raw)
    assert item is not None
    capability = UninstallCapabilityResolver().resolve(item, raw)
    assert capability.capability_type is UninstallCapabilityType.VENDOR_UNINSTALLER
    assert capability.support is CapabilitySupport.METADATA_SUPPORTED
    assert capability.parsed_metadata is not None
    assert capability.parsed_metadata.recognized_switches == ("--quiet", "--uninstall")


def test_parser_rejects_shell_wrapper_relative_unc_and_missing_executable(tmp_path: Path) -> None:
    wrapper = parse_windows_uninstall_metadata('cmd.exe /c "C:\\Bad\\remove.exe"')
    relative = parse_windows_uninstall_metadata("tools\\remove.exe --quiet")
    unc = parse_windows_uninstall_metadata(r"\\server\share\remove.exe --quiet")
    missing = parse_windows_uninstall_metadata(str(tmp_path / "missing.exe"))
    assert not wrapper.parsed and wrapper.wrapper_detected
    assert not relative.parsed and not relative.executable_is_absolute
    assert not unc.parsed and unc.executable_is_unc
    assert not missing.parsed and not missing.executable_exists


def test_parser_handles_absent_oversized_unparseable_empty_and_non_exe_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert not parse_windows_uninstall_metadata(None).parsed
    assert not parse_windows_uninstall_metadata("   ").parsed
    assert "safe parsing limit" in parse_windows_uninstall_metadata("x" * 32_769).warnings[0]

    monkeypatch.setattr(
        uninstall_metadata,
        "_command_line_to_argv",
        lambda _command: (_ for _ in ()).throw(ValueError("synthetic parser failure")),
    )
    assert "could not parse" in parse_windows_uninstall_metadata("broken").warnings[0]
    monkeypatch.setattr(uninstall_metadata, "_command_line_to_argv", lambda _command: ())
    assert "did not contain" in parse_windows_uninstall_metadata("empty").warnings[0]

    script = tmp_path / "remove.bat"
    script.write_bytes(b"synthetic")
    monkeypatch.setattr(uninstall_metadata, "_command_line_to_argv", _command_line_to_argv)
    parsed = parse_windows_uninstall_metadata(str(script))
    assert not parsed.parsed
    assert any("not an executable" in warning for warning in parsed.warnings)


def test_parser_fails_closed_when_path_probe_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "remove.exe"
    monkeypatch.setattr(Path, "is_file", lambda _path: (_ for _ in ()).throw(OSError("denied")))
    parsed = parse_windows_uninstall_metadata(str(executable))
    assert not parsed.parsed
    assert not parsed.executable_exists


def test_command_line_parser_has_conservative_non_windows_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(uninstall_metadata, "os", SimpleNamespace(name="posix"))
    assert _command_line_to_argv('"/opt/remove app" --quiet') == (
        '"/opt/remove app"',
        "--quiet",
    )


def test_command_line_to_argv_failure_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    class Function:
        argtypes: object = None
        restype: object = None

        def __call__(self, *_args: object) -> object:
            return ctypes.POINTER(ctypes.c_wchar_p)()

    class Shell32:
        CommandLineToArgvW = Function()

    class Kernel32:
        LocalFree = Function()

    monkeypatch.setattr(
        uninstall_metadata.ctypes,
        "WinDLL",
        lambda name, **_kwargs: Shell32() if name == "shell32" else Kernel32(),
    )
    with pytest.raises(OSError, match="CommandLineToArgvW failed"):
        _command_line_to_argv("anything")


def test_malformed_product_code_cannot_establish_msi_capability() -> None:
    raw = msi_entry(product_code="not-a-guid")
    item = normalize_raw_entry(raw)
    assert item is not None
    capability = UninstallCapabilityResolver().resolve(item, raw)
    assert capability.support is CapabilitySupport.UNSUPPORTED


def test_structured_msix_and_package_manager_sources_require_exact_ids() -> None:
    msix_raw = RawInstalledSoftwareEntry(
        raw_source_id="msix|family|full",
        source=SoftwareSource.MSIX,
        display_name="Store App",
        display_version="1.2.3.4",
        publisher="Store Publisher",
        scope=SoftwareScope.CURRENT_USER,
        architecture=SoftwareArchitecture.X64,
        package_family_name="Store.App_abc123",
        package_full_name="Store.App_1.2.3.4_x64__abc123",
        package_publisher_id="abc123",
    )
    package_raw = RawInstalledSoftwareEntry(
        raw_source_id="package|winget|Vendor.App",
        source=SoftwareSource.PACKAGE_MANAGER,
        display_name="Managed App",
        publisher="Vendor",
        scope=SoftwareScope.CURRENT_USER,
        architecture=SoftwareArchitecture.X64,
        package_manager_id="winget",
        package_id="Vendor.App",
    )
    resolver = UninstallCapabilityResolver()
    msix = normalize_raw_entry(msix_raw)
    package = normalize_raw_entry(package_raw)
    assert msix is not None and package is not None
    assert resolver.resolve(msix, msix_raw).capability_type is UninstallCapabilityType.MSIX
    assert (
        resolver.resolve(package, package_raw).capability_type
        is UninstallCapabilityType.PACKAGE_MANAGER
    )


def test_protected_unsupported_and_missing_capability_sources() -> None:
    resolver = UninstallCapabilityResolver()

    def capability(source: SoftwareSource, **updates: object):
        raw = RawInstalledSoftwareEntry(
            raw_source_id=f"source|{source.value}",
            source=source,
            display_name=f"{source.value} item",
            publisher="Vendor",
            scope=SoftwareScope.CURRENT_USER,
            architecture=SoftwareArchitecture.X64,
            **updates,
        )
        item = normalize_raw_entry(raw)
        assert item is not None
        return resolver.resolve(item, raw)

    assert (
        capability(SoftwareSource.DRIVER_PACKAGE).capability_type
        is UninstallCapabilityType.DRIVER_PACKAGE
    )
    assert (
        capability(SoftwareSource.WINDOWS_FEATURE).capability_type
        is UninstallCapabilityType.WINDOWS_COMPONENT
    )
    assert capability(SoftwareSource.MSIX).support is CapabilitySupport.UNSUPPORTED
    assert capability(SoftwareSource.PACKAGE_MANAGER).support is CapabilitySupport.UNSUPPORTED
    assert capability(SoftwareSource.PORTABLE).capability_type is UninstallCapabilityType.PORTABLE
    assert capability(SoftwareSource.REGISTRY).capability_type is UninstallCapabilityType.UNKNOWN


def test_capability_rejects_mismatched_raw_source_anchor() -> None:
    original = msi_entry()
    item = normalize_raw_entry(original)
    assert item is not None
    changed = msi_entry(product_code="{92345678-1234-1234-1234-1234567890AB}")
    result = UninstallCapabilityResolver().resolve(item, changed)
    assert result.support is CapabilitySupport.UNSUPPORTED
