from __future__ import annotations

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.domain.file_analysis import (
    AnalysisType,
    FileAnalysisIntentDraft,
    FileAnalysisProgress,
)
from pc_manager_agent.ui.analysis_tab import FileAnalysisTab
from pc_manager_agent.ui.workers import FileAnalysisWorker


@pytest.mark.gui
def test_stage1_tab_plans_confirms_runs_filters_and_invalidates(
    qtbot: QtBot,
    runtime: ApplicationRuntime,
    tmp_path: Path,
) -> None:
    root = tmp_path / "Downloads"
    root.mkdir()
    large_file = root / "large-video.mp4"
    with large_file.open("wb") as handle:
        handle.seek(1_048_575)
        handle.write(b"x")
    runtime.authorized_paths.add_authorized(root, label="Downloads")
    tab = FileAnalysisTab(runtime)
    qtbot.addWidget(tab)
    tab.goal_input.setText("找出大文件")
    tab.minimum_size_mb.setValue(1)
    tab.inactive_checkbox.setChecked(False)

    tab.start_planning()
    assert tab.confirm_button.isEnabled()
    assert "R0" in tab.risk_label.text()
    assert "不会" in tab.plan_view.toPlainText() or "files_modified" in tab.plan_view.toPlainText()
    tab._approve_plan()
    assert tab.run_button.isEnabled()
    tab._run_analysis()
    qtbot.waitUntil(lambda: tab._analysis_worker is None, timeout=15_000)

    assert tab.results_table.rowCount() == 1
    assert tab.results_table.item(0, 0).text() == "large-video.mp4"
    assert "只读分析完成" in tab.progress_label.text()
    assert tab.export_button.isEnabled()

    tab.search_input.setText("not-present")
    tab._reset_and_load_page()
    assert tab.results_table.rowCount() == 0
    tab.search_input.clear()
    tab._reset_and_load_page()
    assert tab.results_table.rowCount() == 1

    tab.minimum_size_mb.setValue(2)
    assert not tab.run_button.isEnabled()
    assert "旧确认已失效" in tab.risk_label.text()


@pytest.mark.gui
def test_stage1_tab_progress_cancel_and_friendly_error(
    qtbot: QtBot,
    runtime: ApplicationRuntime,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "Downloads"
    root.mkdir()
    record = runtime.authorized_paths.add_authorized(root)
    services = runtime.create_file_analysis_services()
    plan = services.compiler.compile(
        "scan",
        FileAnalysisIntentDraft(
            authorized_root_ids=(record.path_id,),
            analyses=(AnalysisType.LARGE_FILES,),
        ),
    )
    tab = FileAnalysisTab(runtime)
    qtbot.addWidget(tab)
    tab._progress_changed(
        FileAnalysisProgress(
            analysis_session_id=plan.analysis_session_id,
            phase="scanning",
            files_scanned=25,
            directories_scanned=3,
            total_bytes=100,
            errors=1,
        )
    )
    assert "25" in tab.progress_label.text()
    assert "3" in tab.progress_label.text()

    worker = FileAnalysisWorker(runtime, plan)
    tab._analysis_worker = worker
    tab.cancel()
    assert worker.cancellation.is_cancelled

    warnings: list[str] = []
    monkeypatch.setattr(
        "pc_manager_agent.ui.analysis_tab.QMessageBox.warning",
        lambda _parent, _title, message: warnings.append(message),
    )
    tab._analysis_failed("simulated failure")
    assert warnings == ["只读分析失败：simulated failure"]
