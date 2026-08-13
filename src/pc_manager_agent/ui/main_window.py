"""Main PySide6 window; all operations are delegated to orchestration services."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThreadPool, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.audit.repository import AuditUnavailableError
from pc_manager_agent.confirmation.models import ConfirmationRequest
from pc_manager_agent.domain.plans import TaskPlan
from pc_manager_agent.domain.process_actions import (
    ProcessTargetQuery,
    ProcessTargetQueryType,
)
from pc_manager_agent.domain.reports import ScanReport
from pc_manager_agent.orchestration.process_action_planner import (
    is_process_action_request,
    process_target_query,
)
from pc_manager_agent.orchestration.service import ScanOrchestrator
from pc_manager_agent.orchestration.service_action_planner import (
    service_action_intent,
    service_target_query,
)
from pc_manager_agent.orchestration.system_diagnostic_planner import is_diagnostic_request
from pc_manager_agent.orchestration.trash_planner import TrashIntentDecision, classify_trash_intent
from pc_manager_agent.ui.analysis_tab import FileAnalysisTab
from pc_manager_agent.ui.operation_tab import FileOperationTab
from pc_manager_agent.ui.service_management_tab import ServiceManagementTab
from pc_manager_agent.ui.startup_management_tab import StartupManagementTab
from pc_manager_agent.ui.system_diagnostics_tab import SystemDiagnosticsTab
from pc_manager_agent.ui.system_tray import SystemTrayController
from pc_manager_agent.ui.trash_tab import TrashTab
from pc_manager_agent.ui.workers import ScanWorker, require_scan_report


class MainWindow(QMainWindow):
    """Present plans and reports while keeping safety logic outside the UI."""

    def __init__(self, runtime: ApplicationRuntime) -> None:
        super().__init__()
        self._runtime = runtime
        self._orchestrator: ScanOrchestrator | None = None
        self._plan: TaskPlan | None = None
        self._confirmation: ConfirmationRequest | None = None
        self._worker: ScanWorker | None = None
        self._tray: SystemTrayController | None = None
        self._quitting = False
        self._last_process_reference: tuple[int, str] | None = None
        self._last_service_reference: tuple[str, str] | None = None
        self.setWindowTitle("Windows PC Manager Agent — Stage 4C1 服务安全管理")
        self.resize(1_080, 720)
        self._tabs = QTabWidget()
        self.setCentralWidget(self._tabs)
        self._build_chat_tab()
        self._build_analysis_tab()
        self._build_operation_tab()
        self._build_trash_tab()
        self._build_system_diagnostics_tab()
        self._build_startup_management_tab()
        self._build_service_management_tab()
        self._build_scan_tab()
        self._build_audit_tab()
        self._build_settings_tab()
        self.statusBar().showMessage("就绪：写操作默认不执行，必须先 Preview 并确认")

    def attach_tray(self, tray: SystemTrayController) -> None:
        """Attach tray presentation after both objects are constructed."""
        self._tray = tray

    def _build_chat_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        self._conversation = QTextBrowser()
        self._conversation.setPlainText(
            "Agent：当前支持阶段 1 只读分析、Stage 2A 安全移动/重命名/回滚，"
            "Stage 2B 双确认回收站、Stage 3 只读系统诊断和 Stage 4A 受控进程关闭。\n"
            "聊天不会直接执行系统操作；所有写操作都要经过真实 Preview 和明确确认。"
        )
        input_row = QHBoxLayout()
        self._chat_input = QLineEdit()
        self._chat_input.setPlaceholderText("输入文件分析、系统诊断或明确的进程关闭目标")
        send_button = QPushButton("发送")
        send_button.clicked.connect(self._handle_chat)
        self._chat_input.returnPressed.connect(self._handle_chat)
        input_row.addWidget(self._chat_input)
        input_row.addWidget(send_button)
        layout.addWidget(self._conversation)
        layout.addLayout(input_row)
        self._tabs.addTab(page, "聊天")

    def _build_scan_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        directory_row = QHBoxLayout()
        self._root_input = QLineEdit()
        self._root_input.setPlaceholderText("选择一个明确授权的扫描目录")
        browse_button = QPushButton("选择目录")
        plan_button = QPushButton("生成安全计划")
        browse_button.clicked.connect(self._choose_directory)
        plan_button.clicked.connect(self._prepare_plan)
        directory_row.addWidget(self._root_input)
        directory_row.addWidget(browse_button)
        directory_row.addWidget(plan_button)

        self._risk_label = QLabel("风险：尚未生成计划")
        self._risk_label.setStyleSheet("font-weight: 600;")
        self._plan_view = QTextBrowser()
        self._plan_view.setPlaceholderText("结构化计划、扫描范围和排除范围会显示在这里。")
        action_row = QHBoxLayout()
        self._confirm_button = QPushButton("确认计划")
        self._reject_button = QPushButton("拒绝计划")
        self._scan_button = QPushButton("开始只读扫描")
        self._cancel_button = QPushButton("取消扫描")
        self._confirm_button.setEnabled(False)
        self._reject_button.setEnabled(False)
        self._scan_button.setEnabled(False)
        self._cancel_button.setEnabled(False)
        self._confirm_button.clicked.connect(self._approve_plan)
        self._reject_button.clicked.connect(self._reject_plan)
        self._scan_button.clicked.connect(self._start_scan)
        self._cancel_button.clicked.connect(self._cancel_scan)
        for button in (
            self._confirm_button,
            self._reject_button,
            self._scan_button,
            self._cancel_button,
        ):
            action_row.addWidget(button)

        self._progress = QProgressBar()
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        self._results = QTableWidget(0, 6)
        self._results.setHorizontalHeaderLabels(
            ("文件名", "扩展名", "类型", "大小（字节）", "修改时间（UTC）", "完整路径")
        )
        self._results.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._results.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._results.setSortingEnabled(True)
        self._results.horizontalHeader().setStretchLastSection(True)

        layout.addLayout(directory_row)
        layout.addWidget(self._risk_label)
        layout.addWidget(self._plan_view, 2)
        layout.addLayout(action_row)
        layout.addWidget(self._progress)
        layout.addWidget(self._results, 3)
        self._tabs.addTab(page, "只读扫描")

    def _build_analysis_tab(self) -> None:
        """Attach the formal Stage 1 workflow as an independent UI controller."""
        self._analysis_tab = FileAnalysisTab(self._runtime)
        self._analysis_tab.status_message.connect(self.statusBar().showMessage)
        self._tabs.addTab(self._analysis_tab, "文件分析")

    def _build_operation_tab(self) -> None:
        """Attach Stage 2A Preview, confirmed execution, transaction, and rollback UI."""
        self._operation_tab = FileOperationTab(self._runtime)
        self._operation_tab.status_message.connect(self.statusBar().showMessage)
        self._analysis_tab.move_selected_requested.connect(self._open_move_for_paths)
        self._analysis_tab.rename_selected_requested.connect(self._open_rename_for_paths)
        self._tabs.addTab(self._operation_tab, "安全文件操作")

    def _build_trash_tab(self) -> None:
        """Attach the independent R2 Preview and two-confirmation workflow."""
        self._trash_tab = TrashTab(self._runtime)
        self._trash_tab.status_message.connect(self.statusBar().showMessage)
        self._analysis_tab.trash_selected_requested.connect(self._open_trash_for_paths)
        self._tabs.addTab(self._trash_tab, "Windows 回收站")

    def _build_system_diagnostics_tab(self) -> None:
        """Attach the independent Stage 3 read-only diagnostic dashboard."""
        self._system_diagnostics_tab = SystemDiagnosticsTab(self._runtime)
        self._system_diagnostics_tab.status_message.connect(self.statusBar().showMessage)
        self._system_diagnostics_tab.process_reference_changed.connect(
            self._remember_process_reference
        )
        self._tabs.addTab(self._system_diagnostics_tab, "系统诊断")

    def _build_startup_management_tab(self) -> None:
        """Attach current-user startup inventory, Preview, confirmation, and restore UI."""
        self._startup_management_tab = StartupManagementTab(self._runtime)
        self._startup_management_tab.status_message.connect(self.statusBar().showMessage)
        self._tabs.addTab(self._startup_management_tab, "启动项管理")

    def _build_service_management_tab(self) -> None:
        """Attach protected service inventory and exact Stage 4C1 action workflow."""
        self._service_management_tab = ServiceManagementTab(self._runtime)
        self._service_management_tab.status_message.connect(self.statusBar().showMessage)
        self._service_management_tab.service_reference_changed.connect(
            self._remember_service_reference
        )
        self._tabs.addTab(self._service_management_tab, "服务管理")

    def _build_audit_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        refresh = QPushButton("刷新审计记录")
        refresh.clicked.connect(self._refresh_audit)
        self._audit_table = QTableWidget(0, 6)
        self._audit_table.setHorizontalHeaderLabels(
            ("时间", "事件", "风险", "工具", "确认", "计划 ID")
        )
        self._audit_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._audit_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(refresh)
        layout.addWidget(self._audit_table)
        self._tabs.addTab(page, "审计")

    def _build_settings_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        provider = self._runtime.settings.llm_provider
        model = self._runtime.settings.openai_model or "未设置"
        layout.addWidget(QLabel(f"模型供应商：{provider}"))
        layout.addWidget(QLabel(f"模型 ID：{model}"))
        layout.addWidget(QLabel("API Key：仅从环境变量读取，界面和日志不会显示"))
        layout.addWidget(QLabel(f"本地数据目录：{self._runtime.settings.data_directory}"))
        layout.addWidget(QLabel(f"扫描文件上限：{self._runtime.settings.scan_max_files}"))
        layout.addWidget(
            QLabel("Stage 2A 仅支持已授权目录内的同卷移动、同父重命名、mkdir 和回滚。")
        )
        layout.addWidget(QLabel("Stage 2B 仅支持双确认后移入 Windows 回收站；恢复能力为 MANUAL。"))
        layout.addWidget(
            QLabel(
                "Stage 4A 仅关闭当前用户普通进程；双确认、无提权、回滚 NONE，"
                "强制终止必须是全新流程。"
            )
        )
        layout.addWidget(QLabel("不覆盖、不跨卷、不永久删除、不修改服务/启动项/注册表。"))
        layout.addStretch(1)
        self._tabs.addTab(page, "设置")

    @Slot()
    def _handle_chat(self) -> None:
        text = self._chat_input.text().strip()
        if not text:
            return
        self._conversation.append(f"你：{text}")
        self._chat_input.clear()
        service_intent_value = service_action_intent(text)
        if service_intent_value is not None:
            self._tabs.setCurrentWidget(self._service_management_tab)
            try:
                target_query = service_target_query(text)
            except ValueError as exc:
                if _references_previous_service(text) and self._last_service_reference is not None:
                    target_query, display_name = self._last_service_reference
                else:
                    self._conversation.append(f"Agent：未执行。{exc}")
                    return
            else:
                display_name = target_query
            self._conversation.append(
                "Agent：正在用当前 SCM 清单本地解析目标并生成 Preview。"
                "显示名称只是查询提示；执行前仍会绑定唯一 service name 并完成两次确认。"
            )
            self._service_management_tab.open_action_request(
                service_intent_value,
                target_query,
                display_name=display_name,
            )
            return
        if is_process_action_request(text):
            self._tabs.setCurrentWidget(self._system_diagnostics_tab)
            query: ProcessTargetQuery | None = None
            if _references_previous_process(text) and self._last_process_reference is not None:
                pid, name = self._last_process_reference
                query = ProcessTargetQuery(
                    query_type=ProcessTargetQueryType.SELECTED_PROCESS,
                    pid=pid,
                    include_application_group=True,
                )
                self._conversation.append(
                    f"Agent：已将“它”绑定到最近明确显示或选择的 {name}（PID {pid}）。"
                    "现在只生成实时 Preview；不会直接关闭进程。"
                )
            else:
                try:
                    process_target_query(text)
                except ValueError as exc:
                    if is_diagnostic_request(text):
                        self._conversation.append(
                            "Agent：这句话同时包含诊断和关闭意图，但目前没有唯一目标。"
                            "请先运行进程诊断并选中一行，再点击“审查选中进程的关闭选项”。"
                        )
                    else:
                        self._conversation.append(f"Agent：未执行。{exc}")
                    return
                self._conversation.append(
                    "Agent：正在本地解析具体进程并生成 Stage 4A Preview。"
                    "必须完成计划确认和即时确认才可能执行。"
                )
            self._system_diagnostics_tab.open_process_action(text, query=query)
            return
        if is_diagnostic_request(text):
            self._tabs.setCurrentWidget(self._system_diagnostics_tab)
            self._conversation.append(
                "Agent：已转到系统诊断。将先展示 R0 只读计划，确认后才查询；"
                "不会终止进程、修改服务/启动项、卸载软件或请求管理员权限。"
            )
            self._system_diagnostics_tab.start_planning(text)
            return
        trash_intent = classify_trash_intent(text)
        if trash_intent is TrashIntentDecision.PROHIBITED_PERMANENT_DELETE:
            reason = "R4/MVP prohibits permanent deletion, Recycle Bin bypass, and emptying"
            try:
                self._runtime.audit_prohibited_request(text, reason)
            except AuditUnavailableError:
                self.statusBar().showMessage(
                    "永久删除请求已拒绝；审计数据库不可用，请停止写操作并检查本地数据"
                )
            self._conversation.append(
                "Agent：已拒绝永久删除、跳过或清空回收站请求。Stage 2B 只允许将你明确选择的对象"
                "移入 Windows 回收站，并要求两次确认。"
            )
            self.statusBar().showMessage("R4/MVP 禁止：永久删除能力未注册，未执行任何操作")
            return
        if trash_intent is TrashIntentDecision.RECYCLE_BIN:
            self._tabs.setCurrentWidget(self._trash_tab)
            self._conversation.append(
                "Agent：已转到 Windows 回收站页面。模型不会选择对象；请在文件分析结果中勾选，"
                "或在该页面手动添加对象，然后生成 R2 Preview 并完成两次确认。"
            )
            return
        operation_terms = ("移动", "重命名", "改名", "整理", "撤销", "回滚")
        if any(term in text for term in operation_terms):
            self._tabs.setCurrentWidget(self._operation_tab)
            if any(term in text for term in ("撤销", "回滚")):
                self._conversation.append(
                    "Agent：已转到“安全文件操作”历史页。请选择具体事务并生成回滚 Preview；"
                    "系统不会让模型猜测反向路径。"
                )
                self._operation_tab.refresh_history()
            else:
                self._conversation.append(
                    "Agent：已转到 Stage 2A。模型只生成受限意图；具体路径由本地代码计算，"
                    "写操作必须经过真实 Preview 和明确确认。"
                )
                self._operation_tab.start_planning(text)
            return
        self._analysis_tab.goal_input.setText(text)
        self._tabs.setCurrentWidget(self._analysis_tab)
        if not self._runtime.authorized_paths.list_authorized():
            self._conversation.append(
                "Agent：尚未授权扫描目录，因此不会把聊天内容发送给模型，"
                "也不会读取文件。请先在“文件分析”页添加授权目录。"
            )
            return
        self._conversation.append(
            "Agent：已转到文件分析页。任何模型调用都会先显示外部数据确认，"
            "扫描仍需结构化计划、安全审查和计划确认。"
        )
        self._analysis_tab.start_planning(text)

    @Slot(object)
    def _open_move_for_paths(self, value: object) -> None:
        paths = (
            tuple(path for path in value if isinstance(path, Path))
            if isinstance(value, tuple)
            else ()
        )
        self._operation_tab.set_sources(paths)
        self._tabs.setCurrentWidget(self._operation_tab)
        self.statusBar().showMessage("已传入勾选结果；请选择目标并生成移动 Preview")

    @Slot(object)
    def _open_rename_for_paths(self, value: object) -> None:
        paths = (
            tuple(path for path in value if isinstance(path, Path))
            if isinstance(value, tuple)
            else ()
        )
        self._operation_tab.set_sources(paths)
        self._tabs.setCurrentWidget(self._operation_tab)
        self.statusBar().showMessage("已传入勾选结果；选择有限规则并生成重命名 Preview")

    @Slot(object)
    def _open_trash_for_paths(self, value: object) -> None:
        """Transfer only explicitly checked analysis paths into the R2 page."""
        paths = (
            tuple(path for path in value if isinstance(path, Path))
            if isinstance(value, tuple)
            else ()
        )
        self._trash_tab.set_sources(paths)
        self._tabs.setCurrentWidget(self._trash_tab)
        self.statusBar().showMessage("已传入明确勾选对象；请生成 R2 Preview 并完成两次确认")

    @Slot(int, str)
    def _remember_process_reference(self, pid: int, name: str) -> None:
        self._last_process_reference = (pid, name)

    @Slot(str, str)
    def _remember_service_reference(self, service_name: str, display_name: str) -> None:
        self._last_service_reference = (service_name, display_name)

    @Slot()
    def _choose_directory(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "选择允许扫描的目录")
        if selected:
            self._root_input.setText(selected)

    @Slot()
    def _prepare_plan(self) -> None:
        root_text = self._root_input.text().strip()
        if not root_text:
            self._show_error("请先选择一个扫描目录。")
            return
        try:
            orchestrator = self._runtime.create_scan_orchestrator(Path(root_text))
            plan, review = orchestrator.prepare_plan(Path(root_text))
            if not review.approved:
                details = "\n".join(issue.message for issue in review.issues)
                raise RuntimeError(f"安全审查拒绝计划：\n{details}")
            confirmation = orchestrator.request_plan_confirmation(plan)
        except Exception as exc:
            self._show_error(str(exc))
            return
        self._orchestrator = orchestrator
        self._plan = plan
        self._confirmation = confirmation
        self._plan_view.setPlainText(plan.model_dump_json(indent=2))
        self._risk_label.setText("风险：R0 只读；修改文件 0；删除文件 0；回滚等级 NONE（无需回滚）")
        self._confirm_button.setEnabled(True)
        self._reject_button.setEnabled(True)
        self._scan_button.setEnabled(False)
        self.statusBar().showMessage("计划已通过安全审查，等待你的明确确认")

    @Slot()
    def _approve_plan(self) -> None:
        if not self._orchestrator or not self._plan or not self._confirmation:
            self._show_error("没有待确认的计划。")
            return
        try:
            self._orchestrator.resolve_plan_confirmation(
                self._confirmation.confirmation_id,
                True,
                self._plan,
            )
        except Exception as exc:
            self._show_error(str(exc))
            return
        self._confirm_button.setEnabled(False)
        self._reject_button.setEnabled(False)
        self._scan_button.setEnabled(True)
        self.statusBar().showMessage("计划已确认；可以开始 R0 只读扫描")

    @Slot()
    def _reject_plan(self) -> None:
        if not self._orchestrator or not self._plan or not self._confirmation:
            return
        try:
            self._orchestrator.resolve_plan_confirmation(
                self._confirmation.confirmation_id,
                False,
                self._plan,
            )
        except Exception as exc:
            self._show_error(str(exc))
            return
        self._confirm_button.setEnabled(False)
        self._reject_button.setEnabled(False)
        self._scan_button.setEnabled(False)
        self.statusBar().showMessage("计划已拒绝；未执行扫描")

    @Slot()
    def _start_scan(self) -> None:
        if not self._orchestrator or not self._plan or self._worker is not None:
            return
        worker = ScanWorker(self._orchestrator, self._plan)
        worker.signals.completed.connect(self._scan_completed)
        worker.signals.failed.connect(self._scan_failed)
        self._worker = worker
        self._scan_button.setEnabled(False)
        self._cancel_button.setEnabled(True)
        self._progress.setRange(0, 0)
        self.statusBar().showMessage("正在执行只读扫描……")
        QThreadPool.globalInstance().start(worker)

    @Slot()
    def _cancel_scan(self) -> None:
        if self._worker:
            self._worker.cancel()
            self.statusBar().showMessage("已请求取消，正在等待当前元数据读取结束……")

    @Slot(object)
    def _scan_completed(self, value: object) -> None:
        try:
            report = require_scan_report(value)
        except TypeError as exc:
            self._scan_failed(str(exc))
            return
        self._worker = None
        self._cancel_button.setEnabled(False)
        self._scan_button.setEnabled(True)
        self._progress.setRange(0, 1)
        self._progress.setValue(1)
        self._populate_results(report)
        summary = report.summary
        state = "已取消" if summary.cancelled else "完成"
        self.statusBar().showMessage(
            f"{state}：{summary.files_seen} 个文件，{summary.total_size_bytes} 字节，"
            f"{summary.issues} 个跳过/错误"
        )
        self._refresh_audit()

    @Slot(str)
    def _scan_failed(self, message: str) -> None:
        self._worker = None
        self._cancel_button.setEnabled(False)
        self._scan_button.setEnabled(True)
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        self._show_error(f"扫描失败：{message}")

    def _populate_results(self, report: ScanReport) -> None:
        self._results.setSortingEnabled(False)
        self._results.setRowCount(len(report.files))
        for row, metadata in enumerate(report.files):
            values = (
                metadata.name,
                metadata.extension,
                metadata.media_type or "未知",
                str(metadata.size_bytes),
                metadata.modified_at.isoformat(),
                str(metadata.path),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 3:
                    item.setData(Qt.ItemDataRole.UserRole, metadata.size_bytes)
                self._results.setItem(row, column, item)
        self._results.setSortingEnabled(True)
        self._results.resizeColumnsToContents()

    @Slot()
    def _refresh_audit(self) -> None:
        try:
            rows = self._runtime.audit.list_recent(100)
        except Exception as exc:
            self._show_error(f"无法读取审计记录：{exc}")
            return
        self._audit_table.setRowCount(len(rows))
        for row_index, event in enumerate(rows):
            values = (
                event.occurred_at.isoformat(),
                event.event_type,
                event.risk_level or "",
                event.tool_name or "",
                event.confirmation_result or "",
                event.plan_id or "",
            )
            for column, value in enumerate(values):
                self._audit_table.setItem(row_index, column, QTableWidgetItem(value))

    def request_quit(self) -> None:
        """Cancel work, hide tray, and close the window for application shutdown."""
        self._quitting = True
        self.shutdown()
        if self._tray:
            self._tray.hide()
        self.close()

    def shutdown(self) -> None:
        """Request cancellation and wait a bounded time for workers."""
        self._analysis_tab.shutdown()
        self._operation_tab.shutdown()
        self._trash_tab.shutdown()
        self._system_diagnostics_tab.shutdown()
        self._startup_management_tab.shutdown()
        self._service_management_tab.shutdown()
        if self._worker:
            self._worker.cancel()
        # A dispatched SCM request cannot be force-cancelled safely.  Allow the
        # configured 30-second service timeout plus a small cleanup margin so
        # the database is not closed while a service worker is still auditing.
        QThreadPool.globalInstance().waitForDone(35_000)

    def closeEvent(self, event: QCloseEvent) -> None:
        """Hide to tray unless a controlled application exit is in progress."""
        if not self._quitting and self._tray and self._tray.is_available:
            event.ignore()
            self.hide()
            self.statusBar().showMessage("应用仍在托盘运行")
            return
        if not self._quitting:
            self.shutdown()
        event.accept()

    def _show_error(self, message: str) -> None:
        QMessageBox.warning(self, "操作未执行", message)
        self.statusBar().showMessage(message)


def _references_previous_process(text: str) -> bool:
    normalized = text.casefold()
    return any(
        marker in normalized
        for marker in ("它", "这个进程", "选中的", "that process", "close it", "kill it")
    )


def _references_previous_service(text: str) -> bool:
    normalized = text.casefold()
    return any(
        marker in normalized
        for marker in (
            "这个服务",
            "那个服务",
            "选中的服务",
            "restart it",
            "stop it",
            "start it",
        )
    )
