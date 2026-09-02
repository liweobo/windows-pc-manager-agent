"""User-facing task metadata and cancellation; no confirmation or execution controls."""

from __future__ import annotations

from uuid import UUID

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pc_manager_agent.app.agents import AgentServices
from pc_manager_agent.orchestration.agent_runtime import PreparedAgentTask


class TaskCenterTab(QWidget):
    """Show content-free task state and stop only future coordination."""

    status_message = Signal(str)

    def __init__(self, services: AgentServices, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._services = services
        self._goal_summaries: dict[UUID, str] = {}
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "任务中心只显示协调状态。所有实际操作仍在原业务页面完成 Preview、确认和验证；"
                "这里不能批量批准。"
            )
        )
        actions = QHBoxLayout()
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh)
        cancel = QPushButton("停止选中任务的后续协调")
        cancel.clicked.connect(self.cancel_selected)
        actions.addWidget(refresh)
        actions.addWidget(cancel)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ("更新时间", "状态", "目标摘要", "参与角色", "节点", "已完成", "任务 ID")
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        if services.interrupted_task_count:
            layout.addWidget(
                QLabel(
                    f"启动时发现 {services.interrupted_task_count} 个未完成任务，"
                    "已标记为 INTERRUPTED，未自动恢复。"
                )
            )
        self.refresh()

    def register_task(self, prepared: PreparedAgentTask) -> None:
        """Remember a bounded visible summary for this process and refresh metadata."""
        summary = " ".join(prepared.graph.goal.split())
        self._goal_summaries[prepared.graph.task_id] = summary[:120]
        self.refresh()

    @Slot()
    def refresh(self) -> None:
        """Reload content-free durable state; never query domain authorization."""
        entries = self._services.runtime.coordinator.list_recent()
        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            values = (
                entry.updated_at.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                entry.status.value,
                self._goal_summaries.get(entry.task_id, "重启前任务（目标未持久化）"),
                ", ".join(entry.agent_roles),
                str(entry.node_count),
                str(entry.completed_count),
                str(entry.task_id),
            )
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))

    @Slot()
    def cancel_selected(self) -> None:
        """Cancel future nodes after an object-specific warning; this is not Undo."""
        row = self.table.currentRow()
        if row < 0:
            self.status_message.emit("请先选择一个任务。")
            return
        task_item = self.table.item(row, 6)
        if task_item is None:
            return
        task_id = UUID(task_item.text())
        answer = QMessageBox.question(
            self,
            "停止后续协调",
            "将停止此任务尚未开始的 Agent 委派和步骤。已经完成的业务操作不会撤销，"
            "外部卸载器也不会被终止。是否继续？",
        )
        if answer is not QMessageBox.StandardButton.Yes:
            return
        try:
            self._services.runtime.cancel(task_id)
        except Exception as exc:
            self.status_message.emit(f"无法停止任务：{type(exc).__name__}")
        else:
            self.status_message.emit("已停止后续协调；没有执行 Undo。")
        self.refresh()
