"""Windows Installer inventory and fixed-client MSI uninstall adapters."""

from __future__ import annotations

import ctypes
import os

# This module permits subprocess only for the fixed system MSI executable and argument schema.
import subprocess  # nosec B404
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from pc_manager_agent.domain.software_uninstall_analysis import canonical_digest
from pc_manager_agent.domain.software_uninstall_execution import (
    MsiInstallContext,
    MsiInstallerExecutionResult,
    MsiInstallerResultCategory,
    MsiProductRegistration,
    ValidatedMsiProduct,
)
from pc_manager_agent.orchestration.msi_exit_codes import map_msi_exit_code
from pc_manager_agent.orchestration.software_msi_validation import normalize_product_code
from pc_manager_agent.tools.manifest import CancellationToken

_ERROR_SUCCESS = 0
_ERROR_MORE_DATA = 234
_ERROR_NO_MORE_ITEMS = 259
_ERROR_UNKNOWN_PRODUCT = 1605
_MSIINSTALLCONTEXT_USERMANAGED = 1
_MSIINSTALLCONTEXT_USERUNMANAGED = 2
_MSIINSTALLCONTEXT_MACHINE = 4
_MSIINSTALLCONTEXT_ALL = 7


class _PolledProcess(Protocol):
    def poll(self) -> int | None:
        """Return the child exit code or None while it is running."""
        ...


class WindowsMsiProductInventory:
    """Query exact product registrations through msi.dll without mutation."""

    def __init__(self, msi_dll: Any | None = None) -> None:
        if os.name != "nt" and msi_dll is None:
            raise OSError("Windows Installer inventory is available only on Windows")
        self._msi = msi_dll or ctypes.WinDLL("msi", use_last_error=True)
        self._configure_signatures()

    def registrations(self, product_code: str) -> tuple[MsiProductRegistration, ...]:
        """Enumerate every current-user/machine context for one exact ProductCode."""
        canonical = normalize_product_code(product_code)
        registrations: list[MsiProductRegistration] = []
        index = 0
        while True:
            installed_code = ctypes.create_unicode_buffer(39)
            context = ctypes.c_uint(0)
            sid = ctypes.create_unicode_buffer(512)
            sid_length = ctypes.c_uint(len(sid))
            result = int(
                self._msi.MsiEnumProductsExW(
                    canonical,
                    None,
                    _MSIINSTALLCONTEXT_ALL,
                    index,
                    installed_code,
                    ctypes.byref(context),
                    sid,
                    ctypes.byref(sid_length),
                )
            )
            if result == _ERROR_NO_MORE_ITEMS:
                break
            if result != _ERROR_SUCCESS:
                raise OSError(result, "MsiEnumProductsExW failed")
            enum_code = normalize_product_code(installed_code.value)
            enum_context = _context_from_raw(context.value)
            sid_value = sid.value or None
            query_sid = None if enum_context is MsiInstallContext.MACHINE else sid_value
            registrations.append(
                MsiProductRegistration(
                    product_code=enum_code,
                    context=enum_context,
                    user_sid_digest=canonical_digest(sid_value) if sid_value else None,
                    product_name=self._get_info(
                        enum_code, query_sid, context.value, "InstalledProductName"
                    ),
                    version=self._get_info(enum_code, query_sid, context.value, "VersionString"),
                    publisher=self._get_info(enum_code, query_sid, context.value, "Publisher"),
                    install_location=_optional_path(
                        self._get_info(enum_code, query_sid, context.value, "InstallLocation")
                    ),
                    installed=(self._get_info(enum_code, query_sid, context.value, "State") == "5"),
                )
            )
            index += 1
        return tuple(registrations)

    def _get_info(
        self,
        product_code: str,
        user_sid: str | None,
        context: int,
        property_name: str,
    ) -> str | None:
        length = ctypes.c_uint(0)
        result = int(
            self._msi.MsiGetProductInfoExW(
                product_code,
                user_sid,
                context,
                property_name,
                None,
                ctypes.byref(length),
            )
        )
        if result in {_ERROR_UNKNOWN_PRODUCT}:
            return None
        if result not in {_ERROR_SUCCESS, _ERROR_MORE_DATA}:
            return None
        buffer = ctypes.create_unicode_buffer(length.value + 1)
        capacity = ctypes.c_uint(len(buffer))
        result = int(
            self._msi.MsiGetProductInfoExW(
                product_code,
                user_sid,
                context,
                property_name,
                buffer,
                ctypes.byref(capacity),
            )
        )
        if result != _ERROR_SUCCESS:
            return None
        value = buffer.value.strip()
        return value or None

    def _configure_signatures(self) -> None:
        """Declare Unicode API signatures when the loaded object supports ctypes metadata."""
        enum = self._msi.MsiEnumProductsExW
        info = self._msi.MsiGetProductInfoExW
        if hasattr(enum, "argtypes"):
            enum.argtypes = [
                ctypes.c_wchar_p,
                ctypes.c_wchar_p,
                ctypes.c_uint,
                ctypes.c_uint,
                ctypes.c_wchar_p,
                ctypes.POINTER(ctypes.c_uint),
                ctypes.c_wchar_p,
                ctypes.POINTER(ctypes.c_uint),
            ]
            enum.restype = ctypes.c_uint
        if hasattr(info, "argtypes"):
            info.argtypes = [
                ctypes.c_wchar_p,
                ctypes.c_wchar_p,
                ctypes.c_uint,
                ctypes.c_wchar_p,
                ctypes.c_wchar_p,
                ctypes.POINTER(ctypes.c_uint),
            ]
            info.restype = ctypes.c_uint


