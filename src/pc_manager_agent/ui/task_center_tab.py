"""User-facing Stage 5E task history, plan review, and safe lifecycle controls."""

from __future__ import annotations

from collections.abc import Callable
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
from pc_manager_agent.app.tasks import FinalTaskServices
from pc_manager_agent.domain.computer_tasks import ComputerTask, ComputerTaskState
from pc_manager_agent.domain.task_workflows import DomainPreparationResult
from pc_manager_agent.orchestration.agent_runtime import PreparedAgentTask


class TaskCenterTab(QWidget):
    """Show durable task metadata without exposing domain execution authority."""

    status_message = Signal(str)
    handoff_requested = Signal(str)

    def __init__(
        self,
        services: FinalTaskServices | AgentServices,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._services = services
        self._goal_summaries: dict[UUID, str] = {}
        self._final = isinstance(services, FinalTaskServices)
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "任务中心负责计划、进度、等待和恢复协调。具体业务操作仍在原页面完成 "
                "Fresh 检查、Preview、对象级确认和验证；没有“全部确认”或全局 Undo。"
            )
        )
        actions = QHBoxLayout()
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh)
        self.review_button = QPushButton("审查并确认选中任务计划")
        self.review_button.clicked.connect(self.review_selected_plan)
        pause = QPushButton("暂停后续协调")
        pause.clicked.connect(self.pause_selected)
        resume = QPushButton("恢复前重新检查")
        resume.clicked.connect(self.resume_selected)
        recover = QPushButton("检查中断任务")
        recover.clicked.connect(self.recover_selected)
        cancel = QPushButton("停止选中任务的后续协调")
        cancel.clicked.connect(self.cancel_selected)
        for button in (refresh, self.review_button, pause, resume, recover, cancel):
            actions.addWidget(button)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ("更新时间", "状态", "目标摘要", "类型", "步骤", "已完成", "等待处理", "任务 ID")
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
                    "已标记为 INTERRUPTED。旧确认已失效，未自动恢复或重放。"
                )
            )
        self.refresh()

    def register_task(self, prepared: PreparedAgentTask) -> None:
        """Keep compatibility with Stage 5D metadata during migration."""
        summary = " ".join(prepared.graph.goal.split())
        self._goal_summaries[prepared.graph.task_id] = summary[:120]
        self.refresh()

    def register_computer_task(self, task: ComputerTask) -> None:
        """Refresh after a new durable Stage 5E task is created."""
        self._goal_summaries[task.task_id] = task.safe_goal_summary
        self.refresh()

    @Slot()
    def refresh(self) -> None:
        """Reload durable metadata; never query or mutate domain authorization."""
        if self._final:
            self._refresh_final()
        else:
            self._refresh_legacy()

    def _refresh_final(self) -> None:
        services = self._require_final()
        entries = services.orchestrator.list_recent()
        attention_counts: dict[UUID, int] = {}
        for item in services.orchestrator.pending_attention():
            attention_counts[item.task_id] = attention_counts.get(item.task_id, 0) + 1
        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            values = (
                entry.updated_at.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                entry.state.value,
                entry.safe_goal_summary,
                entry.task_kind.value,
                str(entry.progress.planned_steps),
                str(entry.progress.completed_steps),
                str(attention_counts.get(entry.task_id, 0)),
                str(entry.task_id),
            )
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))

    def _refresh_legacy(self) -> None:
        services = self._require_legacy()
        entries = services.runtime.coordinator.list_recent()
        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            values = (
                entry.updated_at.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                entry.status.value,
                self._goal_summaries.get(entry.task_id, "重启前任务（目标未持久化）"),
                ", ".join(entry.agent_roles),
                str(entry.node_count),
                str(entry.completed_count),
                "0",
                str(entry.task_id),
            )
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))

    @Slot()
    def review_selected_plan(self) -> None:
        """Review one task plan; approval covers coordination R0 nodes only."""
        if not self._final:
            self.status_message.emit("旧任务仅供查看，不能在这里补发确认。")
            return
        task_id = self._selected_task_id()
        if task_id is None:
            return
        services = self._require_final()
        task = next(
            (item for item in services.orchestrator.list_recent(500) if item.task_id == task_id),
            None,
        )
        if task is None or task.state is not ComputerTaskState.AWAITING_PLAN_CONFIRMATION:
            self.status_message.emit("该任务当前不等待计划确认。")
            return
        try:
            confirmation = services.orchestrator.request_plan_confirmation(task_id)
        except Exception as exc:
            self.status_message.emit(f"无法生成计划确认：{type(exc).__name__}")
            return
        answer = QMessageBox.question(
            self,
            "确认一个任务计划",
            f"任务：{task.safe_goal_summary}\n"
            f"计划步骤：{task.progress.planned_steps}\n"
            "本次只确认高层只读协调。它不会批准文件写入、终止进程、修改系统、"
            "卸载软件、网页交易或文档保存；这些仍需在各自页面单独确认。是否继续？",
        )
        approved = answer is QMessageBox.StandardButton.Yes
        try:
            changed = services.orchestrator.resolve_plan_confirmation(
                confirmation.confirmation_id,
                approved=approved,
            )
            if approved:
                services.orchestrator.start(task_id, confirmation.confirmation_id)
                services.orchestrator.advance(task_id)
                prepared = services.orchestrator.advance(task_id)
                if isinstance(prepared, DomainPreparationResult) and prepared.next_action_code:
                    self.handoff_requested.emit(prepared.next_action_code)
        except Exception as exc:
            self.status_message.emit(f"任务计划未启动：{type(exc).__name__}")
        else:
            self.status_message.emit(
                "任务只读协调计划已启动；具体操作仍等待原业务域审查。"
                if approved
                else f"任务已{changed.state.value}，未派发后续工作。"
            )
        self.refresh()

    @Slot()
    def pause_selected(self) -> None:
        """Pause only future Stage 5E scheduling."""
        if not self._final:
            self.status_message.emit("旧任务不支持此 Stage 5E 控制。")
            return
        self._apply_final_action(
            self._require_final().orchestrator.pause,
            "已暂停后续协调；外部程序未被终止。",
        )

    @Slot()
    def resume_selected(self) -> None:
        """Route resume through Fresh review instead of restoring old authority."""
        if not self._final:
            self.status_message.emit("旧任务不支持此 Stage 5E 控制。")
            return
        self._apply_final_action(
            self._require_final().orchestrator.resume,
            "恢复请求已转为 Fresh 人工检查；未重用旧确认。",
        )

    @Slot()
    def recover_selected(self) -> None:
        """Inspect an interrupted task without automatic replay."""
        if not self._final:
            self.status_message.emit("旧任务不支持此 Stage 5E 控制。")
            return
        self._apply_final_action(
            self._require_final().orchestrator.recover,
            "恢复检查已完成；未自动重放任何操作。",
        )

    @Slot()
    def cancel_selected(self) -> None:
        """Cancel future nodes after a warning; this is not Undo."""
        task_id = self._selected_task_id()
        if task_id is None:
            return
        answer = QMessageBox.question(
            self,
            "停止后续协调",
            "将停止此任务尚未开始的协调步骤。已经完成的业务操作不会撤销，"
            "外部卸载器也不会被终止。是否继续？",
        )
        if answer is not QMessageBox.StandardButton.Yes:
            return
        try:
            if self._final:
                self._require_final().orchestrator.cancel(task_id)
            else:
                self._require_legacy().runtime.cancel(task_id)
        except Exception as exc:
            self.status_message.emit(f"无法停止任务：{type(exc).__name__}")
        else:
            self.status_message.emit("已停止后续协调；没有执行 Undo。")
        self.refresh()

    def _apply_final_action(
        self,
        action: Callable[[UUID], object],
        success: str,
    ) -> None:
        task_id = self._selected_task_id()
        if task_id is None:
            return
        try:
            action(task_id)
        except Exception as exc:
            self.status_message.emit(f"任务状态未改变：{type(exc).__name__}")
        else:
            self.status_message.emit(success)
        self.refresh()

    def _selected_task_id(self) -> UUID | None:
        row = self.table.currentRow()
        if row < 0:
            self.status_message.emit("请先选择一个任务。")
            return None
        task_item = self.table.item(row, 7)
        if task_item is None:
            return None
        return UUID(task_item.text())

    def _require_final(self) -> FinalTaskServices:
        if not isinstance(self._services, FinalTaskServices):
            raise RuntimeError("Stage 5E services are unavailable")
        return self._services

    def _require_legacy(self) -> AgentServices:
        if not isinstance(self._services, AgentServices):
            raise RuntimeError("Stage 5D services are unavailable")
        return self._services
