"""Windows App Installer-backed winget adapter with a finite command policy."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os

# Subprocess is restricted to the trusted Desktop App Installer alias and fixed argv.
import subprocess  # nosec B404
import tempfile
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Protocol, cast

from pc_manager_agent.domain.software_uninstall_analysis import RawInstalledSoftwareEntry
from pc_manager_agent.domain.system_diagnostics import SoftwareScope
from pc_manager_agent.domain.winget_uninstall import (
    DESKTOP_APP_INSTALLER_FAMILY,
    OFFICIAL_WINGET_SOURCE_IDENTIFIER,
    OFFICIAL_WINGET_SOURCE_NAME,
    NormalizedWingetPackage,
    RawWingetPackage,
    ValidatedWingetUninstallAction,
    WingetAvailability,
    WingetAvailabilityState,
    WingetExecutableIdentity,
    WingetInventoryState,
    WingetPackageIdentity,
    WingetPackageInventory,
    WingetProcessExecutionResult,
    WingetProcessResultCategory,
    fixed_winget_uninstall_arguments,
)
from pc_manager_agent.platform_support.base import CancellationSignal
from pc_manager_agent.tools.manifest import CancellationToken

_IO_REPARSE_TAG_APPEXECLINK: Final = 0x8000001B
_FSCTL_GET_REPARSE_POINT: Final = 0x000900A8
_FILE_FLAG_OPEN_REPARSE_POINT: Final = 0x00200000
_FILE_FLAG_BACKUP_SEMANTICS: Final = 0x02000000
_OPEN_EXISTING: Final = 3
_GENERIC_READ: Final = 0x80000000
_FILE_SHARE_ALL: Final = 0x00000007
_INVALID_HANDLE_VALUE: Final = ctypes.c_void_p(-1).value
_MAX_REPARSE_SIZE: Final = 16_384
_MAX_EXPORT_BYTES: Final = 16 * 1024 * 1024


class _Process(Protocol):
    pid: int

    def poll(self) -> int | None:
        """Return an exit code or ``None`` while running."""
        ...


class _PopenFactory(Protocol):
    def __call__(self, args: Sequence[str], **kwargs: object) -> _Process:
        """Start one process using an explicit argv array."""
        ...


class WindowsWingetAvailabilityPlatform:
    """Validate the current user's exact App Execution Alias and package family."""

    def __init__(self, local_app_data: Path | None = None) -> None:
        self._local_app_data = local_app_data

    def inspect(self) -> WingetAvailability:
        """Read reparse metadata directly and reject PATH, UNC, or family ambiguity."""
        local = self._local_app_data or _local_app_data()
        if local is None:
            return WingetAvailability(
                state=WingetAvailabilityState.UNAVAILABLE,
                reason="LOCALAPPDATA is unavailable; winget execution is disabled.",
            )
        alias = Path(os.path.abspath(local / "Microsoft" / "WindowsApps" / "winget.exe"))
        if alias.drive == "" or str(alias).startswith(("\\\\", "\\?\\", "\\.\\")):
            return WingetAvailability(
                state=WingetAvailabilityState.UNTRUSTED,
                reason="The winget alias path is not a literal local absolute path.",
            )
        try:
            stat = alias.lstat()
            tag, raw, values = _read_app_execution_alias(alias)
            package_full_name, _entry_point, target = values[:3]
            family = _package_family_from_full_name(package_full_name)
            if tag != _IO_REPARSE_TAG_APPEXECLINK:
                raise ValueError("winget path is not an App Execution Alias")
            identity = WingetExecutableIdentity(
                alias_path=alias,
                package_full_name=package_full_name,
                package_family_name=family,
                target_executable=target,
                reparse_tag=tag,
                alias_size=stat.st_size,
                alias_modified_ns=stat.st_mtime_ns,
                alias_sha256=hashlib.sha256(raw).hexdigest(),
            )
        except (OSError, ValueError, IndexError) as exc:
            return WingetAvailability(
                state=WingetAvailabilityState.UNTRUSTED,
                reason=f"App Installer alias identity could not be proven ({type(exc).__name__}).",
            )
        return WingetAvailability(
            state=WingetAvailabilityState.AVAILABLE,
            executable=identity,
            reason="A current-user App Installer winget alias was verified.",
        )


