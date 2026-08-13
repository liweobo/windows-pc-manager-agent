"""Stage 4C1 service GUI defaults, protection, and two-confirmation tests."""

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot
from tests.stage4c1_support import FakeServicePlatform, service_observation

from pc_manager_agent.app.runtime import ApplicationRuntime, ServiceActionServices
from pc_manager_agent.audit.service_actions import ServiceActionAuditLogger
from pc_manager_agent.confirmation.service_actions import ServiceActionConfirmationService
from pc_manager_agent.domain.service_actions import ServiceActionType, ServiceState
from pc_manager_agent.orchestration.service_action_planner import ServiceActionPlanCompiler
from pc_manager_agent.orchestration.service_actions import ServiceActionService
from pc_manager_agent.orchestration.service_dependency_analyzer import ServiceDependencyAnalyzer
from pc_manager_agent.orchestration.service_target_resolver import ServiceTargetResolver
from pc_manager_agent.persistence.service_actions import ServiceExecutionGuard
from pc_manager_agent.safety.service_policy import ServiceSafetyPolicy
from pc_manager_agent.safety.service_preview import ServicePreviewEngine
from pc_manager_agent.safety.service_validator import ServiceActionSafetyValidator
from pc_manager_agent.tools.registry import ToolRegistry
from pc_manager_agent.tools.system_tools.service_actions import StartServiceTool, StopServiceTool
from pc_manager_agent.ui.service_action_dialog import ServiceActionDialog
from pc_manager_agent.ui.service_management_tab import ServiceManagementTab


def _replace_services(
    monkeypatch: pytest.MonkeyPatch,
    runtime: ApplicationRuntime,
    platform: FakeServicePlatform,
    tmp_path: Path,
) -> ServiceActionServices:
    repository = runtime.service_action_repository
    registry = ToolRegistry(write_guard=ServiceExecutionGuard(repository))

    def dispatched(request: object) -> None:
        from pc_manager_agent.domain.service_actions import (
            ServiceStepRequest,
            ServiceStepType,
            ServiceTransactionState,
        )

        typed = ServiceStepRequest.model_validate(request)
        repository.mark_dispatched(
            typed.transaction_id,
            (
                ServiceTransactionState.WAITING_RUNNING
                if typed.step is ServiceStepType.START
                else ServiceTransactionState.WAITING_STOPPED
            ),
        )

    registry.register(StartServiceTool(platform, dispatched))
    registry.register(StopServiceTool(platform, dispatched))
    policy = ServiceSafetyPolicy(
        current_username=r"DESKTOP\alice",
        agent_root=tmp_path / "agent",
        windows_directory=tmp_path / "Windows",
    )
    resolver = ServiceTargetResolver(platform)
    compiler = ServiceActionPlanCompiler()
    dependencies = ServiceDependencyAnalyzer()
    service = ServiceActionService(
        platform,
        resolver,
        compiler,
        policy,
        ServicePreviewEngine(policy, dependencies),
        ServiceActionSafetyValidator(registry),
        ServiceActionConfirmationService(),
        repository,
        registry,
        ServiceActionAuditLogger(runtime.audit, app_version="test", git_commit=None),
        timeout_seconds=5,
    )
    services = ServiceActionServices(
        registry=registry,
        platform=platform,
        resolver=resolver,
        compiler=compiler,
        service=service,
    )
    monkeypatch.setattr(runtime, "create_service_action_services", lambda: services)
    return services


@pytest.mark.gui
def test_service_page_shows_only_eligible_actions(
    qtbot: QtBot,
    runtime: ApplicationRuntime,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    binary = tmp_path / "demo.exe"
    binary.touch()
    platform = FakeServicePlatform(service_observation(binary))
    _replace_services(monkeypatch, runtime, platform, tmp_path)
    tab = ServiceManagementTab(runtime)
    qtbot.addWidget(tab)
    tab.refresh()
    qtbot.waitUntil(lambda: tab._worker is None and tab._table.rowCount() == 1)
    tab._table.selectRow(0)
    assert not tab._start.isEnabled()
    assert tab._stop.isEnabled()
    assert tab._restart.isEnabled()
    tab._search.setText("missing")
    assert tab._table.rowCount() == 0


@pytest.mark.gui
def test_protected_service_page_has_no_action_buttons(
    qtbot: QtBot,
    runtime: ApplicationRuntime,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    binary = tmp_path / "demo.exe"
    binary.touch()
    platform = FakeServicePlatform(
        service_observation(binary, service_name="WinDefend", display_name="Protected")
    )
    _replace_services(monkeypatch, runtime, platform, tmp_path)
    tab = ServiceManagementTab(runtime)
    qtbot.addWidget(tab)
    tab.refresh()
    qtbot.waitUntil(lambda: tab._worker is None and tab._table.rowCount() == 1)
    tab._table.selectRow(0)
    assert not tab._start.isEnabled()
    assert not tab._stop.isEnabled()
    assert not tab._restart.isEnabled()


@pytest.mark.gui
def test_service_dialog_defaults_cancel_and_requires_two_confirmations(
    qtbot: QtBot,
    runtime: ApplicationRuntime,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    binary = tmp_path / "demo.exe"
    binary.touch()
    platform = FakeServicePlatform(service_observation(binary))
    _replace_services(monkeypatch, runtime, platform, tmp_path)
    dialog = ServiceActionDialog(
        runtime,
        ServiceActionType.STOP,
        service_name="UserDemoSvc",
        display_name="User Demo Service",
    )
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: dialog._stage == "PLAN_CONFIRMATION", timeout=10_000)
    assert dialog._cancel.isDefault()
    assert platform.observation.state is ServiceState.RUNNING
    dialog._primary.click()
    qtbot.waitUntil(lambda: dialog._stage == "RUNTIME_CONFIRMATION", timeout=10_000)
    assert platform.observation.state is ServiceState.RUNNING
    dialog._primary.click()
    qtbot.waitUntil(lambda: dialog._stage == "COMPLETED", timeout=10_000)
    assert platform.observation.state is ServiceState.STOPPED
    assert "MANUAL" in dialog._risk.text()