class WindowsMsiUninstallPlatform:
    """Run only fixed ``msiexec /x ProductCode /norestart`` argument arrays."""

    def __init__(
        self,
        *,
        system_directory: Path | None = None,
        popen_factory: Callable[..., _PolledProcess] | None = None,
        poll_interval_seconds: float = 0.25,
        long_running_seconds: float = 900.0,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if poll_interval_seconds <= 0 or long_running_seconds <= 0:
            raise ValueError("MSI monitor intervals must be positive")
        self._system_directory = system_directory
        self._popen = popen_factory or subprocess.Popen
        self._poll_interval = poll_interval_seconds
        self._long_running = long_running_seconds
        self._monotonic = monotonic
        self._sleep = sleeper

    def uninstall(
        self,
        product: ValidatedMsiProduct,
        cancellation: CancellationToken,
    ) -> MsiInstallerExecutionResult:
        """Launch the fixed interactive client and never terminate it after dispatch."""
        if cancellation.cancellation_requested():
            return MsiInstallerExecutionResult(
                category=MsiInstallerResultCategory.USER_CANCELLED,
                launched=False,
                cancellation_requested_before_launch=True,
            )
        started_at = datetime.now(UTC)
        started = self._monotonic()
        try:
            system_directory = (self._system_directory or _get_system_directory()).resolve(
                strict=True
            )
            executable = (system_directory / "msiexec.exe").resolve(strict=True)
            if executable.parent != system_directory or executable.name.casefold() != "msiexec.exe":
                raise OSError("Resolved Windows Installer client escaped the system directory")
            process = self._popen(
                [str(executable), "/x", product.product_code, "/norestart"],
                shell=False,
                cwd=str(system_directory),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        except OSError as exc:
            elapsed = max(0, round((self._monotonic() - started) * 1000))
            return MsiInstallerExecutionResult(
                category=MsiInstallerResultCategory.LAUNCH_FAILED,
                launched=False,
                started_at=started_at,
                duration_ms=elapsed,
                error_type=type(exc).__name__,
            )
        cancellation_after_launch = False
        while True:
            exit_code = process.poll()
            elapsed_seconds = self._monotonic() - started
            if exit_code is not None:
                break
            cancellation_after_launch = (
                cancellation_after_launch or cancellation.cancellation_requested()
            )
            if elapsed_seconds >= self._long_running:
                duration_ms = max(0, round(elapsed_seconds * 1000))
                return MsiInstallerExecutionResult(
                    category=MsiInstallerResultCategory.MONITORING_DETACHED,
                    launched=True,
                    cancellation_requested_after_launch=cancellation_after_launch,
                    long_running_observed=True,
                    started_at=started_at,
                    duration_ms=duration_ms,
                )
            self._sleep(self._poll_interval)
        duration_ms = max(0, round((self._monotonic() - started) * 1000))
        normalized_exit = int(exit_code)
        if normalized_exit < 0:
            normalized_exit &= 0xFFFFFFFF
        return MsiInstallerExecutionResult(
            category=map_msi_exit_code(normalized_exit),
            exit_code=normalized_exit,
            launched=True,
            cancellation_requested_after_launch=cancellation_after_launch,
            started_at=started_at,
            duration_ms=duration_ms,
        )


def _context_from_raw(value: int) -> MsiInstallContext:
    return {
        _MSIINSTALLCONTEXT_USERMANAGED: MsiInstallContext.USER_MANAGED,
        _MSIINSTALLCONTEXT_USERUNMANAGED: MsiInstallContext.USER_UNMANAGED,
        _MSIINSTALLCONTEXT_MACHINE: MsiInstallContext.MACHINE,
    }.get(value, MsiInstallContext.UNKNOWN)


def _optional_path(value: str | None) -> Path | None:
    return Path(value) if value else None


def _get_system_directory() -> Path:
    if os.name != "nt":
        raise OSError("Windows Installer execution is available only on Windows")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_system_directory = kernel32.GetSystemDirectoryW
    get_system_directory.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
    get_system_directory.restype = ctypes.c_uint
    buffer = ctypes.create_unicode_buffer(32_768)
    written = int(get_system_directory(buffer, len(buffer)))
    if written == 0 or written >= len(buffer):
        raise ctypes.WinError(ctypes.get_last_error())
    return Path(buffer.value)


def current_process_is_elevated() -> bool:
    """Return TokenElevation without requesting or changing any privilege."""
    if os.name != "nt":
        return False
    from win32api import CloseHandle, GetCurrentProcess
    from win32con import TOKEN_QUERY
    from win32security import GetTokenInformation, OpenProcessToken, TokenElevation

    token = OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY)
    try:
        return bool(GetTokenInformation(token, TokenElevation))
    finally:
        CloseHandle(token)
