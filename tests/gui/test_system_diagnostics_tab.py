from __future__ import annotations

import pytest
from pytestqt.qtbot import QtBot

from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.ui.system_diagnostics_tab import SystemDiagnosticsTab


@pytest.mark.gui
def test_system_dashboard_requires_confirmation_and_runs_in_background(
    qtbot: QtBot,
    runtime: ApplicationRuntime,
) -> None:
    runtime.settings = runtime.settings.model_copy(
        update={"diagnostic_sample_interval_seconds": 0.1}
    )
    tab = SystemDiagnosticsTab(runtime)
    qtbot.addWidget(tab)
    tab.start_planning("查看内存")
    assert tab.confirm_button.isEnabled()
    assert "R0" in tab.risk_label.text()
    assert not tab.run_button.isEnabled()
    tab._approve()
    assert tab.run_button.isEnabled()
    tab._run()
    qtbot.waitUntil(lambda: tab._worker is None, timeout=30_000)
    assert tab._report is not None
    assert tab.overview_table.rowCount() > 0
    assert "read-only" in tab._report.disclaimer
    tab.search_input.setText("definitely-no-such-process")
    assert all(tab.process_table.isRowHidden(row) for row in range(tab.process_table.rowCount()))