class WindowsWingetPackageInventoryPlatform:
    """Read package IDs and versions from a bounded ``winget export`` JSON file."""

    def __init__(
        self,
        availability: WindowsWingetAvailabilityPlatform,
        data_directory: Path,
        timeout_seconds: float = 60.0,
    ) -> None:
        self._availability = availability
        self._data_directory = data_directory
        self._timeout_seconds = timeout_seconds

    def inventory(
        self,
        max_items: int,
        cancellation: CancellationToken,
    ) -> WingetPackageInventory:
        """Export official-source records; localized table output is never authoritative."""
        if max_items < 1 or max_items > 20_000:
            raise ValueError("winget inventory max_items is outside the safe bound")
        if cancellation.cancellation_requested():
            return WingetPackageInventory(
                state=WingetInventoryState.FAILED,
                packages=(),
                warnings=("Package inventory was cancelled before collection.",),
            )
        availability = self._availability.inspect()
        if availability.executable is None:
            return WingetPackageInventory(
                state=WingetInventoryState.FAILED,
                packages=(),
                warnings=(availability.reason,),
            )
        self._data_directory.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.TemporaryDirectory(
                prefix="winget-inventory-",
                dir=self._data_directory,
            ) as temporary:
                output = Path(temporary) / "packages.json"
                argv = (
                    str(availability.executable.alias_path),
                    "export",
                    "--output",
                    str(output),
                    "--source",
                    OFFICIAL_WINGET_SOURCE_NAME,
                    "--include-versions",
                    "--disable-interactivity",
                )
                completed = subprocess.run(  # nosec B603
                    argv,
                    executable=str(availability.executable.alias_path),
                    cwd=temporary,
                    env=_sanitized_environment(os.environ),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                    check=False,
                    timeout=self._timeout_seconds,
                )
                if completed.returncode != 0:
                    return WingetPackageInventory(
                        state=WingetInventoryState.FAILED,
                        packages=(),
                        warnings=("winget export did not complete successfully.",),
                    )
                return _parse_export(output, max_items)
        except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError) as exc:
            return WingetPackageInventory(
                state=WingetInventoryState.FAILED,
                packages=(),
                warnings=(f"Package inventory failed ({type(exc).__name__}).",),
            )


class IndependentWingetSoftwarePackageProvider:
    """Avoid a false package-source warning when winget evidence is collected separately."""

    def collect(
        self,
        max_items: int,
        cancellation: CancellationSignal,
    ) -> tuple[tuple[RawInstalledSoftwareEntry, ...], tuple[str, ...], bool]:
        """Return no duplicate software rows; Package inventory has its own typed service."""
        del max_items, cancellation
        return (), (), False


