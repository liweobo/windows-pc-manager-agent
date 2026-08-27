"""Explicit Windows UAC launcher for the fixed Stage 4X2 Broker executable."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pywintypes
import win32api
import win32con
import win32event
import win32process
from win32com.shell import shell, shellcon

from pc_manager_agent.platform_support.privileged_broker import (
    BrokerLaunchArguments,
    ElevatedBrokerLauncher,
    ElevatedProcessHandle,
    ElevationLaunchResult,
    ElevationLaunchStatus,
)

_ERROR_CANCELLED = 1223


class WindowsElevationError(RuntimeError):
    """Raised for invalid launcher configuration before Windows is called."""


class WindowsUacBrokerLauncher(ElevatedBrokerLauncher):
    """Launch exactly one configured Broker with ``ShellExecuteEx`` and ``runas``."""

    def __init__(self, expected_broker_path: Path) -> None:
        if not expected_broker_path.is_absolute():
            raise WindowsElevationError("Configured Broker path must be absolute")
        self._expected = expected_broker_path.resolve(strict=False)

    def launch(
        self,
        broker_path: Path,
        arguments: BrokerLaunchArguments,
    ) -> ElevationLaunchResult:
        """Show real UAC with only fixed opaque routing arguments."""
        candidate = broker_path.resolve(strict=True)
        if candidate != self._expected or candidate.suffix.casefold() != ".exe":
            raise WindowsElevationError("Broker launch path differs from configuration")
        if arguments.protocol_version != 1:
            raise WindowsElevationError("Broker launch protocol version is unsupported")
        rendezvous = arguments.rendezvous_id
        if len(rendezvous) != 43 or not rendezvous.replace("_", "a").replace("-", "a").isalnum():
            raise WindowsElevationError("Broker rendezvous ID is malformed")
        parameters = (
            f"--broker-instance {arguments.broker_instance_id} "
            f"--rendezvous {rendezvous} "
            f"--protocol-version {arguments.protocol_version} "
            f"--caller-pid {arguments.expected_caller_process_id} "
            f"--agent-instance {arguments.agent_instance_id}"
        )
        try:
            result = shell.ShellExecuteEx(
                fMask=(
                    shellcon.SEE_MASK_NOCLOSEPROCESS
                    | shellcon.SEE_MASK_FLAG_DDEWAIT
                    | shellcon.SEE_MASK_FLAG_NO_UI
                ),
                lpVerb="runas",
                lpFile=str(candidate),
                lpParameters=parameters,
                lpDirectory=str(candidate.parent),
                nShow=win32con.SW_HIDE,
            )
            handle = result.get("hProcess")
            if handle is None:
                return ElevationLaunchResult(
                    ElevationLaunchStatus.FAILED,
                    error_code=0,
                )
            return ElevationLaunchResult(
                ElevationLaunchStatus.STARTED,
                process=ElevatedProcessHandle(
                    process_id=int(win32process.GetProcessId(handle)),
                    native_handle=handle,
                ),
            )
        except (pywintypes.error, pywintypes.com_error) as exc:
            error_code = _winerror(exc)
            return ElevationLaunchResult(
                ElevationLaunchStatus.CANCELLED
                if error_code == _ERROR_CANCELLED
                else ElevationLaunchStatus.FAILED,
                error_code=error_code,
            )

    def close_process_handle(self, process: ElevatedProcessHandle) -> None:
        """Close ShellExecuteEx's process handle without terminating the process."""
        try:
            win32api.CloseHandle(cast(int, process.native_handle))
        except pywintypes.error:
            return

    def wait_for_exit(
        self,
        process: ElevatedProcessHandle,
        *,
        timeout_seconds: float,
    ) -> int | None:
        """Wait a bounded time for natural Broker exit; never terminate on timeout."""
        timeout_ms = max(1, min(int(timeout_seconds * 1_000), 120_000))
        handle = cast(int, process.native_handle)
        try:
            status = win32event.WaitForSingleObject(handle, timeout_ms)
            if status == win32event.WAIT_TIMEOUT:
                return None
            if status != win32event.WAIT_OBJECT_0:
                return None
            return int(win32process.GetExitCodeProcess(handle))
        except pywintypes.error:
            return None


def _winerror(exc: BaseException) -> int:
    value = getattr(exc, "winerror", None)
    if isinstance(value, int):
        return value
    hresult = getattr(exc, "hresult", None)
    if isinstance(hresult, int):
        return hresult & 0xFFFF
    for item in getattr(exc, "args", ()):
        if isinstance(item, int) and item > 0:
            return item & 0xFFFF
    return 0
