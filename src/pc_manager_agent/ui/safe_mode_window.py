"""Minimal recovery UI with no business-domain preparation or execution routes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QLabel,
    QMainWindow,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from pc_manager_agent.ui.diagnostic_bundle_dialog import export_diagnostic_bundle

if TYPE_CHECKING:
    from pc_manager_agent.app.runtime import ApplicationRuntime
    from pc_manager_agent.ui.system_tray import SystemTrayController


class SafeModeWindow(QMainWindow):
    """Show settings and sanitized audit metadata without constructing domain tabs."""

    def __init__(self, runtime: ApplicationRuntime) -> None:
        super().__init__()
        self._runtime = runtime
        self._tray: SystemTrayController | None = None
        self._quitting = False
        self.setWindowTitle("Windows PC Manager Agent — 安全模式")
        self.resize(820, 560)
        self._tabs = QTabWidget()
        self.setCentralWidget(self._tabs)
        self._build_status_tab()
        self._build_audit_tab()
        self._build_settings_tab()
        self.statusBar().showMessage(
            "安全模式：业务能力、模型供应商、语音、浏览器和 Broker 均已关闭"
        )

    def _build_status_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("应用已进入安全模式。此窗口不会创建计划、确认或执行系统操作。"))
        layout.addWidget(QLabel("请先查看本地日志、崩溃报告和审计元数据，再决定是否正常启动。"))
        layout.addStretch(1)
        self._tabs.addTab(page, "安全模式")

    def _build_audit_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        refresh = QPushButton("刷新审计元数据")
        refresh.clicked.connect(self._refresh_audit)
        self._audit = QTableWidget(0, 4)
        self._audit.setHorizontalHeaderLabels(("时间", "事件", "风险", "工具"))
        layout.addWidget(refresh)
        layout.addWidget(self._audit)
        self._tabs.addTab(page, "审计")

    def _build_settings_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("安全模式：已启用"))
        layout.addWidget(QLabel("模型供应商：disabled"))
        layout.addWidget(QLabel("所有可选业务功能：disabled"))
        layout.addWidget(QLabel(f"本地数据目录：{self._runtime.settings.data_directory}"))
        diagnostic_bundle = QPushButton("预览并导出已脱敏诊断包")
        diagnostic_bundle.clicked.connect(lambda: export_diagnostic_bundle(self, self._runtime))
        layout.addWidget(diagnostic_bundle)
        layout.addStretch(1)
        self._tabs.addTab(page, "设置")

    def _refresh_audit(self) -> None:
        """Read bounded structural audit fields; never display request or content bodies."""
        rows = self._runtime.audit.list_recent(100)
        self._audit.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = (
                row.occurred_at.isoformat(),
                row.event_type,
                row.risk_level or "-",
                row.tool_name or "-",
            )
            for column, value in enumerate(values):
                self._audit.setItem(row_index, column, QTableWidgetItem(value))

    def attach_tray(self, tray: SystemTrayController) -> None:
        """Let tray task navigation show this recovery window only."""
        self._tray = tray
        tray.task_center_requested.connect(self.show)

    def request_quit(self) -> bool:
        """Allow orderly close; safe mode owns no background business workers."""
        self._quitting = True
        self.close()
        return True

    def shutdown(self) -> bool:
        """Report immediate clean shutdown because no domain worker was constructed."""
        return True

    def closeEvent(self, event: QCloseEvent) -> None:
        """Retain normal tray behavior without adding any action channel."""
        if not self._quitting and self._tray and self._tray.is_available:
            event.ignore()
            self.hide()
            return
        event.accept()
