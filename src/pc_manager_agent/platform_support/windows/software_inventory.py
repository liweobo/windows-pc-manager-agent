"""Read-only Windows installed-software inventory with raw metadata separation."""

from __future__ import annotations

import re
import winreg
from pathlib import Path

from pc_manager_agent.domain.software_uninstall_analysis import (
    RawInstalledSoftwareEntry,
    RegistryHive,
    RegistryView,
    SoftwareSource,
)
from pc_manager_agent.domain.system_diagnostics import SoftwareArchitecture, SoftwareScope
from pc_manager_agent.platform_support.base import CancellationSignal
from pc_manager_agent.platform_support.software_inventory import PackageInventoryProvider

_UNINSTALL_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
_PRODUCT_CODE = re.compile(
    r"^\{[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}$"
)


class UnavailablePackageInventoryProvider:
    """Honest no-op when a vetted structured MSIX/package binding is unavailable."""

    def collect(
        self,
        max_items: int,
        cancellation: CancellationSignal,
    ) -> tuple[tuple[RawInstalledSoftwareEntry, ...], tuple[str, ...], bool]:
        """Report source unavailability without using PowerShell or package CLI output."""
        del max_items, cancellation
        return (), ("Structured current-user MSIX/package metadata is unavailable.",), False


class WindowsSoftwareInventoryPlatform:
    """Enumerate bounded registry and injected structured package metadata read-only."""

    def __init__(self, package_provider: PackageInventoryProvider | None = None) -> None:
        self._package_provider = package_provider or UnavailablePackageInventoryProvider()

    def collect_raw(
        self,
        max_items: int,
        cancellation: CancellationSignal,
    ) -> tuple[tuple[RawInstalledSoftwareEntry, ...], tuple[str, ...], bool]:
        """Read current-user and machine uninstall views without invoking any command."""
        if max_items < 1:
            raise ValueError("max_items must be positive")
        records: list[RawInstalledSoftwareEntry] = []
        warnings: list[str] = []
        truncated = False
        for hive, hive_name, scope, view, architecture, access in _registry_sources():
            if cancellation.cancellation_requested():
                warnings.append("Software inventory was cancelled.")
                truncated = True
                break
            remaining = max_items - len(records)
            if remaining <= 0:
                truncated = True
                break
            source_records, source_warnings, source_truncated = self._collect_registry_view(
                hive,
                hive_name,
                scope,
                view,
                architecture,
                access,
                remaining,
                cancellation,
            )
            records.extend(source_records)
            warnings.extend(source_warnings)
            truncated = truncated or source_truncated
            if len(records) >= max_items:
                truncated = True
                break
        if len(records) < max_items and not cancellation.cancellation_requested():
            package_records, package_warnings, package_truncated = self._package_provider.collect(
                max_items - len(records), cancellation
            )
            records.extend(package_records)
            warnings.extend(package_warnings)
            truncated = truncated or package_truncated
        return tuple(records[:max_items]), tuple(warnings), truncated or len(records) > max_items

    @staticmethod
    def _collect_registry_view(
        hive: int,
        hive_name: RegistryHive,
        scope: SoftwareScope,
        view: RegistryView,
        architecture: SoftwareArchitecture,
        access: int,
        limit: int,
        cancellation: CancellationSignal,
    ) -> tuple[tuple[RawInstalledSoftwareEntry, ...], tuple[str, ...], bool]:
        """Read one explicit registry view and preserve incomplete entries as untrusted raw data."""
        records: list[RawInstalledSoftwareEntry] = []
        warnings: list[str] = []
        try:
            with winreg.OpenKey(hive, _UNINSTALL_KEY, 0, access) as parent:
                count = winreg.QueryInfoKey(parent)[0]
                for index in range(count):
                    if cancellation.cancellation_requested():
                        return tuple(records), tuple(warnings), True
                    if len(records) >= limit:
                        return tuple(records), tuple(warnings), True
                    key_name = winreg.EnumKey(parent, index)
                    key_path = f"{_UNINSTALL_KEY}\\{key_name}"
                    try:
                        with winreg.OpenKey(hive, key_path, 0, access) as key:
                            values = _read_values(key)
                    except OSError as exc:
                        warnings.append(
                            f"One {hive_name.value}/{view.value} software entry was unreadable: "
                            f"{type(exc).__name__}."
                        )
                        continue
                    windows_installer = _optional_bool(values.get("WindowsInstaller"))
                    product_code = _text(values.get("ProductCode"))
                    if (
                        product_code is None
                        and windows_installer
                        and _PRODUCT_CODE.fullmatch(key_name)
                    ):
                        product_code = key_name.upper()
                    uninstall = _text(values.get("UninstallString"))
                    quiet = _text(values.get("QuietUninstallString"))
                    if windows_installer and product_code and _PRODUCT_CODE.fullmatch(product_code):
                        source = SoftwareSource.MSI
                    elif uninstall or quiet:
                        source = SoftwareSource.VENDOR
                    else:
                        source = SoftwareSource.REGISTRY
                    location = _text(values.get("InstallLocation"))
                    records.append(
                        RawInstalledSoftwareEntry(
                            raw_source_id=f"{hive_name.value}|{view.value}|{key_path}",
                            source=source,
                            display_name=_text(values.get("DisplayName")),
                            display_version=_text(values.get("DisplayVersion")),
                            publisher=_text(values.get("Publisher")),
                            install_location=Path(location) if location else None,
                            install_date=_text(values.get("InstallDate")),
                            estimated_size_bytes=_estimated_size(values.get("EstimatedSize")),
                            scope=scope,
                            architecture=architecture,
                            registry_hive=hive_name,
                            registry_view=view,
                            registry_key=key_path,
                            product_code=product_code,
                            windows_installer=windows_installer,
                            system_component=_optional_bool(values.get("SystemComponent")),
                            package_manager_id=_text(values.get("PackageManager")),
                            package_id=_text(values.get("PackageIdentifier")),
                            uninstall_string=uninstall,
                            quiet_uninstall_string=quiet,
                        )
                    )
        except FileNotFoundError:
            return (), (), False
        except OSError as exc:
            warnings.append(
                f"Software registry source {hive_name.value}/{view.value} was unavailable: "
                f"{type(exc).__name__}."
            )
        return tuple(records), tuple(warnings), False


