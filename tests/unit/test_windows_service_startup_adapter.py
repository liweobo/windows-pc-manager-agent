"""Unit tests for exact ChangeServiceConfig argument and verification boundaries."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import pywintypes
import win32service

from pc_manager_agent.domain.service_actions import (
    ServiceObservation,
    ServiceStartupType,
    ServiceState,
)
from pc_manager_agent.domain.service_startup_actions import (
    ServiceStartupActionRequest,
    ServiceStartupActionType,
    ServiceStartupErrorCode,
)
from pc_manager_agent.domain.service_startup_errors import ServiceStartupActionError
from pc_manager_agent.platform_support.windows import service_startup as startup_module
from pc_manager_agent.platform_support.windows.service_startup import (
    WindowsServiceStartupPlatform,
)
from pc_manager_agent.safety.service_startup_policy import build_service_startup_impact
from pc_manager_agent.tools.manifest import CancellationToken
from tests.stage4c1_support import service_observation
from tests.stage4c2_support import configuration


def _request(
    tmp_path: Path,
) -> tuple[ServiceStartupActionRequest, ServiceObservation, ServiceObservation]:
    binary = tmp_path / "vendor.exe"
    binary.write_bytes(b"test")
    before = service_observation(binary, start_type=2)
    after = before.model_copy(
        update={"startup_configuration": configuration(ServiceStartupType.MANUAL)}
    )
    request = ServiceStartupActionRequest(
        action=ServiceStartupActionType.SET_MANUAL,
        identity=before.identity,
        expected_source_configuration=before.startup_configuration,
        target_configuration=after.startup_configuration,
        expected_runtime_state=before.state,
        expected_impact_digest=build_service_startup_impact(before).canonical_digest(),
        backup_id=uuid4(),
        backup_digest="a" * 64,
    )
    return request, before, after


def test_manual_adapter_changes_only_start_type_and_reads_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, before, after = _request(tmp_path)
    observations = iter((before, after))
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(win32service, "OpenSCManager", lambda *_args: "scm")
    monkeypatch.setattr(win32service, "OpenService", lambda *_args: "service")
    monkeypatch.setattr(win32service, "CloseServiceHandle", lambda _handle: None)
    monkeypatch.setattr(
        startup_module,
        "_observation_from_handle",
        lambda *_args: next(observations),
    )
    monkeypatch.setattr(
        win32service,
        "ChangeServiceConfig",
        lambda *args: calls.append(args),
    )
    result = WindowsServiceStartupPlatform().set_manual(request, CancellationToken())
    assert result.verified
    assert result.runtime_unchanged
    assert len(calls) == 1
    arguments = calls[0]
    assert arguments[1] == win32service.SERVICE_NO_CHANGE
    assert arguments[2] == win32service.SERVICE_DEMAND_START
    assert arguments[3] == win32service.SERVICE_NO_CHANGE
    assert arguments[4:] == (None, None, False, None, None, None, None)


def test_access_denied_never_requests_elevation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, _before, _after = _request(tmp_path)
    monkeypatch.setattr(win32service, "OpenSCManager", lambda *_args: "scm")
    monkeypatch.setattr(win32service, "CloseServiceHandle", lambda _handle: None)

    def denied(*_args: object) -> object:
        raise pywintypes.error(5, "OpenService", "Access is denied")

    monkeypatch.setattr(win32service, "OpenService", denied)
    with pytest.raises(ServiceStartupActionError) as captured:
        WindowsServiceStartupPlatform().set_manual(request, CancellationToken())
    assert captured.value.code is ServiceStartupErrorCode.ACCESS_DENIED


def test_cancelled_adapter_does_not_open_scm(tmp_path: Path) -> None:
    request, before, _after = _request(tmp_path)
    cancellation = CancellationToken()
    cancellation.cancel()
    result = WindowsServiceStartupPlatform().set_manual(request, cancellation)
    assert not result.change_dispatched
    assert result.before_runtime_state is before.state is ServiceState.RUNNING
    assert result.completed_at >= result.started_at >= datetime.min.replace(tzinfo=UTC)
