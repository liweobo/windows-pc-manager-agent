from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pytestqt.qtbot import QtBot
from tests.stage4a_support import FakeProcessPlatform, process_observation

from pc_manager_agent.app.runtime import ApplicationRuntime, ProcessActionServices
from pc_manager_agent.confirmation.process_actions import ProcessActionConfirmationService
from pc_manager_agent.domain.process_actions import (
    ProcessActionType,
    ProcessTargetQuery,
    ProcessTargetQueryType,
)
from pc_manager_agent.orchestration.process_action_planner import ProcessActionPlanCompiler
from pc_manager_agent.orchestration.process_actions import ProcessActionService
from pc_manager_agent.orchestration.process_target_resolver import ProcessTargetResolver
from pc_manager_agent.persistence.process_actions import ProcessExecutionGuard
from pc_manager_agent.safety.process_policy import ProcessSafetyPolicy
from pc_manager_agent.safety.process_preview import ProcessPreviewEngine
from pc_manager_agent.safety.process_validator import ProcessActionSafetyValidator
from pc_manager_agent.tools.registry import ToolRegistry
from pc_manager_agent.tools.system_tools.process_actions import (
    ForceTerminateProcessTool,
    RequestProcessExitTool,
)
from pc_manager_agent.ui.process_action_dialog import ProcessActionDialog


def _replace_services(
    monkeypatch: pytest.MonkeyPatch,
    runtime: ApplicationRuntime,
    platform: FakeProcessPlatform,
) -> None:
    resolver = ProcessTargetResolver(platform)
    registry = ToolRegistry(write_guard=ProcessExecutionGuard(runtime.process_action_repository))
    registry.register(RequestProcessExitTool(platform))
    registry.register(ForceTerminateProcessTool(platform))
    service = ProcessActionService(
        ProcessActionPlanCompiler(resolver),
        resolver,
        ProcessPreviewEngine(
            ProcessSafetyPolicy(
                current_owner_sid="S-1-5-21-1000",
                current_session_id=1,
                windows_directory=Path("C:/Windows"),
            )
        ),
        ProcessActionSafetyValidator(registry),
        ProcessActionConfirmationService(),
        runtime.process_action_repository,
        registry,
        SimpleNamespace(
            previewed=lambda *_args: None,
            confirmation_resolved=lambda *_args: None,
            started=lambda *_args: None,
            completed=lambda *_args: None,
            failed=lambda *_args, **_kwargs: None,
        ),
        graceful_timeout_seconds=1,
        force_timeout_seconds=1,
    )
    services = ProcessActionServices(
        registry=registry,
        platform=platform,
        resolver=resolver,
        compiler=ProcessActionPlanCompiler(resolver),
        service=service,
    )
    monkeypatch.setattr(runtime, "create_process_action_services", lambda: services)


@pytest.mark.gui
def test_dialog_defaults_to_cancel_and_requires_both_confirmations(
    qtbot: QtBot,
    runtime: ApplicationRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    platform = FakeProcessPlatform((process_observation(),))
    _replace_services(monkeypatch, runtime, platform)
    dialog = ProcessActionDialog(
        runtime,
        "close selected demo",
        query=ProcessTargetQuery(
            query_type=ProcessTargetQueryType.SELECTED_PROCESS,
            pid=4_001,
            include_application_group=False,
        ),
    )
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: dialog._stage == "PLAN_CONFIRMATION", timeout=10_000)
    assert dialog._cancel_button.isDefault()
    assert platform.graceful_calls == []
    dialog._primary_button.click()
    qtbot.waitUntil(lambda: dialog._stage == "RUNTIME_CONFIRMATION", timeout=10_000)
    assert "R2" in dialog._risk.text()
    assert platform.graceful_calls == []
    dialog._primary_button.click()
    qtbot.waitUntil(lambda: dialog._stage == "COMPLETED", timeout=10_000)
    assert platform.graceful_calls == [4_001]
    assert "回滚等级 NONE" in dialog._risk.text()


@pytest.mark.gui
def test_blocked_system_process_never_shows_executable_button(
    qtbot: QtBot,
    runtime: ApplicationRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    platform = FakeProcessPlatform((process_observation(name="lsass.exe", critical=True),))
    _replace_services(monkeypatch, runtime, platform)
    dialog = ProcessActionDialog(runtime, "关闭 lsass")
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: dialog._stage == "BLOCKED", timeout=10_000)
    assert not dialog._primary_button.isEnabled()
    assert not dialog._force_button.isEnabled()
    assert platform.graceful_calls == []
    assert platform.force_calls == []


@pytest.mark.gui
def test_background_process_offers_separate_force_preview_not_direct_force(
    qtbot: QtBot,
    runtime: ApplicationRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    platform = FakeProcessPlatform((process_observation(window_count=0),))
    _replace_services(monkeypatch, runtime, platform)
    dialog = ProcessActionDialog(runtime, "关闭 demo")
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: dialog._stage == "BLOCKED", timeout=10_000)
    assert dialog._force_button.isEnabled()
    assert platform.force_calls == []
    dialog._force_button.click()
    qtbot.waitUntil(lambda: dialog._stage == "PLAN_CONFIRMATION", timeout=10_000)
    assert dialog._plan is not None
    assert dialog._plan.action is ProcessActionType.FORCE_TERMINATE
    assert "R2_HIGH_IMPACT" in dialog._risk.text()
    assert platform.force_calls == []
