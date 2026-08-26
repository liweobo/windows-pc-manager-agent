"""Developer-only presentation for Stage 4X1 Mock privileged requests."""

from __future__ import annotations

from html import escape

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pc_manager_agent.domain.privileged_actions import (
    PrivilegedActionPlan,
    PrivilegedActionPreview,
    PrivilegedActionResult,
)


class PrivilegedActionDialog(QDialog):
    """Show exact R3 Mock evidence without presenting it as a real system operation."""

    authorization_requested = Signal()
    mock_validation_requested = Signal()

    def __init__(
        self,
        plan: PrivilegedActionPlan,
        preview: PrivilegedActionPreview,
        *,
        broker_mode: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("需要管理员权限 — Stage 4X1 Mock")
        self.setModal(True)
        self._summary = QLabel(_preview_html(plan, preview))
        self._summary.setWordWrap(True)
        self._status = QLabel("尚未授权。此窗口不会弹出 UAC，也不会修改真实 Windows 状态。")
        self._status.setWordWrap(True)
        self._authorize = QPushButton("确认生成一次性管理员操作请求")
        self._authorize.clicked.connect(self.authorization_requested.emit)
        self._mock = QPushButton("使用 Mock Broker 验证")
        self._mock.setVisible(broker_mode == "mock")
        self._mock.setEnabled(False)
        self._mock.clicked.connect(self.mock_validation_requested.emit)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self._summary)
        layout.addWidget(self._status)
        layout.addWidget(self._authorize)
        layout.addWidget(self._mock)
        layout.addWidget(buttons)

    @Slot()
    def mark_authorized(self) -> None:
        """Show that a Mock-only request may now be built, not that Windows approved it."""
        self._authorize.setEnabled(False)
        self._mock.setEnabled(self._mock.isVisible())
        self._status.setText("两级确认已记录。请求仍然只适用于 Stage 4X1 Mock Broker。")

    def show_result(self, result: PrivilegedActionResult) -> None:
        """Render a truthful Broker result with a permanent no-real-action reminder."""
        self._mock.setEnabled(False)
        self._status.setText(
            f"结果：{escape(result.result_code)}。Stage 4X1 没有执行真实管理员系统操作。"
        )


def _preview_html(
    plan: PrivilegedActionPlan,
    preview: PrivilegedActionPreview,
) -> str:
    return (
        "<h3>管理员操作请求安全预览</h3>"
        "<table border='1' cellspacing='0' cellpadding='4'>"
        f"<tr><th>操作</th><td>{plan.action_type.value}</td></tr>"
        f"<tr><th>目标摘要</th><td>{plan.target_identity_hash[:12]}…</td></tr>"
        f"<tr><th>风险</th><td>{plan.risk_level.value}</td></tr>"
        "<tr><th>权限</th><td>需要 Windows Administrator</td></tr>"
        f"<tr><th>协议模式</th><td>{'Mock' if preview.mock_only else 'Blocked'}</td></tr>"
        "</table>"
        "<p><b>本阶段只验证短生命周期、单次使用请求；不会请求 UAC。</b></p>"
    )
