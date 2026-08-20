from __future__ import annotations

import pytest
from pytestqt.qtbot import QtBot
from tests.fixtures.software_analysis import build_software_services, msi_entry

from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.domain.software_uninstall_analysis import SoftwareTargetQuery
from pc_manager_agent.ui.software_analysis_dialog import SoftwareAnalysisDialog


@pytest.mark.gui
def test_dialog_confirms_understanding_then_stops_without_execution(
    qtbot: QtBot,
    runtime: ApplicationRuntime,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    services, repository, _platform = build_software_services(
        tmp_path / "software-audit.sqlite3", (msi_entry(),)
    )
    monkeypatch.setattr(runtime, "create_software_analysis_services", lambda: services)
    dialog = SoftwareAnalysisDialog(
        runtime,
        "卸载软件 Example App",
        query=SoftwareTargetQuery(display_name="Example App"),
    )
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: dialog._stage == "PLAN_CONFIRMATION", timeout=10_000)
    assert "卸载执行" in dialog.details.toPlainText()
    dialog.primary_button.click()
    qtbot.waitUntil(lambda: dialog._stage == "TARGET_ACKNOWLEDGEMENT", timeout=10_000)
    assert "executable_in_current_stage=false" in dialog.risk_label.text()
    assert "没有卸载按钮" in dialog.details.toPlainText()
    dialog.primary_button.click()
    assert dialog._stage == "STOPPED"
    assert "未卸载任何软件" in dialog.risk_label.text()
    assert not hasattr(dialog, "execute_button")
    repository.close()


@pytest.mark.gui
def test_dialog_surfaces_candidates_and_requires_new_plan(
    qtbot: QtBot,
    runtime: ApplicationRuntime,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    services, repository, _platform = build_software_services(
        tmp_path / "software-audit.sqlite3",
        (
            msi_entry(version="1.0"),
            msi_entry(
                version="2.0",
                product_code="{22345678-1234-1234-1234-1234567890AB}",
            ),
        ),
    )
    monkeypatch.setattr(runtime, "create_software_analysis_services", lambda: services)
    dialog = SoftwareAnalysisDialog(runtime, "卸载软件 Example App")
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: dialog._stage == "PLAN_CONFIRMATION", timeout=10_000)
    dialog.primary_button.click()
    qtbot.waitUntil(lambda: dialog._stage == "CANDIDATE_SELECTION", timeout=10_000)
    assert dialog.candidates.rowCount() == 2
    assert "旧确认立即失效" in dialog.details.toPlainText()
    repository.close()
