"""Transparent user controls for finite persistent preferences."""

from __future__ import annotations

from uuid import UUID

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pc_manager_agent.domain.memory import MemoryKey, MemoryScope
from pc_manager_agent.memory.service import (
    MemoryService,
    explicit_setting_candidate,
)


class MemoryTab(QWidget):
    """View, save, pause, and physically delete user Memory values."""

    status_message = Signal(str)

    def __init__(self, service: MemoryService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = service
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel("记忆只用于偏好和引用提示，不会降低风险、跳过确认、扩大路径或授予管理员权限。")
        )
        self.enabled = QCheckBox("启用持久偏好记忆")
        self.enabled.setChecked(service.enabled)
        self.enabled.toggled.connect(self._toggle_enabled)
        layout.addWidget(self.enabled)

        edit_row = QHBoxLayout()
        self.key = QComboBox()
        for key in MemoryKey:
            self.key.addItem(key.value, key.value)
        self.value = QLineEdit()
        self.value.setMaxLength(500)
        self.value.setPlaceholderText("输入有限偏好值；API Key、密码和任意指令会被拒绝")
        save = QPushButton("审查并保存偏好")
        save.clicked.connect(self._save)
        edit_row.addWidget(self.key)
        edit_row.addWidget(self.value, 1)
        edit_row.addWidget(save)
        layout.addLayout(edit_row)

        action_row = QHBoxLayout()
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh)
        delete = QPushButton("删除选中项")
        delete.clicked.connect(self._delete)
        self.scope = QComboBox()
        for scope in MemoryScope:
            self.scope.addItem(scope.value, scope.value)
        clear_scope = QPushButton("清空所选范围")
        clear_scope.clicked.connect(self._clear_scope)
        clear_all = QPushButton("清空全部记忆")
        clear_all.clicked.connect(self._clear_all)
        for widget in (refresh, delete, self.scope, clear_scope, clear_all):
            action_row.addWidget(widget)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ("范围", "类别", "键", "值", "来源/原因", "版本", "更新时间", "Memory ID")
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._load_selected)
        layout.addWidget(self.table)
        self.refresh()

    @Slot()
    def refresh(self) -> None:
        """Load live Memory values for the user, including while use is paused."""
        try:
            entries = self._service.list_for_user()
        except Exception as exc:
            self.status_message.emit(f"读取记忆失败：{type(exc).__name__}")
            return
        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            values = (
                entry.scope.value,
                entry.category.value,
                entry.key.value,
                entry.value,
                entry.source_type.value,
                str(entry.version),
                entry.updated_at.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                str(entry.memory_id),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 3:
                    item.setData(Qt.ItemDataRole.UserRole, value)
                self.table.setItem(row, column, item)

    @Slot(bool)
    def _toggle_enabled(self, enabled: bool) -> None:
        try:
            self._service.set_enabled(enabled)
        except Exception as exc:
            self.enabled.blockSignals(True)
            self.enabled.setChecked(not enabled)
            self.enabled.blockSignals(False)
            self.status_message.emit(f"更新记忆设置失败：{type(exc).__name__}")
            return
        state = "启用" if enabled else "暂停"
        self.status_message.emit(f"持久记忆已{state}；现有条目和审计未删除。")

    @Slot()
    def _save(self) -> None:
        try:
            key = MemoryKey(str(self.key.currentData()))
        except ValueError:
            self.status_message.emit("偏好类型无效。")
            return
        if not self.value.text().strip():
            self.status_message.emit("请选择偏好并输入值。")
            return
        candidate = explicit_setting_candidate(key, self.value.text().strip())
        answer = QMessageBox.question(
            self,
            "保存偏好记忆",
            f"保存偏好 {key.value}={candidate.value!r}。它只作为提示，不能替代任何确认。是否保存？",
        )
        if answer is not QMessageBox.StandardButton.Yes:
            return
        try:
            self._service.save(candidate, confirmed=True)
        except Exception as exc:
            self.status_message.emit(f"偏好未保存：{type(exc).__name__}: {exc}")
        else:
            self.value.clear()
            self.status_message.emit("偏好已保存；不会改变安全或权限规则。")
        self.refresh()

    @Slot()
    def _load_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        key_item, value_item = self.table.item(row, 2), self.table.item(row, 3)
        if key_item is None or value_item is None:
            return
        index = self.key.findData(key_item.text())
        if index >= 0:
            self.key.setCurrentIndex(index)
            self.value.setText(value_item.text())

    @Slot()
    def _delete(self) -> None:
        row = self.table.currentRow()
        id_item = self.table.item(row, 7) if row >= 0 else None
        key_item = self.table.item(row, 2) if row >= 0 else None
        if row < 0 or id_item is None or key_item is None:
            self.status_message.emit("请先选择一条记忆。")
            return
        memory_id = UUID(id_item.text())
        key = key_item.text()
        answer = QMessageBox.question(
            self,
            "删除记忆",
            f"将永久忘记偏好 {key!r}。删除后不能自动恢复，是否继续？",
        )
        if answer is QMessageBox.StandardButton.Yes:
            try:
                self._service.delete(memory_id, confirmed=True)
            except Exception as exc:
                self.status_message.emit(f"删除记忆失败：{type(exc).__name__}")
            else:
                self.refresh()
                self.status_message.emit("记忆已删除；审计中不保留原值。")

    @Slot()
    def _clear_scope(self) -> None:
        try:
            scope = MemoryScope(str(self.scope.currentData()))
        except ValueError:
            return
        answer = QMessageBox.question(
            self,
            "清空记忆范围",
            f"将永久清空 {scope.value} 范围内的全部记忆，不能自动恢复。是否继续？",
        )
        if answer is QMessageBox.StandardButton.Yes:
            try:
                count = self._service.clear_scope(scope, confirmed=True)
            except Exception as exc:
                self.status_message.emit(f"清空记忆失败：{type(exc).__name__}")
            else:
                self.refresh()
                self.status_message.emit(f"已清空 {count} 条记忆；审计中不保留原值。")

    @Slot()
    def _clear_all(self) -> None:
        answer = QMessageBox.question(
            self,
            "清空全部记忆",
            "将永久清空全部偏好记忆，不能自动恢复。审计记录不会被删除。是否继续？",
        )
        if answer is QMessageBox.StandardButton.Yes:
            try:
                count = self._service.clear_all(confirmed=True)
            except Exception as exc:
                self.status_message.emit(f"清空全部记忆失败：{type(exc).__name__}")
            else:
                self.refresh()
                self.status_message.emit(f"已清空 {count} 条记忆；审计中不保留原值。")