class WindowsWingetUninstallPlatform:
    """Launch only a revalidated App Installer alias with the fixed argument array."""

    def __init__(
        self,
        availability: WindowsWingetAvailabilityPlatform,
        poll_seconds: float = 0.25,
        process_factory: _PopenFactory | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self._availability = availability
        self._poll_seconds = poll_seconds
        self._process_factory = process_factory or cast(_PopenFactory, subprocess.Popen)
        self._environment = environment or os.environ

    def uninstall(
        self,
        action: ValidatedWingetUninstallAction,
        cancellation: CancellationToken,
    ) -> WingetProcessExecutionResult:
        """Revalidate TOCTOU evidence, dispatch once, and never terminate a child process."""
        started = datetime.now(UTC)
        started_clock = time.monotonic()
        if cancellation.is_cancelled:
            return _process_result(
                WingetProcessResultCategory.CANCELLED_BEFORE_LAUNCH,
                started,
                started_clock,
                launched=False,
                cancellation_requested_before_launch=True,
            )
        fresh = self._availability.inspect()
        if (
            fresh.executable is None
            or fresh.executable.invariant_digest() != action.executable_identity.invariant_digest()
        ):
            return _process_result(
                WingetProcessResultCategory.LAUNCH_FAILED,
                started,
                started_clock,
                launched=False,
                error_type="WingetExecutableChanged",
            )
        executable = str(fresh.executable.alias_path)
        argv = (executable, *fixed_winget_uninstall_arguments(action.package_identity))
        try:
            process = self._process_factory(
                argv,
                executable=executable,
                cwd=str(fresh.executable.alias_path.parent),
                env=_sanitized_environment(self._environment),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                shell=False,
            )
        except OSError as exc:
            return _process_result(
                WingetProcessResultCategory.LAUNCH_FAILED,
                started,
                started_clock,
                launched=False,
                error_type=type(exc).__name__,
            )
        while True:
            exit_code = process.poll()
            if exit_code is not None:
                category = (
                    WingetProcessResultCategory.EXITED_ZERO
                    if exit_code == 0
                    else WingetProcessResultCategory.EXITED_NONZERO
                )
                return _process_result(
                    category,
                    started,
                    started_clock,
                    launched=True,
                    process_id=process.pid,
                    exit_code=exit_code,
                )
            if cancellation.cancellation_requested():
                return _process_result(
                    WingetProcessResultCategory.MONITORING_STOPPED,
                    started,
                    started_clock,
                    launched=True,
                    process_id=process.pid,
                    monitoring_stopped_after_launch=True,
                )
            time.sleep(self._poll_seconds)


def _local_app_data() -> Path | None:
    value = os.environ.get("LOCALAPPDATA")
    return Path(value) if value else None


def _read_app_execution_alias(path: Path) -> tuple[int, bytes, tuple[str, ...]]:
    """Read and minimally parse ``IO_REPARSE_TAG_APPEXECLINK`` data via Win32."""
    if os.name != "nt":
        raise OSError("App Execution Alias inspection requires Windows")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    )
    create_file.restype = ctypes.c_void_p
    handle = create_file(
        str(path),
        _GENERIC_READ,
        _FILE_SHARE_ALL,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_OPEN_REPARSE_POINT | _FILE_FLAG_BACKUP_SEMANTICS,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        buffer = ctypes.create_string_buffer(_MAX_REPARSE_SIZE)
        returned = ctypes.c_uint32()
        ok = kernel32.DeviceIoControl(
            handle,
            _FSCTL_GET_REPARSE_POINT,
            None,
            0,
            buffer,
            len(buffer),
            ctypes.byref(returned),
            None,
        )
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
        raw = buffer.raw[: returned.value]
    finally:
        kernel32.CloseHandle(handle)
    if len(raw) < 12:
        raise ValueError("truncated App Execution Alias reparse data")
    tag = int.from_bytes(raw[0:4], "little")
    data_length = int.from_bytes(raw[4:6], "little")
    payload = raw[8 : 8 + data_length]
    if tag != _IO_REPARSE_TAG_APPEXECLINK or len(payload) < 6:
        raise ValueError("not an App Execution Alias reparse point")
    # AppExecLink stores a version DWORD followed by NUL-delimited UTF-16 strings.
    decoded = payload[4:].decode("utf-16-le", errors="strict")
    values = tuple(value for value in decoded.split("\x00") if value)
    if len(values) < 3:
        raise ValueError("incomplete App Execution Alias identity")
    return tag, raw, values


def _package_family_from_full_name(package_full_name: str) -> str:
    """Derive and validate the family name from a full package identity."""
    parts = package_full_name.split("_")
    if len(parts) < 5 or not parts[0] or not parts[-1]:
        raise ValueError("invalid App Installer package full name")
    family = f"{parts[0]}_{parts[-1]}"
    if family != DESKTOP_APP_INSTALLER_FAMILY:
        raise ValueError("App Execution Alias belongs to an unexpected package family")
    return family


def _parse_export(path: Path, max_items: int) -> WingetPackageInventory:
    """Parse a bounded export while ignoring source URLs and all unknown fields."""
    stat = path.lstat()
    if stat.st_size > _MAX_EXPORT_BYTES or path.is_symlink():
        raise ValueError("winget export is not a bounded regular file")
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(document, dict) or not isinstance(document.get("Sources"), list):
        raise ValueError("winget export schema is invalid")
    packages: list[NormalizedWingetPackage] = []
    warnings: list[str] = []
    for source in document["Sources"]:
        if not isinstance(source, dict):
            continue
        details = source.get("SourceDetails")
        records = source.get("Packages")
        if not isinstance(details, dict) or not isinstance(records, list):
            continue
        source_name = details.get("Name")
        source_identifier = details.get("Identifier")
        if (
            source_name != OFFICIAL_WINGET_SOURCE_NAME
            or source_identifier != OFFICIAL_WINGET_SOURCE_IDENTIFIER
        ):
            continue
        for record in records:
            if len(packages) >= max_items:
                return WingetPackageInventory(
                    state=WingetInventoryState.TRUNCATED,
                    packages=tuple(packages),
                    warnings=(*warnings, "Package inventory reached its safe item limit."),
                )
            if not isinstance(record, dict):
                continue
            package_id = record.get("PackageIdentifier")
            version = record.get("Version")
            if not isinstance(package_id, str) or not isinstance(version, str):
                warnings.append("An incomplete official-source package record was ignored.")
                continue
            try:
                raw = RawWingetPackage(
                    package_id=package_id,
                    installed_version=version,
                    source_name=source_name,
                    source_identifier=source_identifier,
                    scope=SoftwareScope.CURRENT_USER,
                )
                identity = WingetPackageIdentity(**raw.model_dump())
                packages.append(
                    NormalizedWingetPackage(
                        identity=identity,
                        package_id=identity.package_id,
                        installed_version=identity.installed_version,
                    )
                )
            except ValueError:
                warnings.append("A package with unsafe identity fields was ignored.")
    return WingetPackageInventory(
        state=WingetInventoryState.COMPLETE,
        packages=tuple(packages),
        warnings=tuple(warnings),
    )


def _sanitized_environment(source: Mapping[str, str]) -> dict[str, str]:
    """Preserve ordinary Windows operation variables while dropping injectable configuration."""
    safe_names = {
        "ALLUSERSPROFILE",
        "APPDATA",
        "COMMONPROGRAMFILES",
        "COMMONPROGRAMFILES(X86)",
        "COMMONPROGRAMW6432",
        "COMSPEC",
        "HOMEDRIVE",
        "HOMEPATH",
        "LOCALAPPDATA",
        "NUMBER_OF_PROCESSORS",
        "OS",
        "PATHEXT",
        "PROCESSOR_ARCHITECTURE",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMW6432",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERDOMAIN",
        "USERNAME",
        "USERPROFILE",
        "WINDIR",
    }
    return {key: value for key, value in source.items() if key.upper() in safe_names}


def _process_result(
    category: WingetProcessResultCategory,
    started: datetime,
    started_clock: float,
    *,
    launched: bool,
    process_id: int | None = None,
    exit_code: int | None = None,
    cancellation_requested_before_launch: bool = False,
    monitoring_stopped_after_launch: bool = False,
    error_type: str | None = None,
) -> WingetProcessExecutionResult:
    """Build duration evidence without interpreting an exit as final success."""
    return WingetProcessExecutionResult(
        category=category,
        launched=launched,
        process_id=process_id,
        exit_code=exit_code,
        cancellation_requested_before_launch=cancellation_requested_before_launch,
        monitoring_stopped_after_launch=monitoring_stopped_after_launch,
        started_at=started,
        finished_at=datetime.now(UTC),
        duration_ms=max(0, int((time.monotonic() - started_clock) * 1_000)),
        error_type=error_type,
    )
