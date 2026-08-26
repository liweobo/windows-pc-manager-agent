from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot
from tests.fixtures.privileged_actions import build_privileged_test_stack, prepare_stop

from pc_manager_agent.domain.privileged_actions import (
    BrokerDecision,
    PrivilegedActionResult,
)
from pc_manager_agent.ui.privileged_action_dialog import PrivilegedActionDialog

pytestmark = pytest.mark.gui


def test_dialog_marks_mock_mode_and_never_claims_real_execution(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    stack = build_privileged_test_stack(tmp_path / "state.db")
    try:
        envelope = prepare_stop(stack)
        snapshot = stack.repository.snapshot(envelope.request)
        dialog = PrivilegedActionDialog(snapshot.plan, snapshot.preview, broker_mode="mock")
        qtbot.addWidget(dialog)
        dialog.show()
        assert "不会请求 UAC" in dialog._summary.text()
        assert dialog._mock.isVisible()
        with qtbot.waitSignal(dialog.authorization_requested):
            qtbot.mouseClick(dialog._authorize, Qt.MouseButton.LeftButton)
        dialog.mark_authorized()
        assert dialog._mock.isEnabled()
        dialog.show_result(
            PrivilegedActionResult(
                request_id=envelope.request.request_id,
                action_type=envelope.request.action_type,
                broker_decision=BrokerDecision.APPROVED_FOR_MOCK_EXECUTION,
                result_code="MOCK_VALIDATED",
                message="Mock only",
            )
        )
        assert "没有执行真实管理员系统操作" in dialog._status.text()
    finally:
        stack.close()


def test_dialog_hides_mock_action_when_mode_is_disabled(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    stack = build_privileged_test_stack(tmp_path / "state.db")
    try:
        envelope = prepare_stop(stack)
        snapshot = stack.repository.snapshot(envelope.request)
        dialog = PrivilegedActionDialog(
            snapshot.plan,
            snapshot.preview,
            broker_mode="disabled",
        )
        qtbot.addWidget(dialog)
        dialog.show()
        assert not dialog._mock.isVisible()
    finally:
        stack.close()
