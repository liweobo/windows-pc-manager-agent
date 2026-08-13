"""Stage 4B startup inventory, filtering, safe actions, and restore history UI."""

from __future__ import annotations

from uuid import UUID

from PySide6.QtCore import QThreadPool, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.domain.startup_actions import (
    DisabledStartupRecord,
    StartupActionType,
    StartupIdentity,
    StartupObservation,
    StartupSafetyAssessment,
    StartupSafetyDecision,
)
from pc_manager_agent.ui.startup_action_dialog import StartupActionDialog
from pc_manager_agent.ui.startup_workers import StartupInventoryWorker, require_inventory


class StartupManagementTab(QWidget):
    """Present read-only inventory and expose only object-specific safe actions."""

    status_message = Signal(str)

    def __init__(self, runtime: ApplicationRuntime) -> None:
        super().__init__()
        self._runtime = runtime
        self._worker: StartupInventoryWorker | None = None
        self._active: tuple[tuple[StartupObservation, StartupSafetyAssessment], ...] = ()
        self._disabled: tuple[DisabledStartupRecord, ...] = ()
        self._dialogs: set[StartupActionDialog] = set()
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        info = QLabel(
            "仅管理当前用户 HKCU Run 与可安全解析的当前用户 Startup Folder .lnk。"
            "系统、Microsoft、安全、驱动、企业、Agent 和未知对象默认只读或阻止。"
        )
        info.setWordWrap(True)
        top = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("筛选名称、发布者、来源、状态或目标程序")
        self._search.textChanged.connect(self._render)
        self._refresh = QPushButton("刷新（只读）")
        self._refresh.clicked.connect(self.refresh)
        top.addWidget(self._search)
        top.addWidget(self._refresh)

        self._active_table = _table(
            ("名称", "发布者", "来源", "范围", "状态", "目标程序", "安全分类", "管理")
        )
        self._active_table.itemSelectionChanged.connect(self._selection_changed)
        active_row = QHBoxLayout()
        active_row.addWidget(QLabel("当前启动项"))
        active_row.addStretch(1)
        self._disable = QPushButton("为选中项生成停用 Preview")
        self._disable.setEnabled(False)
        self._disable.clicked.connect(self._disable_selected)
        active_row.addWidget(self._disable)

        disabled_row = QHBoxLayout()
        disabled_row.addWidget(QLabel("Agent 已停用（具有已验证备份）"))
        disabled_row.addStretch(1)
        self._restore = QPushButton("为选中项生成恢复 Preview")
        self._restore.setEnabled(False)
        self._restore.clicked.connect(self._restore_selected)
        disabled_row.addWidget(self._restore)
        self._disabled_table = _table(("名称", "来源", "停用时间", "备份摘要", "恢复能力"))
        self._disabled_table.itemSelectionChanged.connect(self._selection_changed)

        self._status = QLabel("正在读取启动项；未执行任何修改。")
        self._status.setWordWrap(True)
        layout.addWidget(info)
        layout.addLayout(top)
        layout.addLayout(active_row)
        layout.addWidget(self._active_table, 3)
        layout.addLayout(disabled_row)
        layout.addWidget(self._disabled_table, 2)
        layout.addWidget(self._status)

    @Slot()
    def refresh(self) -> None:
        """Start a bounded read-only inventory refresh off the GUI thread."""
        if self._worker is not None:
            return
        self._refresh.setEnabled(False)
        self._disable.setEnabled(False)
        self._restore.setEnabled(False)
        self._status.setText("正在重新读取启动配置和 Agent 恢复索引；未执行写操作。")
        worker = StartupInventoryWorker(self._runtime)
        worker.signals.completed.connect(self._inventory_ready)
        worker.signals.failed.connect(self._failed)
        self._worker = worker
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _inventory_ready(self, value: object) -> None:
        self._worker = None
        self._refresh.setEnabled(True)
        try:
            inventory = require_inventory(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        self._active = inventory.active
        self._disabled = inventory.disabled
        self._render()
        self._status.setText(
            f"只读刷新完成：{len(self._active)} 个当前条目，"
            f"{len(self._disabled)} 个 Agent 可恢复条目。"
        )
        self.status_message.emit(self._status.text())

    @Slot()
    def _render(self) -> None:
        term = self._search.text().strip().casefold()
        active = tuple(
            item for item in self._active if not term or term in _active_text(item).casefold()
        )
        disabled = tuple(
            item for item in self._disabled if not term or term in _disabled_text(item).casefold()
        )
        self._active_table.setSortingEnabled(False)
        self._active_table.setRowCount(len(active))
        for row, (observation, assessment) in enumerate(active):
            active_values = (
                observation.display_name,
                observation.publisher or "未知",
                observation.identity.source.value,
                observation.scope,
                observation.status.value,
                str(observation.executable_path or "未解析"),
                assessment.safety_class.value,
                (
                    "可停用（需双重确认）"
                    if assessment.decision is StartupSafetyDecision.ALLOW
                    else f"只读/阻止：{assessment.explanation}"
                ),
            )
            for column, text in enumerate(active_values):
                cell = QTableWidgetItem(text)
                cell.setData(256, observation)
                cell.setData(257, assessment)
                self._active_table.setItem(row, column, cell)
        self._active_table.setSortingEnabled(True)
        self._disabled_table.setSortingEnabled(False)
        self._disabled_table.setRowCount(len(disabled))
        for row, record in enumerate(disabled):
            disabled_values = (
                record.display_name,
                record.identity.source.value,
                record.disabled_at.isoformat(),
                f"{record.backup_digest[:12]}…",
                "FULL（目标冲突时停止）",
            )
            for column, text in enumerate(disabled_values):
                cell = QTableWidgetItem(text)
                cell.setData(256, record)
                self._disabled_table.setItem(row, column, cell)
        self._disabled_table.setSortingEnabled(True)
        self._selection_changed()

    @Slot()
    def _selection_changed(self) -> None:
        active = self._selected_active()
        self._disable.setEnabled(
            active is not None and active[1].decision is StartupSafetyDecision.ALLOW
        )
        self._restore.setEnabled(self._selected_disabled() is not None)

    @Slot()
    def _disable_selected(self) -> None:
        selected = self._selected_active()
        if selected is None or selected[1].decision is not StartupSafetyDecision.ALLOW:
            return
        observation, _assessment = selected
        self._open_dialog(
            StartupActionType.DISABLE,
            observation.display_name,
            identity=observation.identity,
        )

    @Slot()
    def _restore_selected(self) -> None:
        record = self._selected_disabled()
        if record is None:
            return
        self._open_dialog(
            StartupActionType.RESTORE,
            record.display_name,
            backup_id=record.backup_id,
        )

    def _open_dialog(
        self,
        action: StartupActionType,
        display_name: str,
        *,
        identity: StartupIdentity | None = None,
        backup_id: UUID | None = None,
    ) -> None:
        dialog = StartupActionDialog(
            self._runtime,
            action,
            identity=identity,
            backup_id=backup_id,
            display_name=display_name,
            parent=self,
        )
        dialog.completed.connect(self.refresh)
        dialog.finished.connect(lambda _result, value=dialog: self._dialogs.discard(value))
        self._dialogs.add(dialog)
        dialog.show()

    def _selected_active(
        self,
    ) -> tuple[StartupObservation, StartupSafetyAssessment] | None:
        items = self._active_table.selectedItems()
        if not items:
            return None
        observation = items[0].data(256)
        assessment = items[0].data(257)
        if not isinstance(observation, StartupObservation) or not isinstance(
            assessment,
            StartupSafetyAssessment,
        ):
            return None
        return observation, assessment

    def _selected_disabled(self) -> DisabledStartupRecord | None:
        items = self._disabled_table.selectedItems()
        if not items:
            return None
        value = items[0].data(256)
        return value if isinstance(value, DisabledStartupRecord) else None

    @Slot(str)
    def _failed(self, message: str) -> None:
        self._worker = None
        self._refresh.setEnabled(True)
        self._status.setText(f"启动项读取或操作失败：{message}")
        self.status_message.emit(self._status.text())

    def shutdown(self) -> None:
        """Tell open dialogs to reject pending approval after bounded workers return."""
        for dialog in tuple(self._dialogs):
            dialog.shutdown()


def _table(headers: tuple[str, ...]) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setSortingEnabled(True)
    table.horizontalHeader().setStretchLastSection(True)
    return table


def _active_text(value: tuple[StartupObservation, StartupSafetyAssessment]) -> str:
    observation, assessment = value
    return " ".join(
        (
            observation.display_name,
            observation.publisher or "",
            observation.identity.source.value,
            observation.status.value,
            str(observation.executable_path or ""),
            assessment.safety_class.value,
            assessment.explanation,
        )
    )


def _disabled_text(value: DisabledStartupRecord) -> str:
    return " ".join((value.display_name, value.identity.source.value, value.backup_digest))
