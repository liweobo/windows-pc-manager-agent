"""Stage 4C1 read-only service inventory and object-specific action entry points."""

from __future__ import annotations

from PySide6.QtCore import QThreadPool, Signal, Slot
from PySide6.QtGui import QShowEvent
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
from pc_manager_agent.domain.service_actions import (
    ServiceActionType,
    ServiceInventoryItem,
)
from pc_manager_agent.ui.service_action_dialog import ServiceActionDialog
from pc_manager_agent.ui.service_workers import (
    ServiceInventoryWorker,
    require_service_inventory,
)


class ServiceManagementTab(QWidget):
    """Display service safety evidence and expose only exact eligible actions."""

    status_message = Signal(str)
    service_reference_changed = Signal(str, str)

    def __init__(self, runtime: ApplicationRuntime) -> None:
        super().__init__()
        self._runtime = runtime
        self._worker: ServiceInventoryWorker | None = None
        self._items: tuple[ServiceInventoryItem, ...] = ()
        self._dialogs: set[ServiceActionDialog] = set()
        self._load_requested = False
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        info = QLabel(
            "仅允许普通用户当前权限可控制、身份明确的单个第三方独立进程服务。"
            "系统、安全、驱动、网络、登录、存储、更新、Agent、共享进程及未知服务只读。"
        )
        info.setWordWrap(True)
        top = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("按 service name、显示名称、状态、账号或安全分类筛选")
        self._search.textChanged.connect(self._render)
        self._refresh = QPushButton("刷新（只读）")
        self._refresh.clicked.connect(self.refresh)
        top.addWidget(self._search)
        top.addWidget(self._refresh)
        self._table = QTableWidget(0, 9)
        self._table.setHorizontalHeaderLabels(
            (
                "Service name",
                "显示名称",
                "状态",
                "启动类型",
                "账号",
                "安全分类",
                "依赖",
                "运行中的 Dependents",
                "允许操作",
            )
        )
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setSortingEnabled(True)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.itemSelectionChanged.connect(self._selection_changed)
        actions = QHBoxLayout()
        self._start = QPushButton("生成 START Preview")
        self._stop = QPushButton("生成 STOP Preview")
        self._restart = QPushButton("生成 RESTART Preview")
        for button in (self._start, self._stop, self._restart):
            button.setEnabled(False)
            actions.addWidget(button)
        self._start.clicked.connect(lambda: self._open_action(ServiceActionType.START))
        self._stop.clicked.connect(lambda: self._open_action(ServiceActionType.STOP))
        self._restart.clicked.connect(lambda: self._open_action(ServiceActionType.RESTART))
        self._status = QLabel("打开本页时才会在后台读取服务；未执行任何控制。")
        self._status.setWordWrap(True)
        layout.addWidget(info)
        layout.addLayout(top)
        layout.addWidget(self._table, 1)
        layout.addLayout(actions)
        layout.addWidget(self._status)

    @Slot()
    def refresh(self) -> None:
        """Start a bounded read-only inventory refresh off the GUI thread."""
        if self._worker is not None:
            return
        self._load_requested = True
        self._refresh.setEnabled(False)
        self._set_actions(False, False, False)
        self._status.setText("正在读取服务身份、依赖和当前用户访问权限；没有发送控制。")
        worker = ServiceInventoryWorker(self._runtime)
        worker.signals.completed.connect(self._inventory_ready)
        worker.signals.failed.connect(self._failed)
        self._worker = worker
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _inventory_ready(self, value: object) -> None:
        self._worker = None
        self._refresh.setEnabled(True)
        try:
            self._items = require_service_inventory(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        self._render()
        controllable = sum(
            item.start_allowed or item.stop_allowed or item.restart_allowed for item in self._items
        )
        self._status.setText(
            f"只读刷新完成：{len(self._items)} 个服务；"
            f"{controllable} 个至少有一项动作可进入 Preview。"
        )
        self.status_message.emit(self._status.text())

    def showEvent(self, event: QShowEvent) -> None:
        """Lazily enumerate SCM only after the user opens the service page."""
        super().showEvent(event)
        if not self._load_requested:
            self.refresh()

    @Slot()
    def _render(self) -> None:
        term = self._search.text().strip().casefold()
        items = tuple(
            item for item in self._items if not term or term in _search_text(item).casefold()
        )
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(items))
        for row, item in enumerate(items):
            observation = item.observation
            dependencies = ", ".join(value.service_name for value in observation.dependencies)
            dependents = ", ".join(
                value.service_name
                for value in observation.dependents
                if value.state.value != "STOPPED"
            )
            actions = (
                "/".join(
                    name
                    for name, allowed in (
                        ("START", item.start_allowed),
                        ("STOP", item.stop_allowed),
                        ("RESTART", item.restart_allowed),
                    )
                    if allowed
                )
                or f"只读：{item.explanation}"
            )
            values = (
                observation.identity.service_name,
                observation.identity.display_name,
                observation.state.value,
                str(observation.identity.start_type),
                observation.identity.service_account,
                item.safety_class.value,
                dependencies or "无",
                dependents or "无",
                actions,
            )
            for column, text in enumerate(values):
                cell = QTableWidgetItem(text)
                cell.setData(256, item)
                self._table.setItem(row, column, cell)
        self._table.setSortingEnabled(True)
        self._selection_changed()

    @Slot()
    def _selection_changed(self) -> None:
        item = self._selected()
        self._set_actions(
            bool(item and item.start_allowed),
            bool(item and item.stop_allowed),
            bool(item and item.restart_allowed),
        )
        if item is not None:
            self.service_reference_changed.emit(
                item.observation.identity.service_name,
                item.observation.identity.display_name,
            )

    def _set_actions(self, start: bool, stop: bool, restart: bool) -> None:
        self._start.setEnabled(start)
        self._stop.setEnabled(stop)
        self._restart.setEnabled(restart)

    def _selected(self) -> ServiceInventoryItem | None:
        items = self._table.selectedItems()
        if not items:
            return None
        value = items[0].data(256)
        return value if isinstance(value, ServiceInventoryItem) else None

    def _open_action(self, action: ServiceActionType) -> None:
        item = self._selected()
        if item is None:
            return
        allowed = {
            ServiceActionType.START: item.start_allowed,
            ServiceActionType.STOP: item.stop_allowed,
            ServiceActionType.RESTART: item.restart_allowed,
        }[action]
        if not allowed:
            return
        observation = item.observation
        self.open_action_request(
            action,
            observation.identity.service_name,
            display_name=observation.identity.display_name,
        )

    def open_action_request(
        self,
        action: ServiceActionType,
        target_query: str,
        *,
        display_name: str | None = None,
    ) -> None:
        """Open a Preview flow; local resolver must still establish exact identity."""
        dialog = ServiceActionDialog(
            self._runtime,
            action,
            service_name=target_query,
            display_name=display_name or target_query,
            parent=self,
        )
        dialog.completed.connect(self.refresh)
        dialog.finished.connect(lambda _result, value=dialog: self._dialogs.discard(value))
        self._dialogs.add(dialog)
        dialog.show()

    @Slot(str)
    def _failed(self, message: str) -> None:
        self._worker = None
        self._refresh.setEnabled(True)
        self._set_actions(False, False, False)
        self._status.setText(f"服务只读清单或操作失败：{message}")
        self.status_message.emit(self._status.text())

    def shutdown(self) -> None:
        """Prevent future undispatched service controls in open dialogs."""
        for dialog in tuple(self._dialogs):
            dialog.shutdown()


def _search_text(item: ServiceInventoryItem) -> str:
    observation = item.observation
    return " ".join(
        (
            observation.identity.service_name,
            observation.identity.display_name,
            observation.state.value,
            observation.identity.service_account,
            item.safety_class.value,
            item.explanation,
        )
    )
