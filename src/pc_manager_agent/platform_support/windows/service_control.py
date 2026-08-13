"""Windows SCM adapter for bounded exact service inspection and control."""

from __future__ import annotations

import ctypes
import os
import time
from collections.abc import Callable
from ctypes import wintypes
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pywintypes
import win32api
import win32con
import win32security
import win32service

from pc_manager_agent.domain.service_actions import (
    ServiceActionType,
    ServiceErrorCode,
    ServiceIdentity,
    ServiceObservation,
    ServicePermissionEvidence,
    ServiceRelation,
    ServiceState,
    ServiceStepResult,
    ServiceStepType,
    canonical_binary_fingerprint,
)
from pc_manager_agent.domain.service_errors import (
    ServiceActionError,
    ServiceConfigurationChangedError,
)
from pc_manager_agent.tools.manifest import CancellationToken

_QUERY_ACCESS = win32service.SERVICE_QUERY_CONFIG | win32service.SERVICE_QUERY_STATUS
_CONTROL_POLL_MIN_SECONDS = 0.1
_CONTROL_POLL_MAX_SECONDS = 1.0


class WindowsServiceControlPlatform:
    """Use pywin32 SCM functions without shell, WMI, cascade, or elevation."""

    def list_services(self, max_items: int = 5_000) -> tuple[ServiceObservation, ...]:
        """Return fresh bounded observations using query-only service handles."""
        if max_items < 1:
            raise ValueError("Service inventory limit must be positive")
        scm = win32service.OpenSCManager(
            None,
            None,
            win32service.SC_MANAGER_CONNECT | win32service.SC_MANAGER_ENUMERATE_SERVICE,
        )
        try:
            statuses = win32service.EnumServicesStatus(
                scm,
                win32service.SERVICE_WIN32,
                win32service.SERVICE_STATE_ALL,
            )
            observations: list[ServiceObservation] = []
            for service_name, _display_name, _status in statuses[:max_items]:
                try:
                    observation = self._inspect_with_scm(scm, service_name)
                except (OSError, pywintypes.error, ValueError):
                    continue
                if observation is not None:
                    observations.append(observation)
            return tuple(observations)
        finally:
            win32service.CloseServiceHandle(scm)

    def inspect(self, service_name: str) -> ServiceObservation | None:
        """Read exact current service evidence or return ``None`` when absent."""
        scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
        try:
            try:
                return self._inspect_with_scm(scm, service_name)
            except pywintypes.error as exc:
                if exc.winerror == 1060:  # ERROR_SERVICE_DOES_NOT_EXIST
                    return None
                raise
        finally:
            win32service.CloseServiceHandle(scm)

    def evaluate_permissions(
        self,
        service_name: str,
        action: ServiceActionType,
    ) -> ServicePermissionEvidence:
        """Probe exact SCM access without sending a control code or changing a DACL."""
        elevated = _process_is_elevated()
        query = _can_open_service(service_name, _QUERY_ACCESS)
        can_start = (
            _can_open_service(service_name, _QUERY_ACCESS | win32service.SERVICE_START)
            if action in {ServiceActionType.START, ServiceActionType.RESTART}
            else False
        )
        can_stop = (
            _can_open_service(service_name, _QUERY_ACCESS | win32service.SERVICE_STOP)
            if action in {ServiceActionType.STOP, ServiceActionType.RESTART}
            else False
        )
        can_dependents = (
            _can_open_service(
                service_name,
                _QUERY_ACCESS | win32service.SERVICE_ENUMERATE_DEPENDENTS,
            )
            if action in {ServiceActionType.STOP, ServiceActionType.RESTART}
            else True
        )
        return ServicePermissionEvidence(
            can_query=query,
            can_start=can_start,
            can_stop=can_stop,
            can_enumerate_dependents=can_dependents,
            process_elevated=elevated,
        )

    def start(
        self,
        identity: ServiceIdentity,
        expected_state: ServiceState,
        timeout_seconds: float,
        cancellation: CancellationToken,
        on_dispatched: Callable[[], None] | None = None,
    ) -> ServiceStepResult:
        """Revalidate, start, and wait for RUNNING with a bounded checkpoint loop."""
        return self._control(
            identity,
            ServiceStepType.START,
            expected_state,
            ServiceState.RUNNING,
            timeout_seconds,
            cancellation,
            on_dispatched,
        )

    def stop(
        self,
        identity: ServiceIdentity,
        expected_state: ServiceState,
        timeout_seconds: float,
        cancellation: CancellationToken,
        on_dispatched: Callable[[], None] | None = None,
    ) -> ServiceStepResult:
        """Revalidate, stop, and wait for STOPPED without cascading dependents."""
        return self._control(
            identity,
            ServiceStepType.STOP,
            expected_state,
            ServiceState.STOPPED,
            timeout_seconds,
            cancellation,
            on_dispatched,
        )

    def _inspect_with_scm(self, scm: Any, service_name: str) -> ServiceObservation | None:
        handle = win32service.OpenService(
            scm,
            service_name,
            _QUERY_ACCESS | win32service.SERVICE_ENUMERATE_DEPENDENTS,
        )
        try:
            return _observation_from_handle(scm, handle, service_name)
        finally:
            win32service.CloseServiceHandle(handle)

    def _control(
        self,
        identity: ServiceIdentity,
        step: ServiceStepType,
        expected_state: ServiceState,
        target_state: ServiceState,
        timeout_seconds: float,
        cancellation: CancellationToken,
        on_dispatched: Callable[[], None] | None,
    ) -> ServiceStepResult:
        started = datetime.now(UTC)
        if cancellation.is_cancelled:
            return ServiceStepResult(
                step=step,
                identity_digest=identity.canonical_digest(),
                before_state=ServiceState.UNKNOWN,
                after_state=ServiceState.UNKNOWN,
                control_dispatched=False,
                verified=False,
                message="Cancelled before the SCM control request was dispatched",
                started_at=started,
                completed_at=datetime.now(UTC),
            )
        desired = _QUERY_ACCESS | (
            win32service.SERVICE_START
            if step is ServiceStepType.START
            else win32service.SERVICE_STOP | win32service.SERVICE_ENUMERATE_DEPENDENTS
        )
        scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
        handle = None
        try:
            handle = win32service.OpenService(scm, identity.service_name, desired)
            current = _observation_from_handle(scm, handle, identity.service_name)
            if current.identity.canonical_digest() != identity.canonical_digest():
                raise ServiceConfigurationChangedError()
            before = current.state
            if before is not expected_state:
                raise ServiceActionError(
                    ServiceErrorCode.SERVICE_STATE_CHANGED,
                    "Service state changed after confirmation",
                )
            if before is target_state:
                return ServiceStepResult(
                    step=step,
                    identity_digest=identity.canonical_digest(),
                    before_state=before,
                    after_state=before,
                    control_dispatched=False,
                    verified=True,
                    message=f"Service was already {target_state.value}; no control was sent",
                    started_at=started,
                    completed_at=datetime.now(UTC),
                )
            if before.is_pending:
                raise ServiceActionError(
                    ServiceErrorCode.PENDING_STATE,
                    "Service entered a pending state before execution",
                )
            if cancellation.cancellation_requested():
                return ServiceStepResult(
                    step=step,
                    identity_digest=identity.canonical_digest(),
                    before_state=before,
                    after_state=before,
                    control_dispatched=False,
                    verified=False,
                    message="Cancelled after validation and before SCM dispatch",
                    started_at=started,
                    completed_at=datetime.now(UTC),
                )
            if step is ServiceStepType.START:
                win32service.StartService(handle, None)
            else:
                if any(item.state is not ServiceState.STOPPED for item in current.dependents):
                    raise ServiceActionError(
                        ServiceErrorCode.DEPENDENT_SERVICE_RUNNING,
                        "A dependent service became active; cascade stop is prohibited",
                    )
                win32service.ControlService(handle, win32service.SERVICE_CONTROL_STOP)
            if on_dispatched is not None:
                on_dispatched()
            after = _wait_for_state(handle, target_state, timeout_seconds, cancellation)
            return ServiceStepResult(
                step=step,
                identity_digest=identity.canonical_digest(),
                before_state=before,
                after_state=after,
                control_dispatched=True,
                verified=after is target_state,
                cancellation_requested_after_dispatch=cancellation.is_cancelled,
                message=(
                    f"Service reached {target_state.value}"
                    if after is target_state
                    else f"Service did not reach {target_state.value} before the bounded wait ended"
                ),
                started_at=started,
                completed_at=datetime.now(UTC),
            )
        finally:
            if handle is not None:
                win32service.CloseServiceHandle(handle)
            win32service.CloseServiceHandle(scm)


