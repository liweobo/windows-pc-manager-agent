"""Main PySide6 window; all operations are delegated to orchestration services."""

from __future__ import annotations

from datetime import UTC, datetime
from html import escape
from pathlib import Path
from uuid import UUID, uuid4

from PySide6.QtCore import QEvent, QObject, Qt, QThreadPool, Slot
from PySide6.QtGui import QCloseEvent, QHideEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QDockWidget,
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
from pc_manager_agent.app.voice import audio_process_is_elevated, build_voice_services
from pc_manager_agent.audit.repository import AuditUnavailableError
from pc_manager_agent.confirmation.models import ConfirmationRequest
from pc_manager_agent.domain.optimization_receipts import OptimizationTransactionReference
from pc_manager_agent.domain.plans import TaskPlan
from pc_manager_agent.domain.process_actions import (
    ProcessTargetQuery,
    ProcessTargetQueryType,
)
from pc_manager_agent.domain.reports import ScanReport
from pc_manager_agent.domain.software_uninstall_analysis import SoftwareTargetQuery
from pc_manager_agent.domain.user_requests import (
    RequestChannel,
    RequestDomain,
    RequestRoute,
    UserRequest,
    VoiceInteractionContext,
)
from pc_manager_agent.orchestration.process_action_planner import (
    process_target_query,
)
from pc_manager_agent.orchestration.service import ScanOrchestrator
from pc_manager_agent.orchestration.service_action_planner import (
    service_action_intent,
    service_target_query,
)
from pc_manager_agent.orchestration.software_uninstall_analysis import (
    extract_software_target_name,
)
from pc_manager_agent.orchestration.system_diagnostic_planner import is_diagnostic_request
from pc_manager_agent.orchestration.trash_planner import TrashIntentDecision, classify_trash_intent
from pc_manager_agent.orchestration.user_requests import (
    UserRequestDispatcher,
    diagnostic_preparation_goal,
    optimization_preparation_goal,
)
from pc_manager_agent.ui.analysis_tab import FileAnalysisTab
from pc_manager_agent.ui.domain_review_events import ObservedDomainDialog
from pc_manager_agent.ui.office_tab import OfficeTab
from pc_manager_agent.ui.operation_tab import FileOperationTab
from pc_manager_agent.ui.service_action_dialog import ServiceActionDialog
from pc_manager_agent.ui.service_management_tab import ServiceManagementTab
from pc_manager_agent.ui.service_startup_dialog import ServiceStartupActionDialog
from pc_manager_agent.ui.stage4x3_action_dialog import Stage4X3ActionDialog
from pc_manager_agent.ui.startup_management_tab import StartupManagementTab
from pc_manager_agent.ui.system_diagnostics_tab import SystemDiagnosticsTab
from pc_manager_agent.ui.system_optimization_tab import SystemOptimizationTab
from pc_manager_agent.ui.system_tray import SystemTrayController
from pc_manager_agent.ui.trash_tab import TrashTab
from pc_manager_agent.ui.voice_audio import QtAudioCapture, QtAudioPlayback
from pc_manager_agent.ui.voice_controller import VoiceUiController
from pc_manager_agent.ui.voice_controls import VoiceControls
from pc_manager_agent.ui.workers import ScanWorker, require_scan_report
from pc_manager_agent.voice.audio import MicrophonePermissionService
from pc_manager_agent.voice.results import VoiceResultSummaryService
from pc_manager_agent.voice.speech import SpeechOutcome, SpeechSummaryFacts


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
        self._request_dispatcher = UserRequestDispatcher()
        self._voice: VoiceUiController | None = None
        self._voice_context_id = uuid4()
        self._voice_dialogs: dict[UUID, QDialog] = {}
        self._voice_receipts: dict[UUID, OptimizationTransactionReference] = {}
        self._voice_results = VoiceResultSummaryService(
            runtime.optimization_result_reader.read, datetime.now(UTC)
        )
        self.setWindowTitle("Windows PC Manager Agent — Stage 5B 受控语音交互")
        self.resize(1_080, 720)
        self._tabs = QTabWidget()
        self.setCentralWidget(self._tabs)
        self._build_chat_tab()
        self._build_analysis_tab()
        self._build_operation_tab()
        self._build_trash_tab()
        self._build_system_diagnostics_tab()
        self._build_system_optimization_tab()
        self._build_startup_management_tab()
        self._build_service_management_tab()
        self._build_scan_tab()
        self._build_audit_tab()
        self._build_settings_tab()
        self._office_tab = OfficeTab(runtime.office)
        self._tabs.addTab(self._office_tab, "办公文档")
        self._build_voice()
        self._tabs.currentChanged.connect(self._voice_surface_changed)
        self.statusBar().showMessage("就绪：写操作默认不执行，必须先 Preview 并确认")

    def attach_tray(self, tray: SystemTrayController) -> None:
        """Attach tray presentation after both objects are constructed."""
        self._tray = tray

    def _build_chat_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        self._conversation = QTextBrowser()
        self._conversation.setPlainText(
            "Agent：Stage 4E1 已增加严格只读的空间、性能和优化建议分析。\n"
            "系统优化分析不会清理、加速、修复或应用设置；结果也不授予未来写权限。\n"
            "聊天不会直接执行系统操作；现有写操作仍要经过真实 Preview 和明确确认。"
        )
        input_row = QHBoxLayout()
        self._chat_input = QLineEdit()
        self._chat_input.setMaxLength(4000)
        self._chat_input.setPlaceholderText(
            "输入文件分析、系统诊断、系统优化分析或一个明确的软件卸载分析目标"
        )
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

    def _build_system_optimization_tab(self) -> None:
        """Attach the isolated Stage 4E1 read-only optimization dashboard."""
        self._system_optimization_tab = SystemOptimizationTab(self._runtime)
        self._system_optimization_tab.status_message.connect(self.statusBar().showMessage)
        self._tabs.addTab(self._system_optimization_tab, "系统优化分析")

    def _build_startup_management_tab(self) -> None:
        """Attach current-user startup inventory, Preview, confirmation, and restore UI."""
        self._startup_management_tab = StartupManagementTab(self._runtime)
        self._startup_management_tab.status_message.connect(self.statusBar().showMessage)
        self._tabs.addTab(self._startup_management_tab, "启动项管理")

    def _build_service_management_tab(self) -> None:
        """Attach Stage 4C1 state control and Stage 4C2 startup configuration workflows."""
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
        layout.addWidget(
            QLabel(
                "Stage 4D2B 仅支持双确认后的单个 current-user MSI 或高可信厂商卸载器；"
                "不直接执行原始 UninstallString，不自动提权或删除残留。"
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
        self._chat_input.clear()
        request = UserRequest(channel=RequestChannel.TEXT, text=text)
        self._route_request(request, self._request_dispatcher.route(request))

    def _dispatch_domain(self, request: UserRequest, route: RequestRoute) -> None:
        """Enter existing domain preparation only; all confirmations remain domain-owned."""
        text = request.text
        voice = request.channel is not RequestChannel.TEXT
        domain = route.domain
        self._conversation.append(f"你：{escape(text)}")
        if domain is RequestDomain.OFFICE:
            self._tabs.setCurrentWidget(self._office_tab)
            self._office_tab.set_user_goal(text)
            self._conversation.append(
                "Agent：已转到办公文档。请明确选择文件；读取、模型发送和编辑各自确认。"
                "不会执行宏、脚本或自动控制 Word/Excel。"
            )
            return
        if domain is RequestDomain.SOFTWARE:
            self._tabs.setCurrentWidget(self._system_diagnostics_tab)
            target_name = extract_software_target_name(text)
            if target_name is None:
                self._conversation.append(
                    "Agent：未执行。请明确写出要卸载的软件名称，系统不会按模糊目标自动选择。"
                )
                return
            self._conversation.append(
                "Agent：将先用本地只读元数据区分 MSI 与厂商卸载器，再进入各自独立的 "
                "双确认流程；不会执行原始 UninstallString。"
            )
            self._system_diagnostics_tab.open_routed_uninstall(
                "审查软件卸载能力" if voice else text,
                query=SoftwareTargetQuery(display_name=target_name),
            )
            return
        service_intent_value = service_action_intent(text)
        if domain is RequestDomain.SERVICE and service_intent_value is not None:
            self._tabs.setCurrentWidget(self._service_management_tab)
            try:
                target_query = service_target_query(text)
            except ValueError as exc:
                if (
                    not voice
                    and _references_previous_service(text)
                    and self._last_service_reference is not None
                ):
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
        if domain is RequestDomain.OPTIMIZATION:
            self._tabs.setCurrentWidget(self._system_optimization_tab)
            self._system_optimization_tab.goal_input.setText(
                optimization_preparation_goal(text) if voice else text
            )
            self._conversation.append(
                "Agent：已转到 Stage 4E1 系统优化分析。这里仅生成 R0 计划、空间候选、"
                "性能发现和建议，不提供一键清理、Boost、Fix 或 Apply。"
            )
            self._system_optimization_tab.prepare()
            return
        if domain is RequestDomain.PROCESS:
            self._tabs.setCurrentWidget(self._system_diagnostics_tab)
            query: ProcessTargetQuery | None = None
            if (
                not voice
                and _references_previous_process(text)
                and self._last_process_reference is not None
            ):
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
                    parsed_query = process_target_query(text)
                    if voice:
                        query = parsed_query
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
            self._system_diagnostics_tab.open_process_action(
                "审查进程正常退出" if voice else text, query=query
            )
            return
        if domain is RequestDomain.DIAGNOSTICS:
            self._tabs.setCurrentWidget(self._system_diagnostics_tab)
            self._conversation.append(
                "Agent：已转到系统诊断。将先展示 R0 只读计划，确认后才查询；"
                "不会终止进程、修改服务/启动项、卸载软件或请求管理员权限。"
            )
            self._system_diagnostics_tab.start_planning(
                diagnostic_preparation_goal(text) if voice else text
            )
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
        if domain is RequestDomain.FILE_OPERATIONS:
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
                if voice:
                    self._conversation.append(
                        "Agent：请在页面选择确切文件和规则；语音不会授权路径。"
                    )
                else:
                    self._operation_tab.start_planning(text)
            return
        if domain is not RequestDomain.FILES:
            self._conversation.append("Agent：请在对应页面明确目标和操作，尚未执行。")
            return
        if voice:
            text = "只读分析大文件、疑似闲置文件和重复候选"
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

    def _build_voice(self) -> None:
        """Compose exactly one microphone owner without opening or probing any device."""
        try:
            services = build_voice_services(
                self._runtime.settings.data_directory, self._runtime.audit
            )
            capture = QtAudioCapture(services.coordinator.settings, self)
            playback = QtAudioPlayback(self)
            self._voice = VoiceUiController(
                services,
                capture,
                playback,
                MicrophonePermissionService(audio_process_is_elevated),
                self,
            )
        except Exception:
            self.statusBar().showMessage("语音配置或日志不可用；已停用语音，请使用文字输入。")
            return
        capture.failed.connect(self._voice.hardware_error)
        capture.progress.connect(self._voice.capture_progress)
        playback.failed.connect(self._voice.hardware_error)
        playback.finished.connect(self._voice.playback_finished)
        self._voice.request_ready.connect(self._route_request)
        dock = QDockWidget("语音输入：默认不录音、不上传、不播报", self)
        dock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea)
        dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        dock.setWidget(
            VoiceControls(
                self._voice,
                self._voice_context,
                dock,
                summary=lambda: self._voice_summary(self._voice_context_id),
            )
        )
        self._operation_tab.domain_preview_ready.connect(self._voice_tab_preview)
        self._trash_tab.domain_preview_ready.connect(self._voice_tab_preview)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)
        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.installEventFilter(self)
            app.applicationStateChanged.connect(self._voice_application_state)

    def _voice_application_state(self, state: Qt.ApplicationState) -> None:
        if self._voice is not None and state != Qt.ApplicationState.ApplicationActive:
            self._voice.cancel()

    def _voice_summary(self, context_id: UUID) -> SpeechSummaryFacts:
        reference = self._voice_receipts.get(context_id)
        if reference is None:
            return SpeechSummaryFacts(outcome=SpeechOutcome.UNVERIFIED)
        return self._voice_results.summarize(reference)

    def _voice_tab_preview(self, value: object) -> None:
        if isinstance(value, OptimizationTransactionReference):
            self._voice_receipts[self._voice_context_id] = value

    def _voice_surface_changed(self, index: int) -> None:
        self._voice_context_id = uuid4()

    def _voice_context(self) -> VoiceInteractionContext:
        surfaces = {
            1: RequestDomain.FILES,
            2: RequestDomain.FILE_OPERATIONS,
            3: RequestDomain.TRASH,
            4: RequestDomain.DIAGNOSTICS,
            5: RequestDomain.OPTIMIZATION,
            6: RequestDomain.STARTUP,
            7: RequestDomain.SERVICE,
            11: RequestDomain.OFFICE,
        }
        return VoiceInteractionContext(
            active_surface=surfaces.get(self._tabs.currentIndex()),
            conversation_id=self._voice_context_id,
        )

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Mirror voice controls in existing review dialogs; no new session or authority."""
        if (
            event.type() == QEvent.Type.Show
            and self._voice is not None
            and isinstance(
                watched,
                (
                    ObservedDomainDialog,
                    ServiceActionDialog,
                    ServiceStartupActionDialog,
                    Stage4X3ActionDialog,
                ),
            )
            and self._owns_voice_dialog(watched)
            and watched not in self._voice_dialogs.values()
            and watched.layout() is not None
        ):
            reference = uuid4()
            self._voice_dialogs[reference] = watched
            controls = VoiceControls(
                self._voice,
                lambda: VoiceInteractionContext(conversation_id=reference),
                watched,
                summary=lambda: self._voice_summary(reference),
            )
            layout = watched.layout()
            if layout is not None:
                layout.addWidget(controls)
            watched.finished.connect(lambda _result: self._voice_dialogs.pop(reference, None))
            if isinstance(watched, ObservedDomainDialog):
                watched.domain_preview_ready.connect(
                    lambda value: self._voice_bind_receipt(reference, value)
                )
        return super().eventFilter(watched, event)

    def _owns_voice_dialog(self, dialog: QDialog) -> bool:
        parent = dialog.parent()
        while parent is not None:
            if parent is self:
                return True
            parent = parent.parent()
        return False

    def _voice_bind_receipt(self, context_id: UUID, value: object) -> None:
        if isinstance(value, OptimizationTransactionReference):
            self._voice_receipts[context_id] = value

    @Slot(object, object)
    def _route_request(self, request: UserRequest, route: RequestRoute) -> None:
        """Use one finite dispatcher for both channels; speech can never resolve a confirmation."""
        if route != self._request_dispatcher.route(request):
            self.statusBar().showMessage("请求路由已变化，请重新提交；尚未执行。")
            return
        voice = request.channel is not RequestChannel.TEXT
        domain = route.domain
        context_id = request.context.conversation_id
        dialog = self._voice_dialogs.get(context_id) if voice and context_id is not None else None
        if voice and not dialog and request.context.conversation_id != self._voice_context_id:
            self.statusBar().showMessage("录音时的页面已变化；请重新明确当前目标。")
            return
        if domain is RequestDomain.CONFIRMATION:
            self._conversation.append(
                "Agent：语音/聊天文字不能代替业务确认，请检查屏幕上的具体对象。"
            )
            self.statusBar().showMessage("未批准任何计划、即时确认或 UAC。")
            return
        if domain is RequestDomain.STATUS:
            self._conversation.append("Agent：请查看当前业务页的真实状态；语音不推断操作成功。")
            return
        if domain is RequestDomain.CANCEL:
            if dialog and dialog.isVisible():
                # Existing closeEvent owns cancellation, including leave-installer-alive semantics.
                dialog.close()
            elif not voice or request.context.conversation_id == self._voice_context_id:
                self._cancel_current_surface()
            self.statusBar().showMessage(
                "已请求停止当前页面的后续工作；不是撤销，不终止外部卸载器。"
            )
            return
        if domain is RequestDomain.BLOCKED:
            try:
                self._runtime.audit_prohibited_request(
                    "[redacted user request]", "REQUEST_BLOCKED_BY_INPUT_POLICY"
                )
            except AuditUnavailableError:
                self.statusBar().showMessage("请求已拒绝；审计不可用，请停止写操作并检查本地数据。")
            self._conversation.append(
                "Agent：已拒绝永久删除、敏感数据或绕过安全规则的请求；未执行。"
            )
            return
        if dialog or QApplication.activeModalWidget() is not None:
            self.statusBar().showMessage("先完成或取消当前审查；语音不能替换待确认计划。")
            return
        if domain in {RequestDomain.AMBIGUOUS, RequestDomain.UNSUPPORTED}:
            self._conversation.append(
                "Agent：目标不明确或不支持。请明确对象，并在原业务页选择；尚未执行。"
            )
            return
        if domain is RequestDomain.STARTUP:
            self._tabs.setCurrentWidget(self._startup_management_tab)
            self._conversation.append("Agent：请从当前启动项清单选择确切对象，再生成独立 Preview。")
            return
        if domain is RequestDomain.CLEANUP:
            self._tabs.setCurrentWidget(self._system_optimization_tab)
            self._conversation.append(
                "Agent：请先完成只读分析并明确勾选，再点击受控清理；不能全选或自动清理。"
            )
            return
        if domain is RequestDomain.RECYCLE_BIN_EMPTY:
            self._tabs.setCurrentWidget(self._system_optimization_tab)
            self._system_optimization_tab.open_recycle_bin_empty()
            return
        self._dispatch_domain(request, route)

    def _cancel_current_surface(self) -> None:
        current = self._tabs.currentWidget()
        if current is self._analysis_tab:
            self._analysis_tab.cancel()
        elif current is self._operation_tab:
            self._operation_tab.cancel()
        elif current is self._trash_tab:
            self._trash_tab.cancel()
        elif current is self._system_diagnostics_tab:
            self._system_diagnostics_tab.cancel()
        elif current is self._system_optimization_tab:
            self._system_optimization_tab.cancel()
        elif current is self._office_tab:
            self._office_tab.cancel_current_work()

    def hideEvent(self, event: QHideEvent) -> None:
        """Stop audio before hiding to tray; no background or invisible microphone session."""
        if self._voice is not None:
            self._voice.cancel()
        super().hideEvent(event)

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

    def request_quit(self) -> bool:
        """Cancel work, hide tray, and close the window for application shutdown."""
        if not self.shutdown():
            self.statusBar().showMessage("办公任务正在安全退出，请稍后再次退出；不会强杀任务。")
            return False
        self._quitting = True
        if self._tray:
            self._tray.hide()
        self.close()
        return True

    def shutdown(self) -> bool:
        """Request cancellation and wait a bounded time for workers."""
        if self._voice is not None and not self._voice.shutdown():
            self.statusBar().showMessage("正在取消语音网络请求，请稍后再次退出。")
            return False
        if not self._office_tab.shutdown():
            return False
        self._analysis_tab.shutdown()
        self._operation_tab.shutdown()
        self._trash_tab.shutdown()
        self._system_diagnostics_tab.shutdown()
        self._system_optimization_tab.shutdown()
        self._startup_management_tab.shutdown()
        self._service_management_tab.shutdown()
        if self._worker:
            self._worker.cancel()
        # A dispatched SCM request cannot be force-cancelled safely.  Allow the
        # configured 30-second service timeout plus a small cleanup margin so
        # the database is not closed while a service worker is still auditing.
        QThreadPool.globalInstance().waitForDone(35_000)
        return True

    def closeEvent(self, event: QCloseEvent) -> None:
        """Hide to tray unless a controlled application exit is in progress."""
        if not self._quitting and self._tray and self._tray.is_available:
            event.ignore()
            self.hide()
            self.statusBar().showMessage("应用仍在托盘运行")
            return
        if not self._quitting and not self.shutdown():
            event.ignore()
            return
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
