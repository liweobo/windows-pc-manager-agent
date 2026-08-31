"""Qt navigation/confirmation tests with no real process, cleanup or uninstall execution."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QMessageBox
from tests.fixtures.optimization_actions import build_stage4e3_report

from pc_manager_agent.domain.optimization_actions import (
    OptimizationRecommendationReference,
    OptimizationSessionStatus,
)
from pc_manager_agent.domain.system_optimization import RecommendationType
from pc_manager_agent.ui.optimization_review_dialog import OptimizationDomainReviewDialog
from pc_manager_agent.ui.system_diagnostics_tab import SystemDiagnosticsTab
from pc_manager_agent.ui.system_optimization_tab import SystemOptimizationTab


def test_review_checkboxes_start_unchecked_and_navigation_does_not_execute(
    qtbot, runtime, monkeypatch
):
    import pc_manager_agent.ui.system_optimization_tab as module

    opened = []

    class FakeReviewDialog(QDialog):
        def __init__(self, _runtime, session_id, preparation, context, parent):
            super().__init__(parent)
            opened.append((session_id, preparation, context))

        def shutdown(self):
            pass

    monkeypatch.setattr(module, "OptimizationDomainReviewDialog", FakeReviewDialog)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_: QMessageBox.StandardButton.Ok)
    report = build_stage4e3_report(RecommendationType.REVIEW_STARTUP_ITEM)
    runtime.optimization_report_store.save(report)
    tab = SystemOptimizationTab(runtime)
    qtbot.addWidget(tab)
    tab._completed(report)
    selector = tab.recommendation_table.item(0, 0)
    assert selector.checkState() is Qt.CheckState.Unchecked
    assert tab.recommendation_table.horizontalHeaderItem(5).text() == "复查入口风险"
    assert tab.recommendation_table.item(0, 5).text() == "R0（仅复查）；操作风险由业务重新评估"
    tab.create_review_session()
    assert tab._review_session_id is None
    selector.setCheckState(Qt.CheckState.Checked)
    tab.create_review_session()
    assert tab._review_session_id is not None
    tab.open_next_review()
    qtbot.waitUntil(lambda: tab._review_dialog is not None)
    assert len(opened) == 1
    assert opened[0][1].requires_user_confirmation
    assert opened[0][1].causes_system_change is False
    tab.open_next_review()
    assert len(opened) == 1
    tab._review_dialog.accept()
    qtbot.waitUntil(lambda: tab._review_dialog is None)
    session = tab._reviews.sessions.get(tab._review_session_id)
    assert session.status is OptimizationSessionStatus.COMPLETED
    assert session.items[0].outcome is None
    assert "没有已验证的执行结果" in tab.session_view.toPlainText()
    tab.shutdown()


def test_cancelled_review_never_opens_next_dialog(qtbot, runtime, monkeypatch):
    monkeypatch.setattr(QMessageBox, "warning", lambda *_: QMessageBox.StandardButton.Ok)
    report = build_stage4e3_report(RecommendationType.REVIEW_HIGH_RESOURCE_PROCESS)
    runtime.optimization_report_store.save(report)
    tab = SystemOptimizationTab(runtime)
    qtbot.addWidget(tab)
    tab._completed(report)
    tab.recommendation_table.item(0, 0).setCheckState(Qt.CheckState.Checked)
    tab.create_review_session()
    tab.cancel_review_session()
    tab.open_next_review()
    assert tab._review_dialog is None
    assert "CANCELLED" in tab.session_view.toPlainText()
    tab.shutdown()


def test_service_recommendation_opens_only_fresh_readonly_plan(qtbot, runtime):
    from pc_manager_agent.domain.optimization_actions import OptimizationSessionCreateRequest

    report = build_stage4e3_report(RecommendationType.REVIEW_SERVICE)
    runtime.optimization_report_store.save(report)
    services = runtime.create_optimization_review_services()
    session = services.sessions.create(
        OptimizationSessionCreateRequest(
            source_report_id=report.report_id,
            recommendation_ids=(report.recommendations[0].recommendation_id,),
        )
    )
    prepared = services.sessions.prepare(
        session.session_id, report.recommendations[0].recommendation_id
    )
    context = services.handoffs.take(prepared.fresh_context_id, prepared.route_id)
    dialog = OptimizationDomainReviewDialog(runtime, session.session_id, prepared, context)
    qtbot.addWidget(dialog)
    diagnostic = dialog.findChild(SystemDiagnosticsTab)
    assert diagnostic is not None
    assert diagnostic._report is None
    assert diagnostic.confirm_button.isEnabled()
    assert not diagnostic.run_button.isEnabled()
    assert diagnostic.process_action_button.isHidden()
    assert diagnostic.software_uninstall_button.isHidden()
    assert diagnostic._plan.collectors[-1].value == "system.services"
    assert prepared.domain_plan_id is None
    assert dialog._poll.isActive()
    dialog.accept()
    assert not dialog._poll.isActive()
    assert dialog._shutdown_requested
    dialog.shutdown()


def test_stale_reference_cannot_be_navigated_even_with_known_uuid(runtime):
    report = build_stage4e3_report(RecommendationType.REVIEW_STARTUP_ITEM)
    runtime.optimization_report_store.save(report)
    services = runtime.create_optimization_review_services()
    services.invalidation.invalidate_by_snapshot(report.snapshot.snapshot_id)
    import pytest

    with pytest.raises(PermissionError):
        services.router.prepare(
            OptimizationRecommendationReference(
                source_report_id=report.report_id,
                recommendation_id=report.recommendations[0].recommendation_id,
            )
        )
