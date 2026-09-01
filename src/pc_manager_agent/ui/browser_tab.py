"""Non-blocking Stage 5C browser review workspace."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from pc_manager_agent.app.browser import BrowserServices
from pc_manager_agent.domain.browser import (
    BrowserActionKind,
    BrowserActionResult,
    BrowserElementReference,
    BrowserObservation,
    BrowserSessionDescriptor,
)
from pc_manager_agent.domain.browser_downloads import (
    BrowserDownloadIdentity,
    BrowserDownloadPreview,
    BrowserDownloadRecoveryRecord,
)
from pc_manager_agent.domain.browser_plans import BrowserConfirmationRecord, BrowserTaskPlan


class _WorkerSignals(QObject):
    completed = Signal(object)
    failed = Signal(str)


class _BrowserWorker(QRunnable):
    """Run one serialized browser service call outside the GUI thread."""

    def __init__(self, operation: Callable[[], object]) -> None:
        super().__init__()
        self.signals = _WorkerSignals()
        self._operation = operation

    @Slot()
    def run(self) -> None:
        """Emit one result or a user-safe failure code."""
        try:
            result = self._operation()
        except Exception as exc:
            message = str(exc)
            self.signals.failed.emit(message if message.isascii() else type(exc).__name__)
            return
        self.signals.completed.emit(result)


class BrowserTab(QWidget):
    """Show exact plans and bounded observations; never execute adapter methods directly."""

    status_message = Signal(str)
    office_handoff_requested = Signal(object)

    def __init__(self, services: BrowserServices, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._services = services
        self._worker: _BrowserWorker | None = None
        self._pending_plan: BrowserTaskPlan | None = None
        self._pending_confirmation: BrowserConfirmationRecord | None = None
        self._download_preview: BrowserDownloadPreview | None = None
        self._download_recovery: BrowserDownloadRecoveryRecord | None = None
        self._elements: dict[UUID, BrowserElementReference] = {}
        self._download_directory: Path | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "独立临时 Chromium：不读取现有浏览器资料。网页内容一律是不可信数据；"
                "购买、发消息、上传、账户修改和远程写入始终被阻止。"
            )
        )
        navigation = QHBoxLayout()
        self._url = QLineEdit()
        self._url.setPlaceholderText("输入完整 HTTPS 网址，例如 https://example.com/")
        self._start = QPushButton("启动隔离浏览器")
        self._prepare_navigation = QPushButton("生成导航计划")
        self._start.clicked.connect(self._start_session)
        self._prepare_navigation.clicked.connect(self._plan_navigation)
        self._prepare_navigation.setEnabled(False)
        navigation.addWidget(self._url, 1)
        navigation.addWidget(self._start)
        navigation.addWidget(self._prepare_navigation)
        layout.addLayout(navigation)

        self._plan_view = QTextBrowser()
        self._plan_view.setPlaceholderText("精确来源、动作、风险和预计影响会显示在这里。")
        layout.addWidget(self._plan_view, 2)

        confirmation = QHBoxLayout()
        self._approve = QPushButton("确认当前计划")
        self._reject = QPushButton("拒绝当前计划")
        self._execute = QPushButton("执行已确认动作")
        self._approve.clicked.connect(self._approve_plan)
        self._reject.clicked.connect(self._reject_plan)
        self._execute.clicked.connect(self._execute_plan)
        for button in (self._approve, self._reject, self._execute):
            button.setEnabled(False)
            confirmation.addWidget(button)
        layout.addLayout(confirmation)

        self._page_summary = QTextBrowser()
        self._page_summary.setPlaceholderText("页面标题、可见文本和提示注入信号会显示在这里。")
        layout.addWidget(self._page_summary, 2)
        self._elements_table = QTableWidget(0, 3)
        self._elements_table.setHorizontalHeaderLabels(("语义角色", "可访问名称", "目标"))
        self._elements_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._elements_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._elements_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._elements_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self._elements_table, 3)

        action_row = QHBoxLayout()
        self._action_kind = QComboBox()
        for label, kind in (
            ("打开选中链接", BrowserActionKind.OPEN_LINK),
            ("站内搜索", BrowserActionKind.SEARCH),
            ("筛选", BrowserActionKind.FILTER),
            ("下一页", BrowserActionKind.NEXT_PAGE),
            ("上一页", BrowserActionKind.PREVIOUS_PAGE),
            ("展开只读内容", BrowserActionKind.EXPAND),
        ):
            self._action_kind.addItem(label, kind.value)
        self._action_text = QLineEdit()
        self._action_text.setPlaceholderText("搜索/筛选文字；其他动作留空")
        prepare_action = QPushButton("为选中元素生成计划")
        prepare_action.clicked.connect(self._plan_element_action)
        action_row.addWidget(self._action_kind)
        action_row.addWidget(self._action_text, 1)
        action_row.addWidget(prepare_action)
        layout.addLayout(action_row)

        download_row = QHBoxLayout()
        self._download_label = QLabel("下载目录：未选择")
        choose_download = QPushButton("选择下载目录")
        prepare_download = QPushButton("为选中链接生成下载 Preview")
        choose_download.clicked.connect(self._choose_download_directory)
        prepare_download.clicked.connect(self._plan_download)
        download_row.addWidget(self._download_label, 1)
        download_row.addWidget(choose_download)
        download_row.addWidget(prepare_download)
        layout.addLayout(download_row)

        takeover_row = QHBoxLayout()
        begin_takeover = QPushButton("用户手动接管登录/MFA/CAPTCHA")
        end_takeover = QPushButton("交还 Agent 并重新验证")
        cancel = QPushButton("取消并销毁浏览器会话")
        begin_takeover.clicked.connect(self._begin_takeover)
        end_takeover.clicked.connect(self._end_takeover)
        cancel.clicked.connect(self.cancel)
        takeover_row.addWidget(begin_takeover)
        takeover_row.addWidget(end_takeover)
        takeover_row.addWidget(cancel)
        layout.addLayout(takeover_row)
        self._handoff = QPushButton("将本次下载交给办公文档重新确认读取")
        self._handoff.setEnabled(False)
        self._handoff.clicked.connect(self._handoff_to_office)
        layout.addWidget(self._handoff)
        self._rollback_download = QPushButton("回滚本次未修改的下载（移入恢复区）")
        self._rollback_download.setEnabled(False)
        self._rollback_download.clicked.connect(self._rollback_last_download)
        layout.addWidget(self._rollback_download)

    def set_user_goal(self, text: str) -> None:
        """Accept chat/voice navigation intent without starting or confirming any action."""
        match = re.search(r"https?://[^\s<>\"']+", text)
        if match is not None:
            self._url.setText(match.group(0).rstrip("。.!！?？,，"))
        self.status_message.emit("已转到受控浏览器；请检查网址并手动生成精确计划。")

    @Slot()
    def _start_session(self) -> None:
        self._run(lambda: self._services.service.start(headless=False), self._session_started)

    def _session_started(self, value: object) -> None:
        if not isinstance(value, BrowserSessionDescriptor):
            self._failure("BROWSER_UI_SESSION_RESULT_INVALID")
            return
        self._start.setEnabled(False)
        self._prepare_navigation.setEnabled(True)
        self.status_message.emit("隔离浏览器已启动；尚未访问任何网站。")

    @Slot()
    def _plan_navigation(self) -> None:
        url = self._url.text().strip()
        if not url:
            self._failure("请先输入一个完整网址。")
            return
        self._clear_pending()
        self._run(
            lambda: self._services.service.prepare_navigation(
                url,
                user_goal_summary="访问用户明确输入的网址",
            ),
            self._plan_ready,
        )

    @Slot()
    def _plan_element_action(self) -> None:
        element_id = self._selected_element_id()
        if element_id is None:
            self._failure("请先选中一个页面元素。")
            return
        kind = BrowserActionKind(str(self._action_kind.currentData()))
        text = (
            self._action_text.text().strip()
            if kind
            in {
                BrowserActionKind.SEARCH,
                BrowserActionKind.FILTER,
            }
            else None
        )
        self._clear_pending()
        self._run(
            lambda: self._services.service.prepare_action(
                element_id=element_id,
                kind=kind,
                text=text,
                user_goal_summary=f"用户选择的语义动作：{kind.value}",
            ),
            self._plan_ready,
        )

    @Slot()
    def _plan_download(self) -> None:
        element_id = self._selected_element_id()
        if element_id is None or self._download_directory is None:
            self._failure("请先选中一个文档链接并选择下载目录。")
            return
        destination_directory = self._download_directory
        self._clear_pending()
        self._run(
            lambda: self._services.service.prepare_download(
                element_id=element_id,
                destination_directory=destination_directory,
                user_goal_summary="下载用户明确选择的一个安全文档",
            ),
            self._download_plan_ready,
        )

    def _plan_ready(self, value: object) -> None:
        if not isinstance(value, BrowserTaskPlan):
            self._failure("BROWSER_UI_PLAN_RESULT_INVALID")
            return
        self._pending_plan = value
        self._plan_view.setPlainText(value.model_dump_json(indent=2))
        self._approve.setEnabled(True)
        self._reject.setEnabled(True)
        self._execute.setEnabled(False)
        self.status_message.emit("计划已通过确定性审查，等待你的明确确认。")

    def _download_plan_ready(self, value: object) -> None:
        if (
            not isinstance(value, tuple)
            or len(value) != 2
            or not isinstance(value[0], BrowserDownloadPreview)
            or not isinstance(value[1], BrowserTaskPlan)
        ):
            self._failure("BROWSER_UI_DOWNLOAD_PREVIEW_INVALID")
            return
        self._download_preview, plan = value
        self._plan_ready(plan)
        self._plan_view.append(
            "\n下载 Preview：\n" + self._download_preview.model_dump_json(indent=2)
        )

    @Slot()
    def _approve_plan(self) -> None:
        plan = self._pending_plan
        if plan is None:
            return
        try:
            record = self._services.service.request_confirmation(plan)
            self._services.service.resolve_confirmation(
                plan,
                record.confirmation_id,
                approved=True,
            )
        except Exception as exc:
            self._failure(str(exc))
            return
        self._pending_confirmation = record
        self._approve.setEnabled(False)
        self._reject.setEnabled(False)
        self._execute.setEnabled(True)
        self.status_message.emit("当前精确计划已确认；修改计划后旧确认会失效。")

    @Slot()
    def _reject_plan(self) -> None:
        plan = self._pending_plan
        if plan is None:
            return
        try:
            record = self._services.service.request_confirmation(plan)
            self._services.service.resolve_confirmation(
                plan,
                record.confirmation_id,
                approved=False,
            )
        except Exception as exc:
            self._failure(str(exc))
            return
        self._clear_pending(invalidate=False)
        self.status_message.emit("计划已拒绝；没有执行网页动作。")

    @Slot()
    def _execute_plan(self) -> None:
        plan = self._pending_plan
        record = self._pending_confirmation
        if plan is None or record is None:
            self._failure("没有已确认的当前计划。")
            return
        self._execute.setEnabled(False)
        if self._download_preview is not None:
            preview = self._download_preview
            self._run(
                lambda: self._services.service.execute_download(
                    preview,
                    plan,
                    record.confirmation_id,
                ),
                self._download_completed,
            )
        else:
            self._run(
                lambda: self._services.service.execute(plan, record.confirmation_id),
                self._action_completed,
            )

    def _action_completed(self, value: object) -> None:
        if not isinstance(value, BrowserActionResult):
            self._failure("BROWSER_UI_ACTION_RESULT_INVALID")
            return
        self._clear_pending(invalidate=False)
        if value.observation is not None:
            self._show_observation(value.observation)
        self.status_message.emit("动作完成并已生成新的页面观察；旧元素引用已失效。")

    def _download_completed(self, value: object) -> None:
        if (
            not isinstance(value, tuple)
            or len(value) != 2
            or not isinstance(value[0], BrowserDownloadIdentity)
            or not isinstance(value[1], BrowserDownloadRecoveryRecord)
        ):
            self._failure("BROWSER_UI_DOWNLOAD_RESULT_INVALID")
            return
        identity, recovery = value
        self._download_recovery = recovery
        self._handoff.setProperty("download_path", str(identity.path))
        self._handoff.setEnabled(True)
        self._rollback_download.setEnabled(True)
        self._clear_pending(invalidate=False)
        self.status_message.emit(f"已保存并校验一个文件：{identity.path.name}；未自动打开或执行。")

    def _show_observation(self, observation: BrowserObservation) -> None:
        signals = ", ".join(item.value for item in observation.prompt_injection_signals) or "无"
        self._page_summary.setPlainText(
            f"标题：{observation.title}\n网址：{observation.url}\n"
            f"不可信内容信号：{signals}\n截断：{observation.truncated}\n\n"
            f"{observation.visible_text}"
        )
        self._elements = {item.element_id: item for item in observation.elements}
        self._elements_table.setRowCount(len(observation.elements))
        for row, element in enumerate(observation.elements):
            values = (element.role.value, element.accessible_name, element.href or "")
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, str(element.element_id))
                self._elements_table.setItem(row, column, item)
        self._elements_table.resizeColumnsToContents()

    @Slot()
    def _choose_download_directory(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "选择一个明确下载目录")
        if selected:
            self._download_directory = Path(selected)
            self._download_label.setText(f"下载目录：{selected}")

    @Slot()
    def _begin_takeover(self) -> None:
        if (
            QMessageBox.question(
                self,
                "手动接管",
                "接管期间请只在独立 Chromium 窗口中手动输入密码、MFA 或 CAPTCHA。"
                "Agent 不读取或记录这些值。交还后页面会重新加载，全部旧确认失效。是否继续？",
            )
            is not QMessageBox.StandardButton.Yes
        ):
            return
        self._clear_pending()
        self._run(self._services.service.begin_user_takeover, self._takeover_started)

    def _takeover_started(self, value: object) -> None:
        if not isinstance(value, BrowserActionResult):
            self._failure("BROWSER_UI_TAKEOVER_RESULT_INVALID")
            return
        self._elements.clear()
        self._elements_table.setRowCount(0)
        self.status_message.emit("已进入用户手动接管；Agent 动作权限已冻结。")

    @Slot()
    def _end_takeover(self) -> None:
        self._run(self._services.service.end_user_takeover, self._handback_completed)

    def _handback_completed(self, value: object) -> None:
        if not isinstance(value, BrowserObservation):
            self._failure("BROWSER_UI_HAND_BACK_RESULT_INVALID")
            return
        self._show_observation(value)
        self.status_message.emit("已重新加载并验证页面；接管前元素和确认全部失效。")

    @Slot()
    def _handoff_to_office(self) -> None:
        value = self._handoff.property("download_path")
        if isinstance(value, str):
            self.office_handoff_requested.emit(Path(value))

    @Slot()
    def _rollback_last_download(self) -> None:
        record = self._download_recovery
        if record is None:
            return
        if (
            QMessageBox.question(
                self,
                "确认回滚下载",
                f"将把未修改的 {record.download.path.name} 移入应用恢复区。"
                "若文件内容已变化或恢复位置冲突，操作会停止且不覆盖。是否继续？",
            )
            is not QMessageBox.StandardButton.Yes
        ):
            return
        self._run(
            lambda: self._services.service.rollback_download(record),
            self._rollback_completed,
        )

    def _rollback_completed(self, value: object) -> None:
        if not isinstance(value, Path):
            self._failure("BROWSER_UI_ROLLBACK_RESULT_INVALID")
            return
        self._download_recovery = None
        self._handoff.setEnabled(False)
        self._rollback_download.setEnabled(False)
        self.status_message.emit(f"下载已移入恢复区：{value.name}；没有覆盖任何文件。")

    def _selected_element_id(self) -> UUID | None:
        row = self._elements_table.currentRow()
        if row < 0:
            return None
        item = self._elements_table.item(row, 0)
        if item is None:
            return None
        try:
            value = UUID(str(item.data(Qt.ItemDataRole.UserRole)))
        except ValueError:
            return None
        return value if value in self._elements else None

    def _clear_pending(self, *, invalidate: bool = True) -> None:
        if invalidate and self._services.service.session is not None:
            try:
                self._services.service.invalidate_pending_authority()
            except Exception as exc:
                self.status_message.emit(f"确认失效处理失败：{type(exc).__name__}；不会执行。")
        self._pending_plan = None
        self._pending_confirmation = None
        self._download_preview = None
        self._approve.setEnabled(False)
        self._reject.setEnabled(False)
        self._execute.setEnabled(False)

    def _run(self, operation: Callable[[], object], completed: Callable[[object], None]) -> None:
        if self._worker is not None:
            self._failure("已有浏览器任务正在进行，请先等待或取消。")
            return
        worker = _BrowserWorker(operation)
        worker.signals.completed.connect(lambda value: self._finished(value, completed))
        worker.signals.failed.connect(self._worker_failed)
        self._worker = worker
        QThreadPool.globalInstance().start(worker)
        self.status_message.emit("浏览器任务进行中……")

    def _finished(self, value: object, completed: Callable[[object], None]) -> None:
        self._worker = None
        completed(value)

    def _worker_failed(self, message: str) -> None:
        self._worker = None
        self._failure(message)

    @Slot()
    def cancel(self) -> None:
        """Cancel future work and destroy the disposable browser context."""
        try:
            self._services.service.cancel()
        except Exception as exc:
            self._failure(str(exc))
            return
        self._clear_pending(invalidate=False)
        self.status_message.emit("浏览器会话已取消并销毁；不会自动恢复或重放。")

    def shutdown(self) -> bool:
        """Request browser shutdown; the application waits for the shared worker pool."""
        try:
            self._services.service.close()
        except Exception:
            return False
        return True

    def _failure(self, message: str) -> None:
        QMessageBox.warning(self, "浏览器操作未执行", message)
        self.status_message.emit(message)