def _registry_sources() -> tuple[
    tuple[int, RegistryHive, SoftwareScope, RegistryView, SoftwareArchitecture, int], ...
]:
    """Return explicit user/machine and 32/64-bit views in deterministic order."""
    return (
        (
            winreg.HKEY_CURRENT_USER,
            RegistryHive.CURRENT_USER,
            SoftwareScope.CURRENT_USER,
            RegistryView.X64,
            SoftwareArchitecture.X64,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ),
        (
            winreg.HKEY_CURRENT_USER,
            RegistryHive.CURRENT_USER,
            SoftwareScope.CURRENT_USER,
            RegistryView.X86,
            SoftwareArchitecture.X86,
            winreg.KEY_READ | winreg.KEY_WOW64_32KEY,
        ),
        (
            winreg.HKEY_LOCAL_MACHINE,
            RegistryHive.LOCAL_MACHINE,
            SoftwareScope.LOCAL_MACHINE,
            RegistryView.X64,
            SoftwareArchitecture.X64,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ),
        (
            winreg.HKEY_LOCAL_MACHINE,
            RegistryHive.LOCAL_MACHINE,
            SoftwareScope.LOCAL_MACHINE,
            RegistryView.X86,
            SoftwareArchitecture.X86,
            winreg.KEY_READ | winreg.KEY_WOW64_32KEY,
        ),
    )


def _read_values(key: winreg.HKEYType) -> dict[str, object]:
    """Read a bounded allow-list of metadata values from an already-open key."""
    names = (
        "DisplayName",
        "DisplayVersion",
        "Publisher",
        "InstallLocation",
        "InstallDate",
        "EstimatedSize",
        "UninstallString",
        "QuietUninstallString",
        "WindowsInstaller",
        "SystemComponent",
        "ProductCode",
        "PackageManager",
        "PackageIdentifier",
    )
    values: dict[str, object] = {}
    for name in names:
        try:
            value, _kind = winreg.QueryValueEx(key, name)
        except OSError:
            continue
        values[name] = value
    return values


def _text(value: object | None) -> str | None:
    """Normalize one string-like registry value without inventing a missing field."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_bool(value: object | None) -> bool | None:
    """Parse only recognized registry boolean representations."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if not isinstance(value, (int, str)):
        return None
    try:
        integer = int(value)
    except (TypeError, ValueError):
        return None
    return bool(integer) if integer in {0, 1} else None


def _estimated_size(value: object | None) -> int | None:
    """Convert the registry EstimatedSize KiB value to bytes with bounds checks."""
    if value is None:
        return None
    if not isinstance(value, (int, str)):
        return None
    try:
        kibibytes = int(value)
    except (TypeError, ValueError):
        return None
    return kibibytes * 1024 if 0 <= kibibytes <= 2**53 else None