def current_windows_username() -> str:
    """Return the current extended logon name used for account comparison."""
    return str(win32api.GetUserNameEx(2))


def _observation_from_handle(scm: Any, handle: Any, service_name: str) -> ServiceObservation:
    config = win32service.QueryServiceConfig(handle)
    status = cast(dict[str, int], win32service.QueryServiceStatusEx(handle))
    raw_binary = str(config[3])
    display_name = str(config[8])
    dependency_names = tuple(str(item) for item in config[6] if item)
    dependencies = tuple(
        _query_relation(scm, item) for item in dependency_names if not item.startswith("+")
    )
    dependent_rows = win32service.EnumDependentServices(handle, win32service.SERVICE_STATE_ALL)
    dependents = tuple(
        ServiceRelation(
            service_name=str(name),
            display_name=str(display),
            state=_state(int(dependent_status[1])),
        )
        for name, display, dependent_status in dependent_rows
    )
    description: str | None = None
    try:
        raw_description = win32service.QueryServiceConfig2(
            handle,
            win32service.SERVICE_CONFIG_DESCRIPTION,
        )
        description = str(raw_description).strip() or None
    except pywintypes.error:
        description = None
    binary = _extract_executable_path(raw_binary)
    return ServiceObservation(
        identity=ServiceIdentity(
            service_name=service_name,
            display_name=display_name,
            service_type=int(config[0]),
            binary_path_fingerprint=canonical_binary_fingerprint(raw_binary),
            service_account=str(config[7] or "LocalSystem"),
            start_type=int(config[1]),
        ),
        state=_state(int(status["CurrentState"])),
        controls_accepted=max(0, int(status["ControlsAccepted"])),
        process_id=max(0, int(status["ProcessId"])),
        binary_path=binary,
        publisher=_publisher(binary) if binary is not None else None,
        publisher_verified=_authenticode_signature_valid(binary) if binary is not None else False,
        description=description,
        dependencies=dependencies,
        dependents=dependents,
    )


