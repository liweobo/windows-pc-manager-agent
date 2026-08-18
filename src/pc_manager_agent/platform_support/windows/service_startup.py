"""Windows SCM adapter limited to one startup-type field and no runtime controls."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pywintypes
import win32service

from pc_manager_agent.domain.service_actions import ServiceStartupType
from pc_manager_agent.domain.service_startup_actions import (
    ServiceStartupActionRequest,
    ServiceStartupActionType,
    ServiceStartupErrorCode,
    ServiceStartupMutationResult,
    ServiceStartupPermissionEvidence,
)
from pc_manager_agent.domain.service_startup_errors import ServiceStartupActionError
from pc_manager_agent.platform_support.windows.service_control import (
    _observation_from_handle,
    _process_is_elevated,
)
from pc_manager_agent.safety.service_startup_policy import build_service_startup_impact
from pc_manager_agent.tools.manifest import CancellationToken

_QUERY_ACCESS = (
    win32service.SERVICE_QUERY_CONFIG
    | win32service.SERVICE_QUERY_STATUS
    | win32service.SERVICE_ENUMERATE_DEPENDENTS
)
_CHANGE_ACCESS = _QUERY_ACCESS | win32service.SERVICE_CHANGE_CONFIG


class WindowsServiceStartupPlatform:
    """Use ChangeServiceConfig only for non-delayed Automatic/Manual transitions."""

    def evaluate_permissions(self, service_name: str) -> ServiceStartupPermissionEvidence:
        """Probe exact SCM rights without requesting elevation or changing a DACL."""
        return ServiceStartupPermissionEvidence(
            can_query_configuration=_can_open_service(service_name, _QUERY_ACCESS),
            can_change_configuration=_can_open_service(service_name, _CHANGE_ACCESS),
            process_elevated=_process_is_elevated(),
        )

    def set_automatic(
        self,
        request: ServiceStartupActionRequest,
        cancellation: CancellationToken,
        on_dispatched: Callable[[], None] | None = None,
    ) -> ServiceStartupMutationResult:
        """Set only SERVICE_AUTO_START; delayed-auto mutation is deliberately absent."""
        if request.action is not ServiceStartupActionType.SET_AUTOMATIC:
            raise ValueError("Automatic adapter accepts only SET_AUTOMATIC")
        if request.target_configuration.startup_type is not ServiceStartupType.AUTOMATIC:
            raise ValueError("Automatic adapter requires an Automatic target")
        return self._change(request, win32service.SERVICE_AUTO_START, cancellation, on_dispatched)

    def set_manual(
        self,
        request: ServiceStartupActionRequest,
        cancellation: CancellationToken,
        on_dispatched: Callable[[], None] | None = None,
    ) -> ServiceStartupMutationResult:
        """Set only SERVICE_DEMAND_START and leave every other service field unchanged."""
        if request.action is not ServiceStartupActionType.SET_MANUAL:
            raise ValueError("Manual adapter accepts only SET_MANUAL")
        if request.target_configuration.startup_type is not ServiceStartupType.MANUAL:
            raise ValueError("Manual adapter requires a Manual target")
        return self._change(request, win32service.SERVICE_DEMAND_START, cancellation, on_dispatched)

    def restore(
        self,
        request: ServiceStartupActionRequest,
        cancellation: CancellationToken,
        on_dispatched: Callable[[], None] | None = None,
    ) -> ServiceStartupMutationResult:
        """Restore only a verified non-delayed Automatic or Manual target."""
        if request.action is not ServiceStartupActionType.RESTORE:
            raise ValueError("Restore adapter accepts only RESTORE")
        raw_target = {
            ServiceStartupType.AUTOMATIC: win32service.SERVICE_AUTO_START,
            ServiceStartupType.MANUAL: win32service.SERVICE_DEMAND_START,
        }.get(request.target_configuration.startup_type)
        if raw_target is None or request.target_configuration.delayed_auto_start:
            raise ValueError("Restore target is outside the Automatic/Manual boundary")
        return self._change(request, raw_target, cancellation, on_dispatched)

    def _change(
        self,
        request: ServiceStartupActionRequest,
        raw_target: int,
        cancellation: CancellationToken,
        on_dispatched: Callable[[], None] | None,
    ) -> ServiceStartupMutationResult:
        """Revalidate, mutate one SCM field, and read back configuration and runtime state."""
        started = datetime.now(UTC)
        if cancellation.is_cancelled:
            return _cancelled_result(request, started)
        scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
        handle: Any | None = None
        try:
            handle = win32service.OpenService(
                scm,
                request.identity.service_name,
                _CHANGE_ACCESS,
            )
            before = _observation_from_handle(scm, handle, request.identity.service_name)
            if before.identity.canonical_digest() != request.identity.canonical_digest():
                raise ServiceStartupActionError(
                    ServiceStartupErrorCode.IDENTITY_CHANGED,
                    "Stable service identity changed before configuration write",
                )
            if before.startup_configuration != request.expected_source_configuration:
                raise ServiceStartupActionError(
                    ServiceStartupErrorCode.CONFIGURATION_CHANGED,
                    "Startup configuration changed after confirmation",
                )
            if before.state is not request.expected_runtime_state:
                raise ServiceStartupActionError(
                    ServiceStartupErrorCode.RUNTIME_STATE_CHANGED,
                    "Runtime state changed after confirmation",
                )
            if build_service_startup_impact(before).canonical_digest() != (
                request.expected_impact_digest
            ):
                raise ServiceStartupActionError(
                    ServiceStartupErrorCode.DEPENDENCY_IMPACT_BLOCKED,
                    "Dependency or runtime impact changed after confirmation",
                )
            if cancellation.cancellation_requested():
                return _cancelled_result(request, started, before=before)
            win32service.ChangeServiceConfig(
                handle,
                win32service.SERVICE_NO_CHANGE,
                raw_target,
                win32service.SERVICE_NO_CHANGE,
                None,
                None,
                False,
                None,
                None,
                None,
                None,
            )
            if on_dispatched is not None:
                on_dispatched()
            after = _observation_from_handle(scm, handle, request.identity.service_name)
            identity_unchanged = (
                after.identity.canonical_digest() == request.identity.canonical_digest()
            )
            runtime_unchanged = after.state is before.state
            verified = (
                identity_unchanged
                and after.startup_configuration == request.target_configuration
                and runtime_unchanged
            )
            return ServiceStartupMutationResult(
                action=request.action,
                identity_digest=request.identity.canonical_digest(),
                before_configuration=before.startup_configuration,
                after_configuration=after.startup_configuration,
                before_runtime_state=before.state,
                after_runtime_state=after.state,
                change_dispatched=True,
                verified=verified,
                runtime_unchanged=runtime_unchanged,
                message=(
                    "Startup configuration was read back and runtime state stayed unchanged"
                    if verified
                    else "Post-write verification failed; inspect the freshly observed state"
                ),
                started_at=started,
                completed_at=datetime.now(UTC),
            )
        except pywintypes.error as exc:
            if exc.winerror == 5:
                raise ServiceStartupActionError(
                    ServiceStartupErrorCode.ACCESS_DENIED,
                    "SERVICE_CHANGE_CONFIG access was denied; elevation is not offered",
                ) from exc
            raise ServiceStartupActionError(
                ServiceStartupErrorCode.PLATFORM_ERROR,
                f"Windows SCM configuration call failed with code {exc.winerror}",
            ) from exc
        finally:
            if handle is not None:
                win32service.CloseServiceHandle(handle)
            win32service.CloseServiceHandle(scm)


def _can_open_service(service_name: str, desired_access: int) -> bool:
    """Return whether an exact service handle opens with the requested existing DACL."""
    scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
    handle: Any | None = None
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


def _cancelled_result(
    request: ServiceStartupActionRequest,
    started: datetime,
    *,
    before: Any | None = None,
) -> ServiceStartupMutationResult:
    """Build a truthful no-write result for cancellation before SCM dispatch."""
    configuration = (
        before.startup_configuration
        if before is not None
        else request.expected_source_configuration
    )
    state = before.state if before is not None else request.expected_runtime_state
    return ServiceStartupMutationResult(
        action=request.action,
        identity_digest=request.identity.canonical_digest(),
        before_configuration=configuration,
        after_configuration=configuration,
        before_runtime_state=state,
        after_runtime_state=state,
        change_dispatched=False,
        verified=False,
        runtime_unchanged=True,
        message="Cancelled before ChangeServiceConfig was dispatched",
        started_at=started,
        completed_at=datetime.now(UTC),
    )
