"""Checked Win32 process inspection, WM_CLOSE, termination, and verification."""

from __future__ import annotations

import ctypes
import time
from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import psutil
import pywintypes
import win32api
import win32con
import win32gui
import win32process
import win32security
import win32service
import winerror

from pc_manager_agent.domain.process_actions import (
    ProcessActionErrorCode,
    ProcessIdentity,
    ProcessMemberResult,
    ProcessMemberResultState,
    ProcessObservation,
)
from pc_manager_agent.platform_support.base import CancellationSignal

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_TERMINATE = 0x0001
_SYNCHRONIZE = 0x00100000
_WAIT_OBJECT_0 = 0x00000000
_WAIT_TIMEOUT = 0x00000102
_WM_CLOSE = 0x0010
_PROTECTION_LEVEL_NONE = 0xFFFFFFFE
_PROCESS_PROTECTION_LEVEL_INFO = 7
_WINDOW_POLL_SECONDS = 0.1

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class _FileTime(ctypes.Structure):
    _fields_ = (("low", ctypes.c_ulong), ("high", ctypes.c_ulong))


class _ProtectionLevelInformation(ctypes.Structure):
    _fields_ = (("protection_level", ctypes.c_ulong),)


_kernel32.GetProcessTimes.argtypes = (
    ctypes.c_void_p,
    ctypes.POINTER(_FileTime),
    ctypes.POINTER(_FileTime),
    ctypes.POINTER(_FileTime),
    ctypes.POINTER(_FileTime),
)
_kernel32.GetProcessTimes.restype = ctypes.c_int
_kernel32.QueryFullProcessImageNameW.argtypes = (
    ctypes.c_void_p,
    ctypes.c_ulong,
    ctypes.c_wchar_p,
    ctypes.POINTER(ctypes.c_ulong),
)
_kernel32.QueryFullProcessImageNameW.restype = ctypes.c_int
_kernel32.IsProcessCritical.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_int))
_kernel32.IsProcessCritical.restype = ctypes.c_int
_kernel32.GetProcessInformation.argtypes = (
    ctypes.c_void_p,
    ctypes.c_int,
    ctypes.c_void_p,
    ctypes.c_ulong,
)
_kernel32.GetProcessInformation.restype = ctypes.c_int
_kernel32.ProcessIdToSessionId.argtypes = (ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong))
_kernel32.ProcessIdToSessionId.restype = ctypes.c_int
_kernel32.TerminateProcess.argtypes = (ctypes.c_void_p, ctypes.c_uint)
_kernel32.TerminateProcess.restype = ctypes.c_int
_kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
_kernel32.WaitForSingleObject.restype = ctypes.c_ulong