def _query_relation(scm: Any, service_name: str) -> ServiceRelation:
    handle = win32service.OpenService(scm, service_name, win32service.SERVICE_QUERY_STATUS)
    try:
        status = cast(dict[str, int], win32service.QueryServiceStatusEx(handle))
        display_name = str(win32service.GetServiceDisplayName(scm, service_name))
        return ServiceRelation(
            service_name=service_name,
            display_name=display_name,
            state=_state(int(status["CurrentState"])),
        )
    finally:
        win32service.CloseServiceHandle(handle)


def _wait_for_state(
    handle: Any,
    expected: ServiceState,
    timeout_seconds: float,
    cancellation: CancellationToken,
) -> ServiceState:
    deadline = time.monotonic() + timeout_seconds
    last_checkpoint: int | None = None
    checkpoint_deadline = deadline
    while True:
        status = cast(dict[str, int], win32service.QueryServiceStatusEx(handle))
        current = _state(int(status["CurrentState"]))
        if current is expected:
            return current
        if current not in {ServiceState.START_PENDING, ServiceState.STOP_PENDING}:
            return current
        checkpoint = max(0, int(status["CheckPoint"]))
        wait_hint = max(0, int(status["WaitHint"])) / 1_000
        now = time.monotonic()
        if checkpoint != last_checkpoint:
            last_checkpoint = checkpoint
            checkpoint_deadline = min(deadline, now + max(wait_hint, _CONTROL_POLL_MAX_SECONDS))
        if now >= deadline or now >= checkpoint_deadline:
            return current
        # Cancellation after dispatch prevents later restart steps but does not abandon
        # bounded verification of the already-issued state transition.
        delay = min(
            max(wait_hint / 10, _CONTROL_POLL_MIN_SECONDS),
            _CONTROL_POLL_MAX_SECONDS,
            deadline - now,
        )
        time.sleep(max(delay, 0))


def _can_open_service(service_name: str, desired_access: int) -> bool:
    scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
    handle = None
    try:
        try:
            handle = win32service.OpenService(scm, service_name, desired_access)
        except pywintypes.error as exc:
            if exc.winerror in {5, 1060}:
                return False
            raise
        return True
    finally:
        if handle is not None:
            win32service.CloseServiceHandle(handle)
        win32service.CloseServiceHandle(scm)


def _process_is_elevated() -> bool:
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        return bool(win32security.GetTokenInformation(token, win32security.TokenElevation))
    finally:
        win32api.CloseHandle(token)


