"""Stage 4B startup GUI defaults, action visibility, and two-confirmation tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot
from tests.integration.test_startup_action_workflow import FakeStartupPlatform, _observation

from pc_manager_agent.app.runtime import ApplicationRuntime, StartupActionServices
from pc_manager_agent.audit.startup_actions import StartupActionAuditLogger
from pc_manager_agent.confirmation.startup_actions import StartupActionConfirmationService
from pc_manager_agent.domain.startup_actions import StartupActionType
from pc_manager_agent.orchestration.startup_actions import StartupActionService
from pc_manager_agent.orchestration.startup_target_resolver import StartupTargetResolver
from pc_manager_agent.persistence.startup_actions import StartupExecutionGuard
from pc_manager_agent.safety.startup_policy import StartupSafetyPolicy
from pc_manager_agent.safety.startup_preview import StartupPreviewEngine
from pc_manager_agent.safety.startup_validator import StartupActionSafetyValidator
from pc_manager_agent.tools.registry import ToolRegistry
from pc_manager_agent.tools.system_tools.startup_actions import (
    DisableStartupTool,
    RestoreStartupTool,
)
from pc_manager_agent.ui.startup_action_dialog import StartupActionDialog
from pc_manager_agent.ui.startup_management_tab import StartupManagementTab


def _replace_services(
    monkeypatch: pytest.MonkeyPatch,
    runtime: ApplicationRuntime,
    platform: FakeStartupPlatform,
) -> StartupActionServices:
    resolver = StartupTargetResolver(platform)
    policy = StartupSafetyPolicy(
        agent_root=Path("C:/Agent"),
        windows_directory=Path("C:/Windows"),
    )
    registry = ToolRegistry(write_guard=StartupExecutionGuard(runtime.startup_action_repository))
    registry.register(
        DisableStartupTool(
            platform,
            runtime.startup_backup_vault,
            runtime.startup_action_repository,
        )
    )
    registry.register(
        RestoreStartupTool(
            platform,
            runtime.startup_backup_vault,
            runtime.startup_action_repository,
        )
    )
    service = StartupActionService(
        platform,
        resolver,
        policy,
        StartupPreviewEngine(policy),
        StartupActionSafetyValidator(registry),
        StartupActionConfirmationService(),
        runtime.startup_backup_vault,
        runtime.startup_action_repository,
        registry,
        StartupActionAuditLogger(
            runtime.audit,
            app_version="test",
            git_commit=None,
        ),
    )
    services = StartupActionServices(
        registry=registry,
        platform=platform,
        resolver=resolver,
        service=service,
    )
    monkeypatch.setattr(runtime, "create_startup_action_services", lambda: services)
    return services


@pytest.mark.gui
def test_dialog_defaults_to_cancel_and_requires_both_confirmations(
    qtbot: QtBot,
    runtime: ApplicationRuntime,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    platform = FakeStartupPlatform(_observation(tmp_path))
    _replace_services(monkeypatch, runtime, platform)
    dialog = StartupActionDialog(
        runtime,
        StartupActionType.DISABLE,
        identity=platform.value.identity,
        display_name=platform.value.display_name,
    )
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: dialog._stage == "PLAN_CONFIRMATION", timeout=10_000)
    assert dialog._cancel.isDefault()
    assert platform.active

    dialog._primary.click()
    qtbot.waitUntil(lambda: dialog._stage == "RUNTIME_CONFIRMATION", timeout=10_000)
    assert "R2" in dialog._risk.text()
    assert platform.active

    dialog._primary.click()
    qtbot.waitUntil(lambda: dialog._stage == "COMPLETED", timeout=10_000)
    assert not platform.active
    assert "FULL" in dialog._risk.text()


@pytest.mark.gui
def test_startup_page_shows_safe_action_and_filters_rows(
    qtbot: QtBot,
    runtime: ApplicationRuntime,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    platform = FakeStartupPlatform(_observation(tmp_path))
    _replace_services(monkeypatch, runtime, platform)
    tab = StartupManagementTab(runtime)
    qtbot.addWidget(tab)
    qtbot.waitUntil(lambda: tab._worker is None and tab._active_table.rowCount() == 1)

    tab._active_table.selectRow(0)
    assert tab._disable.isEnabled()
    tab._search.setText("does-not-match")
    assert tab._active_table.rowCount() == 0
    tab._search.clear()
    assert tab._active_table.rowCount() == 1


@pytest.mark.gui
def test_unknown_publisher_has_no_disable_action(
    qtbot: QtBot,
    runtime: ApplicationRuntime,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    value = _observation(tmp_path).model_copy(update={"publisher": None})
    platform = FakeStartupPlatform(value)
    _replace_services(monkeypatch, runtime, platform)
    tab = StartupManagementTab(runtime)
    qtbot.addWidget(tab)
    qtbot.waitUntil(lambda: tab._worker is None and tab._active_table.rowCount() == 1)

    tab._active_table.selectRow(0)
    assert not tab._disable.isEnabled()
    assert platform.active