class WindowsProcessManagementPlatform:
    """Use explicit query/window/termination APIs without shell commands or elevation."""

    def list_processes(self, max_processes: int) -> tuple[ProcessObservation, ...]:
        """Return current fully identified observations; inaccessible rows are omitted."""
        bounded = max(1, min(max_processes, 2_000))
        window_map = _window_counts()
        service_map = _active_service_processes()
        observations: list[ProcessObservation] = []
        for process in psutil.process_iter():
            if len(observations) >= bounded:
                break
            observation = self._inspect_pid(process.pid, window_map, service_map)
            if observation is not None:
                observations.append(observation)
        return tuple(observations)

    def inspect_process(self, pid: int) -> ProcessObservation | None:
        """Return a fresh complete identity and current protection metadata."""
        return self._inspect_pid(pid, _window_counts(), _active_service_processes())

    def request_graceful_exit(
        self,
        identity: ProcessIdentity,
        timeout_seconds: float,
        cancellation: CancellationSignal,
    ) -> ProcessMemberResult:
        """Post WM_CLOSE only after handle-bound identity revalidation, then wait."""
        try:
            handle = win32api.OpenProcess(
                _PROCESS_QUERY_LIMITED_INFORMATION | _SYNCHRONIZE,
                False,
                identity.pid,
            )
        except pywintypes.error as exc:
            return _open_failure(identity, exc)
        try:
            current = _identity_from_handle(handle, identity.pid)
            if current.canonical_digest() != identity.canonical_digest():
                return _identity_changed(identity)
            windows = _windows_for_pid(identity.pid)
            if not windows:
                state = _wait_for_process(handle, timeout_seconds, cancellation)
                if state is ProcessMemberResultState.EXITED:
                    return ProcessMemberResult(
                        identity_digest=identity.canonical_digest(),
                        pid=identity.pid,
                        state=state,
                        message=(
                            "This helper owned no window, but its original identity exited "
                            "with the application group"
                        ),
                    )
                if state is ProcessMemberResultState.CANCELLED_WAITING:
                    return ProcessMemberResult(
                        identity_digest=identity.canonical_digest(),
                        pid=identity.pid,
                        state=state,
                        message="No window was notified; waiting for this helper was cancelled",
                    )
                if state is ProcessMemberResultState.FAILED:
                    return ProcessMemberResult(
                        identity_digest=identity.canonical_digest(),
                        pid=identity.pid,
                        state=state,
                        message="Windows returned an unexpected helper-process wait status",
                    )
                return ProcessMemberResult(
                    identity_digest=identity.canonical_digest(),
                    pid=identity.pid,
                    state=ProcessMemberResultState.UNSUPPORTED,
                    message="No target-owned top-level window supports WM_CLOSE",
                )
            posted = 0
            last_error: int | None = None
            for window in windows:
                try:
                    win32gui.PostMessage(window, _WM_CLOSE, 0, 0)
                    posted += 1
                except pywintypes.error as exc:
                    last_error = int(exc.winerror)
            if posted == 0:
                return ProcessMemberResult(
                    identity_digest=identity.canonical_digest(),
                    pid=identity.pid,
                    state=ProcessMemberResultState.ACCESS_DENIED,
                    platform_error_code=last_error,
                    message="Windows rejected every WM_CLOSE request",
                )
            state = _wait_for_process(handle, timeout_seconds, cancellation)
            message = {
                ProcessMemberResultState.EXITED: "Original process identity exited after WM_CLOSE",
                ProcessMemberResultState.CANCELLED_WAITING: (
                    "WM_CLOSE was already sent; only subsequent waiting was cancelled"
                ),
                ProcessMemberResultState.STILL_RUNNING: (
                    "Process remained active after the graceful-exit timeout"
                ),
                ProcessMemberResultState.FAILED: (
                    "Windows returned an unexpected status while waiting for process exit"
                ),
            }[state]
            return ProcessMemberResult(
                identity_digest=identity.canonical_digest(),
                pid=identity.pid,
                state=state,
                windows_notified=posted,
                message=message,
            )
        except (OSError, pywintypes.error) as exc:
            return _platform_failure(identity, exc)
        finally:
            win32api.CloseHandle(handle)

    def force_terminate(
        self,
        identity: ProcessIdentity,
        timeout_seconds: float,
    ) -> ProcessMemberResult:
        """Terminate only the exact handle-revalidated process and verify it exits."""
        try:
            handle = win32api.OpenProcess(
                _PROCESS_QUERY_LIMITED_INFORMATION | _PROCESS_TERMINATE | _SYNCHRONIZE,
                False,
                identity.pid,
            )
        except pywintypes.error as exc:
            return _open_failure(identity, exc)
        try:
            current = _identity_from_handle(handle, identity.pid)
            if current.canonical_digest() != identity.canonical_digest():
                return _identity_changed(identity)
            if not _kernel32.TerminateProcess(int(handle), 1):
                error = ctypes.get_last_error()
                state = (
                    ProcessMemberResultState.ACCESS_DENIED
                    if error == winerror.ERROR_ACCESS_DENIED
                    else ProcessMemberResultState.FAILED
                )
                return ProcessMemberResult(
                    identity_digest=identity.canonical_digest(),
                    pid=identity.pid,
                    state=state,
                    platform_error_code=error,
                    message="TerminateProcess was rejected; no elevation was attempted",
                )
            wait = _kernel32.WaitForSingleObject(int(handle), int(timeout_seconds * 1000))
            if wait == _WAIT_OBJECT_0:
                verified = _kernel32.WaitForSingleObject(int(handle), 0)
                state = (
                    ProcessMemberResultState.EXITED
                    if verified == _WAIT_OBJECT_0
                    else ProcessMemberResultState.FAILED
                )
            elif wait == _WAIT_TIMEOUT:
                state = ProcessMemberResultState.STILL_RUNNING
            else:
                state = ProcessMemberResultState.FAILED
            return ProcessMemberResult(
                identity_digest=identity.canonical_digest(),
                pid=identity.pid,
                state=state,
                platform_error_code=None if wait in {_WAIT_OBJECT_0, _WAIT_TIMEOUT} else int(wait),
                message=(
                    "Original process identity exited after TerminateProcess"
                    if state is ProcessMemberResultState.EXITED
                    else (
                        "Termination was requested but verified exit was not observed"
                        if state is ProcessMemberResultState.STILL_RUNNING
                        else "Windows returned an unexpected process wait status"
                    )
                ),
            )
        except (OSError, pywintypes.error) as exc:
            return _platform_failure(identity, exc)
        finally:
            win32api.CloseHandle(handle)

    @staticmethod
    def _inspect_pid(
        pid: int,
        window_map: dict[int, tuple[int, int]],
        service_map: dict[int, tuple[str, ...]],
    ) -> ProcessObservation | None:
        try:
            handle = win32api.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        except pywintypes.error:
            return None
        try:
            identity = _identity_from_handle(handle, pid)
            process = psutil.Process(pid)
            try:
                memory = process.memory_info().rss
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                memory = 0
            try:
                cpu = max(process.cpu_percent(interval=None), 0.0)
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                cpu = 0.0
            window_count, visible_count = window_map.get(pid, (0, 0))
            return ProcessObservation(
                identity=identity,
                cpu_percent=cpu,
                memory_rss_bytes=memory,
                window_count=window_count,
                visible_window_count=visible_count,
                service_names=service_map.get(pid, ()),
                is_critical=_is_process_critical(handle),
                protection_level=_process_protection_level(handle),
            )
        except (OSError, pywintypes.error, psutil.Error, ValueError):
            return None
        finally:
            win32api.CloseHandle(handle)