def _state(value: int) -> ServiceState:
    return {
        win32service.SERVICE_STOPPED: ServiceState.STOPPED,
        win32service.SERVICE_START_PENDING: ServiceState.START_PENDING,
        win32service.SERVICE_STOP_PENDING: ServiceState.STOP_PENDING,
        win32service.SERVICE_RUNNING: ServiceState.RUNNING,
        win32service.SERVICE_CONTINUE_PENDING: ServiceState.CONTINUE_PENDING,
        win32service.SERVICE_PAUSE_PENDING: ServiceState.PAUSE_PENDING,
        win32service.SERVICE_PAUSED: ServiceState.PAUSED,
    }.get(value, ServiceState.UNKNOWN)


def _extract_executable_path(raw: str) -> Path | None:
    value = os.path.expandvars(raw.strip())
    if not value:
        return None
    if value.startswith('"'):
        end = value.find('"', 1)
        executable = value[1:end] if end > 1 else ""
    else:
        lower = value.casefold()
        end = lower.find(".exe")
        executable = value[: end + 4] if end >= 0 else value.split(maxsplit=1)[0]
    path = Path(executable.strip())
    return path.resolve(strict=False) if path.is_absolute() else None


def _publisher(path: Path) -> str | None:
    """Read untrusted CompanyName metadata; policy treats absence as a hard block."""
    try:
        get_version = cast(Any, win32api.GetFileVersionInfo)
        translations = get_version(str(path), r"\VarFileInfo\Translation")
        if not translations:
            return None
        language, codepage = translations[0]
        value = get_version(
            str(path),
            rf"\StringFileInfo\{language:04x}{codepage:04x}\CompanyName",
        )
    except (OSError, TypeError, ValueError, pywintypes.error):
        return None
    text = str(value).strip()
    return text or None


class _GUID(ctypes.Structure):
    _fields_ = (
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    )


class _WinTrustFileInfo(ctypes.Structure):
    _fields_ = (
        ("cbStruct", wintypes.DWORD),
        ("pcwszFilePath", wintypes.LPCWSTR),
        ("hFile", wintypes.HANDLE),
        ("pgKnownSubject", ctypes.POINTER(_GUID)),
    )


class _WinTrustUnion(ctypes.Union):
    _fields_ = (("pFile", ctypes.POINTER(_WinTrustFileInfo)),)


class _WinTrustData(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = (
        ("cbStruct", wintypes.DWORD),
        ("pPolicyCallbackData", wintypes.LPVOID),
        ("pSIPClientData", wintypes.LPVOID),
        ("dwUIChoice", wintypes.DWORD),
        ("fdwRevocationChecks", wintypes.DWORD),
        ("dwUnionChoice", wintypes.DWORD),
        ("union", _WinTrustUnion),
        ("dwStateAction", wintypes.DWORD),
        ("hWVTStateData", wintypes.HANDLE),
        ("pwszURLReference", wintypes.LPCWSTR),
        ("dwProvFlags", wintypes.DWORD),
        ("dwUIContext", wintypes.DWORD),
        ("pSignatureSettings", wintypes.LPVOID),
    )


def _authenticode_signature_valid(path: Path) -> bool:
    """Validate the embedded/cached Authenticode chain without network retrieval."""
    if not path.exists() or not path.is_file():
        return False
    action = _GUID(
        0x00AAC56B,
        0xCD44,
        0x11D0,
        (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE),
    )
    file_info = _WinTrustFileInfo(
        ctypes.sizeof(_WinTrustFileInfo),
        str(path),
        None,
        None,
    )
    data = _WinTrustData()
    data.cbStruct = ctypes.sizeof(_WinTrustData)
    data.dwUIChoice = 2  # WTD_UI_NONE
    data.fdwRevocationChecks = 0  # WTD_REVOKE_NONE
    data.dwUnionChoice = 1  # WTD_CHOICE_FILE
    data.pFile = ctypes.pointer(file_info)
    data.dwStateAction = 0  # WTD_STATEACTION_IGNORE
    data.dwProvFlags = 0x1000  # WTD_CACHE_ONLY_URL_RETRIEVAL
    win_verify_trust = ctypes.WinDLL("wintrust", use_last_error=True).WinVerifyTrust
    win_verify_trust.argtypes = (
        wintypes.HWND,
        ctypes.POINTER(_GUID),
        ctypes.POINTER(_WinTrustData),
    )
    win_verify_trust.restype = wintypes.LONG
    return int(win_verify_trust(wintypes.HWND(-1), ctypes.byref(action), ctypes.byref(data))) == 0
