"""Safe Stage 5E home surface for versioned multi-domain task templates."""

from __future__ import annotations

from uuid import uuid4

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pc_manager_agent.app.tasks import FinalTaskServices
from pc_manager_agent.domain.computer_tasks import ComputerTask


class HomeTaskTab(QWidget):
    """Create allow-listed task plans; it never confirms or executes them."""

    task_created = Signal(object)
    status_message = Signal(str)

    def __init__(self, services: FinalTaskServices, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._services = services
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "从安全模板创建复杂任务。创建后请到“任务中心”逐任务审查计划；"
                "每个业务域仍独立执行 Fresh 检查、Preview 和确认。"
            )
        )
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ("模板", "版本", "任务类型", "自治级别", "业务域", "执行边界")
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        templates = services.templates.list()
        self.table.setRowCount(len(templates))
        for row, template in enumerate(templates):
            values = (
                template.title,
                template.version,
                template.kind.value,
                template.autonomy.value,
                ", ".join(domain.value for domain in template.domains),
                "仅分析" if template.analysis_only else "引导式；业务域独立确认",
            )
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
        layout.addWidget(self.table)
        create = QPushButton("创建选中任务计划")
        create.clicked.connect(self.create_selected)
        layout.addWidget(create)

    @Slot()
    def create_selected(self) -> None:
        """Create one durable unconfirmed task from the selected closed template."""
        row = self.table.currentRow()
        if row < 0:
            self.status_message.emit("请先选择一个任务模板。")
            return
        template = self._services.templates.list()[row]
        try:
            task = self._services.orchestrator.create_task(
                template.title,
                template.domains,
                root_request_id=uuid4(),
                kind=template.kind,
                autonomy=template.autonomy,
            )
        except Exception as exc:
            self.status_message.emit(f"任务计划未创建：{type(exc).__name__}")
            return
        self.task_created.emit(task)
        self.status_message.emit("任务计划已创建；请到任务中心逐任务审查并确认。")


def require_computer_task(value: object) -> ComputerTask:
    """Narrow an emitted Qt object before a typed UI handoff."""
    if not isinstance(value, ComputerTask):
        raise TypeError("Expected ComputerTask")
    return value