def _identity_from_handle(handle: Any, pid: int) -> ProcessIdentity:
    """Read every identity field from one already-opened process object handle."""
    executable = _query_process_path(handle)
    owner_sid, username = _query_process_owner(handle)
    return ProcessIdentity(
        pid=pid,
        process_name=executable.name,
        create_time=_query_create_time(handle),
        executable_path=executable,
        owner_sid=owner_sid,
        username=username,
        session_id=_query_session_id(pid),
        parent_pid=_query_parent_pid(pid),
    )


def _query_process_path(handle: Any) -> Path:
    size = ctypes.c_ulong(32_768)
    buffer = ctypes.create_unicode_buffer(size.value)
    if not _kernel32.QueryFullProcessImageNameW(int(handle), 0, buffer, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    return Path(buffer.value)


def _query_create_time(handle: Any) -> datetime:
    created = _FileTime()
    exited = _FileTime()
    kernel = _FileTime()
    user = _FileTime()
    if not _kernel32.GetProcessTimes(
        int(handle),
        ctypes.byref(created),
        ctypes.byref(exited),
        ctypes.byref(kernel),
        ctypes.byref(user),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    ticks = (created.high << 32) | created.low
    return datetime(1601, 1, 1, tzinfo=UTC) + timedelta(microseconds=ticks // 10)


def _query_process_owner(handle: Any) -> tuple[str, str | None]:
    token = win32security.OpenProcessToken(handle, win32con.TOKEN_QUERY)
    try:
        sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        sid_text = win32security.ConvertSidToStringSid(sid)
        try:
            account, domain, _kind = win32security.LookupAccountSid(None, sid)
            username = f"{domain}\\{account}" if domain else account
        except pywintypes.error:
            username = None
        return sid_text, username
    finally:
        win32api.CloseHandle(token)


def _query_session_id(pid: int) -> int:
    value = ctypes.c_ulong()
    if not _kernel32.ProcessIdToSessionId(pid, ctypes.byref(value)):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(value.value)


def _query_parent_pid(pid: int) -> int | None:
    try:
        return psutil.Process(pid).ppid()
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        return None


def _is_process_critical(handle: Any) -> bool:
    value = ctypes.c_int()
    if not _kernel32.IsProcessCritical(int(handle), ctypes.byref(value)):
        raise ctypes.WinError(ctypes.get_last_error())
    return bool(value.value)


def _process_protection_level(handle: Any) -> int:
    value = _ProtectionLevelInformation()
    if not _kernel32.GetProcessInformation(
        int(handle),
        _PROCESS_PROTECTION_LEVEL_INFO,
        ctypes.byref(value),
        ctypes.sizeof(value),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(value.protection_level)


def _window_counts() -> dict[int, tuple[int, int]]:
    counts: dict[int, list[int]] = defaultdict(lambda: [0, 0])

    def collect(window: int, _extra: object) -> bool:
        try:
            _thread, pid = win32process.GetWindowThreadProcessId(window)
            counts[pid][0] += 1
            if win32gui.IsWindowVisible(window):
                counts[pid][1] += 1
        except pywintypes.error:
            pass
        return True

    win32gui.EnumWindows(collect, None)
    return {pid: (value[0], value[1]) for pid, value in counts.items()}


def _windows_for_pid(pid: int) -> tuple[int, ...]:
    windows: list[int] = []

    def collect(window: int, _extra: object) -> bool:
        try:
            _thread, owner_pid = win32process.GetWindowThreadProcessId(window)
            if owner_pid == pid:
                windows.append(window)
        except pywintypes.error:
            pass
        return True

    win32gui.EnumWindows(collect, None)
    return tuple(windows)


def _active_service_processes() -> dict[int, tuple[str, ...]]:
    services: dict[int, list[str]] = defaultdict(list)
    scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_ENUMERATE_SERVICE)
    try:
        rows = win32service.EnumServicesStatus(
            scm,
            win32service.SERVICE_WIN32,
            win32service.SERVICE_ACTIVE,
        )
        for service_name, _display_name, _status in rows:
            handle = None
            try:
                handle = win32service.OpenService(
                    scm,
                    service_name,
                    win32service.SERVICE_QUERY_STATUS,
                )
                status = cast(Mapping[str, object], win32service.QueryServiceStatusEx(handle))
                raw_pid = status.get("ProcessId", 0)
                pid = raw_pid if isinstance(raw_pid, int) else 0
                if pid > 0:
                    services[pid].append(service_name)
            except pywintypes.error:
                continue
            finally:
                if handle is not None:
                    win32service.CloseServiceHandle(handle)
    finally:
        win32service.CloseServiceHandle(scm)
    return {pid: tuple(sorted(names, key=str.casefold)) for pid, names in services.items()}


def _wait_for_process(
    handle: Any,
    timeout_seconds: float,
    cancellation: CancellationSignal,
) -> ProcessMemberResultState:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = _kernel32.WaitForSingleObject(int(handle), 0)
        if result == _WAIT_OBJECT_0:
            return ProcessMemberResultState.EXITED
        if result != _WAIT_TIMEOUT:
            return ProcessMemberResultState.FAILED
        if cancellation.cancellation_requested():
            return ProcessMemberResultState.CANCELLED_WAITING
        time.sleep(_WINDOW_POLL_SECONDS)
    return ProcessMemberResultState.STILL_RUNNING


def _open_failure(identity: ProcessIdentity, error: pywintypes.error) -> ProcessMemberResult:
    if int(error.winerror) in {winerror.ERROR_INVALID_PARAMETER, winerror.ERROR_NOT_FOUND}:
        state = ProcessMemberResultState.ALREADY_EXITED
        message = "The original process exited before execution; nothing was changed"
    elif int(error.winerror) == winerror.ERROR_ACCESS_DENIED:
        state = ProcessMemberResultState.ACCESS_DENIED
        message = "Ordinary-user access was denied; no elevation was attempted"
    else:
        state = ProcessMemberResultState.FAILED
        message = "The process could not be opened safely"
    return ProcessMemberResult(
        identity_digest=identity.canonical_digest(),
        pid=identity.pid,
        state=state,
        platform_error_code=int(error.winerror),
        message=message,
    )


def _identity_changed(identity: ProcessIdentity) -> ProcessMemberResult:
    return ProcessMemberResult(
        identity_digest=identity.canonical_digest(),
        pid=identity.pid,
        state=ProcessMemberResultState.IDENTITY_CHANGED,
        message="PID now belongs to a different identity; no action was performed",
    )


def _platform_failure(identity: ProcessIdentity, error: BaseException) -> ProcessMemberResult:
    code = getattr(error, "winerror", None)
    state = (
        ProcessMemberResultState.ACCESS_DENIED
        if code == winerror.ERROR_ACCESS_DENIED
        else ProcessMemberResultState.FAILED
    )
    return ProcessMemberResult(
        identity_digest=identity.canonical_digest(),
        pid=identity.pid,
        state=state,
        platform_error_code=int(code) if isinstance(code, int) and code >= 0 else None,
        message=(
            ProcessActionErrorCode.PROCESS_ACCESS_DENIED.value
            if state is ProcessMemberResultState.ACCESS_DENIED
            else ProcessActionErrorCode.PLATFORM_ERROR.value
        ),
    )


def protection_level_none() -> int:
    """Return the documented Win32 sentinel used for an unprotected process."""
    return _PROTECTION_LEVEL_NONE
