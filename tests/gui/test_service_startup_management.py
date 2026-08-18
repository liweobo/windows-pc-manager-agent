"""GUI tests for Stage 4C2 buttons, double confirmation, and runtime invariant."""

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot
from tests.stage4c1_support import FakeServicePlatform, service_observation
from tests.stage4c2_support import FakeServiceStartupPlatform

from pc_manager_agent.app.runtime import ApplicationRuntime, ServiceStartupActionServices
from pc_manager_agent.audit.service_startup_actions import ServiceStartupActionAuditLogger
from pc_manager_agent.confirmation.service_startup_actions import (
    ServiceStartupActionConfirmationService,
)
from pc_manager_agent.domain.service_actions import ServiceStartupType, ServiceState
from pc_manager_agent.domain.service_startup_actions import ServiceStartupActionType
from pc_manager_agent.orchestration.service_startup_actions import ServiceStartupActionService
from pc_manager_agent.orchestration.service_target_resolver import ServiceTargetResolver
from pc_manager_agent.persistence.service_startup_actions import ServiceStartupExecutionGuard
from pc_manager_agent.safety.service_policy import ServiceSafetyPolicy
from pc_manager_agent.safety.service_startup_policy import ServiceStartupSafetyPolicy
from pc_manager_agent.safety.service_startup_preview import ServiceStartupPreviewEngine
from pc_manager_agent.safety.service_startup_validator import ServiceStartupSafetyValidator
from pc_manager_agent.tools.registry import ToolRegistry
from pc_manager_agent.tools.system_tools.service_startup_actions import (
    RestoreServiceStartupTool,
    SetServiceAutomaticTool,
    SetServiceManualTool,
)
from pc_manager_agent.ui.service_startup_dialog import ServiceStartupActionDialog


def _replace_services(
    monkeypatch: pytest.MonkeyPatch,
    runtime: ApplicationRuntime,
    control: FakeServicePlatform,
    startup: FakeServiceStartupPlatform,
    tmp_path: Path,
) -> ServiceStartupActionServices:
    repository = runtime.service_startup_repository
    vault = runtime.service_startup_backup_vault
    registry = ToolRegistry(ServiceStartupExecutionGuard(repository))
    registry.register(SetServiceAutomaticTool(startup, vault))
    registry.register(SetServiceManualTool(startup, vault))
    registry.register(RestoreServiceStartupTool(startup, vault))
    base = ServiceSafetyPolicy(
        current_username=r"DESKTOP\alice",
        agent_root=tmp_path / "agent",
        windows_directory=tmp_path / "Windows",
    )
    policy = ServiceStartupSafetyPolicy(base)
    resolver = ServiceTargetResolver(control)
    service = ServiceStartupActionService(
        control,
        startup,
        resolver,
        policy,
        ServiceStartupPreviewEngine(policy),
        ServiceStartupSafetyValidator(),
        ServiceStartupActionConfirmationService(),
        vault,
        repository,
        registry,
        ServiceStartupActionAuditLogger(
            runtime.audit,
            app_version="test",
            git_commit=None,
        ),
    )
    services = ServiceStartupActionServices(
        registry=registry,
        platform=startup,
        resolver=resolver,
        service=service,
    )
    monkeypatch.setattr(runtime, "create_service_startup_action_services", lambda: services)
    return services


@pytest.mark.gui
def test_service_startup_dialog_requires_two_confirmations_and_keeps_state(
    qtbot: QtBot,
    runtime: ApplicationRuntime,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    binary = tmp_path / "vendor.exe"
    binary.write_bytes(b"test")
    control = FakeServicePlatform(service_observation(binary, start_type=2))
    startup = FakeServiceStartupPlatform(control)
    _replace_services(monkeypatch, runtime, control, startup, tmp_path)
    original_state = control.observation.state
    dialog = ServiceStartupActionDialog(
        runtime,
        ServiceStartupActionType.SET_MANUAL,
        display_name=control.observation.display_name,
        identity=control.observation.identity,
    )
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: dialog._stage == "PLAN_CONFIRMATION", timeout=10_000)
    assert control.observation.startup_configuration.startup_type is (ServiceStartupType.AUTOMATIC)
    assert control.observation.state is original_state
    dialog._primary.click()
    qtbot.waitUntil(lambda: dialog._stage == "RUNTIME_CONFIRMATION", timeout=10_000)
    assert control.observation.state is original_state
    dialog._primary.click()
    qtbot.waitUntil(lambda: dialog._stage == "COMPLETED", timeout=10_000)
    assert control.observation.startup_configuration.startup_type is ServiceStartupType.MANUAL
    assert control.observation.state is original_state is ServiceState.RUNNING
    assert "运行状态不变=是" in dialog._risk.text()
