"""PySide6 dashboard for confirmed Stage 3 read-only Windows diagnostics."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import Qt, QThreadPool, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
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

from pc_manager_agent.app.runtime import ApplicationRuntime, SystemDiagnosticServices
from pc_manager_agent.confirmation.models import ConfirmationRequest
from pc_manager_agent.domain.msix_uninstall import MsixTargetQuery
from pc_manager_agent.domain.process_actions import (
    ProcessTargetQuery,
    ProcessTargetQueryType,
)
from pc_manager_agent.domain.software_uninstall_analysis import SoftwareTargetQuery
from pc_manager_agent.domain.system_diagnostics import (
    DiagnosticIntent,
    DiagnosticPlan,
    DiagnosticReport,
    ProcessSnapshot,
    SoftwareArchitecture,
    SoftwareScope,
)
from pc_manager_agent.orchestration.software_uninstall_router import (
    SoftwareUninstallMechanism,
)
from pc_manager_agent.orchestration.system_diagnostic_planner import extract_software_search_term
from pc_manager_agent.ui.msix_uninstall_dialog import MsixUninstallDialog
from pc_manager_agent.ui.process_action_dialog import ProcessActionDialog
from pc_manager_agent.ui.software_analysis_dialog import SoftwareAnalysisDialog
from pc_manager_agent.ui.software_uninstall_dialog import SoftwareUninstallDialog
from pc_manager_agent.ui.software_uninstall_router_worker import (
    SoftwareUninstallRouteWorker,
    require_software_uninstall_route,
)
from pc_manager_agent.ui.stage4x3_action_dialog import Stage4X3ActionDialog
from pc_manager_agent.ui.stage4x3_workers import Stage4X3UiAction, Stage4X3UiRequest
from pc_manager_agent.ui.system_workers import DiagnosticWorker, require_diagnostic_report
from pc_manager_agent.ui.vendor_uninstall_dialog import VendorUninstallDialog
from pc_manager_agent.ui.winget_uninstall_dialog import WingetUninstallDialog


def _bytes_text(value: int) -> str:
    """Render a byte count using a compact binary unit for local presentation."""
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} TiB"


class SystemDiagnosticsTab(QWidget):
    """Present local planning, confirmation, progress, findings, and inventories."""

    status_message = Signal(str)
    process_reference_changed = Signal(int, str)
    domain_dialog_opened = Signal(object)
    report_ready = Signal(object)

    def __init__(self, runtime: ApplicationRuntime) -> None:
        super().__init__()
        self._runtime = runtime
        self._services: SystemDiagnosticServices | None = None
        self._plan: DiagnosticPlan | None = None
        self._confirmation: ConfirmationRequest | None = None
        self._report: DiagnosticReport | None = None
        self._worker: DiagnosticWorker | None = None
        self._process_dialogs: set[ProcessActionDialog] = set()
        self._software_dialogs: set[SoftwareAnalysisDialog] = set()
        self._software_uninstall_dialogs: set[SoftwareUninstallDialog] = set()
        self._vendor_uninstall_dialogs: set[VendorUninstallDialog] = set()
        self._winget_uninstall_dialogs: set[WingetUninstallDialog] = set()
        self._msix_uninstall_dialogs: set[MsixUninstallDialog] = set()
        self._stage4x3_dialogs: set[Stage4X3ActionDialog] = set()
        self._uninstall_route_workers: set[SoftwareUninstallRouteWorker] = set()
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        goal_row = QHBoxLayout()
        self.goal_input = QLineEdit()
        self.goal_input.setPlaceholderText("例如：诊断电脑为什么卡顿；查看启动项；列出已安装软件")
        plan_button = QPushButton("生成只读诊断计划")
        plan_button.clicked.connect(self._plan_clicked)
        self.goal_input.returnPressed.connect(self._plan_clicked)
        goal_row.addWidget(self.goal_input)
        goal_row.addWidget(plan_button)

        quick_row = QHBoxLayout()
        for label, goal in (
            ("系统概览", "查看电脑系统状态"),
            ("性能诊断", "诊断电脑性能和卡顿"),
            ("进程", "查看进程资源占用"),
            ("启动项", "查看开机启动项"),
            ("服务", "查看 Windows 服务列表"),
            ("软件", "查看已安装软件清单"),
        ):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, value=goal: self.start_planning(value))
            quick_row.addWidget(button)

        self.risk_label = QLabel(
            "尚未生成计划。Stage 3 仅查询系统状态，不请求管理员权限，不修改系统。"
        )
        self.plan_view = QTextBrowser()
        self.plan_view.setMaximumHeight(180)
        self.plan_view.setPlaceholderText("确认前会在这里显示收集器、采样参数和预计影响。")

        action_row = QHBoxLayout()
        self.confirm_button = QPushButton("确认 R0 计划")
        self.reject_button = QPushButton("拒绝")
        self.run_button = QPushButton("开始只读诊断")
        self.cancel_button = QPushButton("取消")
        for button in (
            self.confirm_button,
            self.reject_button,
            self.run_button,
            self.cancel_button,
        ):
            button.setEnabled(False)
            action_row.addWidget(button)
        self.confirm_button.clicked.connect(self._approve)
        self.reject_button.clicked.connect(self._reject)
        self.run_button.clicked.connect(self._run)
        self.cancel_button.clicked.connect(self.cancel)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.summary = QTextBrowser()
        self.summary.setMaximumHeight(180)
        self.cache_label = QLabel(
            "数据时效：每次点击执行都会重新查询；当前版本不缓存启动项、服务或软件清单。"
        )

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("筛选当前结果"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入名称、状态、发布者或其他可见文字")
        self.search_input.textChanged.connect(self._filter_tables)
        filter_row.addWidget(self.search_input)

        self.results_tabs = QTabWidget()
        self.overview_table = self._table(("项目", "值"))
        self.disk_table = self._table(("挂载点", "文件系统", "已用", "可用", "使用率"))
        self.process_table = self._table(
            (
                "PID",
                "名称",
                "CPU %",
                "内存 %",
                "内存",
                "状态",
                "开始时间",
                "父 PID",
                "用户",
                "可执行路径",
                "访问完整性",
            )
        )
        self.process_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.process_table.itemSelectionChanged.connect(self._process_selection_changed)
        self.process_group_table = self._table(
            ("规范名称", "进程数", "合计 CPU %", "合计内存", "PID 列表")
        )
        self.startup_table = self._table(("名称", "来源", "范围", "命令或路径"))
        self.service_table = self._table(("服务名", "显示名", "状态", "启动类型", "账户"))
        self.software_table = self._table(
            ("名称", "版本", "发布者", "范围", "架构", "估算大小", "安装位置", "来源")
        )
        self.software_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.software_table.itemSelectionChanged.connect(self._software_selection_changed)
        self.results_tabs.addTab(self.overview_table, "概览")
        self.results_tabs.addTab(self.disk_table, "磁盘")
        self.results_tabs.addTab(self.process_table, "进程")
        self.results_tabs.addTab(self.process_group_table, "进程组")
        self.results_tabs.addTab(self.startup_table, "启动项")
        self.results_tabs.addTab(self.service_table, "服务")
        self.results_tabs.addTab(self.software_table, "软件")

        process_action_row = QHBoxLayout()
        self.process_action_label = QLabel(
            "选择一行后可生成受控关闭 Preview；选中表格不会授权任何写操作。"
        )
        self.process_action_button = QPushButton("审查选中进程的关闭选项")
        self.process_action_button.setEnabled(False)
        self.process_action_button.clicked.connect(self._open_selected_process_action)
        process_action_row.addWidget(self.process_action_label, 1)
        process_action_row.addWidget(self.process_action_button)

        software_action_row = QHBoxLayout()
        self.software_action_label = QLabel(
            "选择软件后可先分析，或分别审查 MSI 与高可信度 current-user 厂商卸载器。"
        )
        self.software_action_button = QPushButton("分析选中软件的卸载影响")
        self.software_action_button.setEnabled(False)
        self.software_action_button.clicked.connect(self._open_selected_software_analysis)
        self.software_uninstall_button = QPushButton("受控卸载选中 MSI")
        self.software_uninstall_button.setEnabled(False)
        self.software_uninstall_button.clicked.connect(self._open_selected_msi_uninstall)
        self.vendor_uninstall_button = QPushButton("受控审查选中 Vendor")
        self.vendor_uninstall_button.setEnabled(False)
        self.vendor_uninstall_button.clicked.connect(self._open_selected_vendor_uninstall)
        software_action_row.addWidget(self.software_action_label, 1)
        software_action_row.addWidget(self.software_action_button)
        software_action_row.addWidget(self.software_uninstall_button)
        software_action_row.addWidget(self.vendor_uninstall_button)

        layout.addLayout(goal_row)
        layout.addLayout(quick_row)
        layout.addWidget(self.risk_label)
        layout.addWidget(self.plan_view)
        layout.addLayout(action_row)
        layout.addWidget(self.progress)
        layout.addWidget(self.summary)
        layout.addWidget(self.cache_label)
        layout.addLayout(filter_row)
        layout.addWidget(self.results_tabs, 2)
        layout.addLayout(process_action_row)
        layout.addLayout(software_action_row)

    @staticmethod
    def _table(headers: tuple[str, ...]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSortingEnabled(True)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    @Slot()
    def _plan_clicked(self) -> None:
        self.start_planning()

    def start_planning(self, goal: str | None = None) -> None:
        """Create and review a local finite plan without reading system state."""
        if goal is not None:
            self.goal_input.setText(goal)
        user_goal = self.goal_input.text().strip()
        if not user_goal:
            self._show_error("请先输入系统诊断目标。")
            return
        try:
            services = self._runtime.create_system_diagnostic_services()
            plan, review = services.orchestrator.prepare(user_goal)
            if not review.approved:
                raise RuntimeError("；".join(issue.message for issue in review.issues))
            confirmation = services.orchestrator.request_confirmation(plan)
        except Exception as exc:
            self._show_error(str(exc))
            return
        self._services = services
        self._plan = plan
        self._confirmation = confirmation
        self.plan_view.setPlainText(plan.model_dump_json(indent=2))
        self.risk_label.setText(
            "风险 R0（只读）；系统修改 0；管理员权限：否；回滚 NONE（因为没有写操作）。"
        )
        self.confirm_button.setEnabled(True)
        self.reject_button.setEnabled(True)
        self.run_button.setEnabled(False)
        self.status_message.emit("只读计划已通过独立安全审查，等待你的明确确认")

    @Slot()
    def _approve(self) -> None:
        if self._services is None or self._plan is None or self._confirmation is None:
            return
        try:
            self._services.orchestrator.resolve_confirmation(
                self._confirmation.confirmation_id, True, self._plan
            )
        except Exception as exc:
            self._show_error(str(exc))
            return
        self.confirm_button.setEnabled(False)
        self.reject_button.setEnabled(False)
        self.run_button.setEnabled(True)
        self.status_message.emit("R0 计划已确认；可以开始读取系统状态")

    @Slot()
    def _reject(self) -> None:
        if self._services is None or self._plan is None or self._confirmation is None:
            return
        try:
            self._services.orchestrator.resolve_confirmation(
                self._confirmation.confirmation_id, False, self._plan
            )
        except Exception as exc:
            self._show_error(str(exc))
            return
        self.confirm_button.setEnabled(False)
        self.reject_button.setEnabled(False)
        self.run_button.setEnabled(False)
        self.status_message.emit("计划已拒绝；没有读取系统状态")

    @Slot()
    def _run(self) -> None:
        if self._services is None or self._plan is None or self._worker is not None:
            return
        worker = DiagnosticWorker(self._services.orchestrator, self._plan)
        worker.signals.completed.connect(self._completed)
        worker.signals.failed.connect(self._failed)
        self._worker = worker
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress.setRange(0, 0)
        self.status_message.emit("正在执行已确认的只读系统查询…")
        QThreadPool.globalInstance().start(worker)

    @Slot()
    def cancel(self) -> None:
        """Request cooperative cancellation without terminating the worker thread."""
        if self._worker is not None:
            self._worker.cancel()
            self.status_message.emit("已请求取消；正在安全结束当前采样")

    @Slot(object)
    def _completed(self, value: object) -> None:
        try:
            report = require_diagnostic_report(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        self._worker = None
        self._report = report
        self.cancel_button.setEnabled(False)
        self.run_button.setEnabled(True)
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self._populate(report)
        self.status_message.emit(report.summary)
        self.report_ready.emit(report)

    @Slot(str)
    def _failed(self, message: str) -> None:
        self._worker = None
        self.cancel_button.setEnabled(False)
        self.run_button.setEnabled(True)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self._show_error(f"系统诊断未完成：{message}")

    def _populate(self, report: DiagnosticReport) -> None:
        snapshot = report.snapshot
        finding_lines = [report.summary, report.disclaimer, "", "确定性观察："]
        finding_lines.extend(
            f"- [{finding.severity.value}] {finding.title}\n  {finding.explanation}"
            for finding in report.findings
        )
        finding_lines.append("")
        finding_lines.append(f"实际阈值：{report.thresholds.model_dump_json()}")
        self.summary.setPlainText("\n".join(finding_lines))
        self.cache_label.setText(
            f"采集时间 UTC：{snapshot.collected_at.isoformat()}；全部为本次实时查询，未使用缓存。"
        )

        overview: list[tuple[str, str]] = []
        if snapshot.system_info is not None:
            info = snapshot.system_info
            overview.extend(
                (
                    ("计算机", info.computer_name),
                    ("Windows", f"{info.windows_edition or ''} {info.windows_release}".strip()),
                    ("构建", info.windows_build),
                    ("架构", info.architecture),
                    ("处理器", info.processor_model or "不可用"),
                    ("启动时间 UTC", info.boot_time.isoformat()),
                )
            )
        if snapshot.cpu is not None:
            overview.extend(
                (
                    ("CPU 平均", f"{snapshot.cpu.average_percent:.1f}%"),
                    ("CPU 峰值", f"{snapshot.cpu.peak_percent:.1f}%"),
                )
            )
        if snapshot.memory is not None:
            overview.extend(
                (
                    ("内存使用率", f"{snapshot.memory.used_percent:.1f}%"),
                    ("可用内存", _bytes_text(snapshot.memory.available_bytes)),
                )
            )
        for outcome in snapshot.outcomes:
            overview.append(
                (
                    f"采集器 {outcome.collector.value}",
                    f"{outcome.state.value}; {outcome.item_count} 项; {outcome.duration_ms} ms",
                )
            )
        self._fill(self.overview_table, overview)
        self._fill(
            self.disk_table,
            [
                (
                    str(item.mountpoint),
                    item.filesystem or "",
                    _bytes_text(item.used_bytes),
                    _bytes_text(item.free_bytes),
                    f"{item.used_percent:.1f}%",
                )
                for item in snapshot.disks
            ],
        )
        processes = snapshot.processes.processes if snapshot.processes is not None else ()
        process_sort_key: Callable[[ProcessSnapshot], tuple[float | int, ...]] = (
            (lambda item: (item.memory_rss_bytes, item.cpu_percent, item.pid))
            if self._plan is not None and self._plan.intent is DiagnosticIntent.MEMORY
            else (lambda item: (item.cpu_percent, item.memory_rss_bytes, item.pid))
        )
        processes = tuple(
            sorted(
                processes,
                key=process_sort_key,
                reverse=True,
            )
        )
        if (
            processes
            and self._plan is not None
            and self._plan.intent in {DiagnosticIntent.CPU, DiagnosticIntent.MEMORY}
        ):
            self.process_reference_changed.emit(processes[0].pid, processes[0].name)
        self._fill(
            self.process_table,
            [
                (
                    str(item.pid),
                    item.name,
                    f"{item.cpu_percent:.1f}",
                    f"{item.memory_percent:.1f}",
                    _bytes_text(item.memory_rss_bytes),
                    item.status or "",
                    item.started_at.isoformat() if item.started_at else "",
                    str(item.parent_pid) if item.parent_pid is not None else "",
                    item.username or "",
                    str(item.executable_path) if item.executable_path else "",
                    item.access.value,
                )
                for item in processes
            ],
        )
        groups = snapshot.processes.groups if snapshot.processes is not None else ()
        self._fill(
            self.process_group_table,
            [
                (
                    item.normalized_name,
                    str(item.process_count),
                    f"{item.total_cpu_percent:.1f}",
                    _bytes_text(item.total_memory_rss_bytes),
                    ", ".join(str(pid) for pid in item.pids),
                )
                for item in sorted(
                    groups,
                    key=lambda value: (
                        value.total_memory_rss_bytes,
                        value.total_cpu_percent,
                    ),
                    reverse=True,
                )
            ],
        )
        self._fill(
            self.startup_table,
            [
                (item.name, item.source.value, item.scope.value, item.command_or_path)
                for item in snapshot.startup_entries
            ],
        )
        self._fill(
            self.service_table,
            [
                (
                    item.name,
                    item.display_name,
                    item.state,
                    item.start_type or "",
                    item.account or "",
                )
                for item in snapshot.services
            ],
        )
        self._fill(
            self.software_table,
            [
                (
                    item.name,
                    item.version or "",
                    item.publisher or "",
                    item.scope.value,
                    item.architecture.value,
                    _bytes_text(item.estimated_size_bytes)
                    if item.estimated_size_bytes is not None
                    else "",
                    str(item.install_location) if item.install_location else "",
                    "registry-uninstall-metadata",
                )
                for item in snapshot.software
            ],
        )
        if (
            not self.search_input.text()
            and self._plan is not None
            and self._plan.intent is DiagnosticIntent.SOFTWARE
        ):
            search_term = extract_software_search_term(self._plan.user_goal)
            if search_term:
                self.search_input.setText(search_term)
        self._filter_tables(self.search_input.text())

    @staticmethod
    def _fill(table: QTableWidget, rows: Sequence[tuple[str, ...]]) -> None:
        table.setSortingEnabled(False)
        table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column, text in enumerate(row):
                item = QTableWidgetItem(text)
                if column == 0 and text.isdigit():
                    item.setData(Qt.ItemDataRole.UserRole, int(text))
                table.setItem(row_index, column, item)
        table.setSortingEnabled(True)
        table.resizeColumnsToContents()

    @Slot(str)
    def _filter_tables(self, text: str) -> None:
        """Filter visible inventory rows locally without querying or mutating Windows."""
        needle = text.strip().casefold()
        for table in (
            self.disk_table,
            self.process_table,
            self.process_group_table,
            self.startup_table,
            self.service_table,
            self.software_table,
        ):
            for row in range(table.rowCount()):
                values = tuple(
                    item.text().casefold()
                    for column in range(table.columnCount())
                    if (item := table.item(row, column)) is not None
                )
                visible = not needle or any(needle in value for value in values)
                table.setRowHidden(row, not visible)

    def shutdown(self) -> None:
        """Cancel outstanding sampling during controlled application shutdown."""
        self.cancel()
        for process_dialog in tuple(self._process_dialogs):
            process_dialog.shutdown()
        for software_dialog in tuple(self._software_dialogs):
            software_dialog.shutdown()
        for uninstall_dialog in tuple(self._software_uninstall_dialogs):
            uninstall_dialog.shutdown()
        for msix_dialog in tuple(self._msix_uninstall_dialogs):
            msix_dialog.close()
        for vendor_dialog in tuple(self._vendor_uninstall_dialogs):
            vendor_dialog.shutdown()
        for privileged_dialog in tuple(self._stage4x3_dialogs):
            privileged_dialog.shutdown()
        for route_worker in tuple(self._uninstall_route_workers):
            route_worker.cancel()

    @Slot()
    def _process_selection_changed(self) -> None:
        selected = self._selected_process()
        self.process_action_button.setEnabled(selected is not None)
        if selected is not None:
            pid, name = selected
            self.process_action_label.setText(
                f"已选择 {name}（PID {pid}）；点击后会重新读取身份并进行安全分类。"
            )
            self.process_reference_changed.emit(pid, name)

    @Slot()
    def _open_selected_process_action(self) -> None:
        selected = self._selected_process()
        if selected is None:
            self._show_error("请先选择一个具体进程。")
            return
        pid, name = selected
        collection = self._report.snapshot.processes if self._report is not None else None
        matches = (
            tuple(item for item in collection.processes if item.pid == pid) if collection else ()
        )
        if len(matches) != 1 or matches[0].started_at is None or matches[0].executable_path is None:
            self._show_error("进程身份信息不完整；请刷新列表后重新选择。")
            return
        query = ProcessTargetQuery(
            query_type=ProcessTargetQueryType.SELECTED_PROCESS,
            pid=pid,
            include_application_group=True,
            expected_create_time=matches[0].started_at,
            expected_executable_path=matches[0].executable_path,
        )
        self.open_process_action(f"关闭选中的 {name}（PID {pid}）", query=query)

    def open_process_action(
        self,
        user_goal: str,
        *,
        query: ProcessTargetQuery | None = None,
    ) -> None:
        """Open a modeless Preview dialog; execution remains in orchestration workers."""
        dialog = ProcessActionDialog(self._runtime, user_goal, query=query, parent=self)
        self._process_dialogs.add(dialog)
        self.domain_dialog_opened.emit(dialog)
        dialog.finished.connect(lambda _result, value=dialog: self._process_dialogs.discard(value))
        dialog.show()
        self.status_message.emit("正在后台生成实时进程 Preview；尚未执行任何进程操作")

    @Slot()
    def _software_selection_changed(self) -> None:
        query = self._selected_software_query()
        self.software_action_button.setEnabled(query is not None)
        self.software_uninstall_button.setEnabled(query is not None)
        self.vendor_uninstall_button.setEnabled(query is not None)
        if query is not None:
            self.software_action_label.setText(
                f"已选择 {query.display_name}；点击后会重新刷新身份，表格选择本身不授权操作。"
            )

    @Slot()
    def _open_selected_software_analysis(self) -> None:
        query = self._selected_software_query()
        if query is None:
            self._show_error("请先选择一个具体软件。")
            return
        self.open_software_analysis(f"分析卸载软件 {query.display_name}", query=query)

    @Slot()
    def _open_selected_msi_uninstall(self) -> None:
        query = self._selected_software_query()
        if query is None:
            self._show_error("请先选择一个具体软件。")
            return
        self.open_msi_uninstall(f"卸载软件 {query.display_name}", query=query)

    @Slot()
    def _open_selected_vendor_uninstall(self) -> None:
        """Open Vendor analysis for exactly the selected software row."""
        query = self._selected_software_query()
        if query is None:
            self._show_error("请先选择一个具体软件。")
            return
        self.open_vendor_uninstall(f"卸载软件 {query.display_name}", query=query)

    def open_software_analysis(
        self,
        user_goal: str,
        *,
        query: SoftwareTargetQuery | None = None,
    ) -> None:
        """Open a modeless Stage 4D1 dialog whose terminal state is always STOP."""
        dialog = SoftwareAnalysisDialog(self._runtime, user_goal, query=query, parent=self)
        self._software_dialogs.add(dialog)
        dialog.finished.connect(lambda _result, value=dialog: self._software_dialogs.discard(value))
        dialog.show()
        self.status_message.emit("正在生成软件卸载分析 Preview；Stage 4D1 不执行卸载")

    def open_msi_uninstall(
        self,
        user_goal: str,
        *,
        query: SoftwareTargetQuery,
    ) -> None:
        """Open the Stage 4D2A one-product MSI workflow with cancellation as default."""
        dialog = SoftwareUninstallDialog(self._runtime, user_goal, query=query, parent=self)
        self._software_uninstall_dialogs.add(dialog)
        self.domain_dialog_opened.emit(dialog)
        dialog.finished.connect(
            lambda _result, value=dialog: self._software_uninstall_dialogs.discard(value)
        )
        dialog.show()
        self.status_message.emit("正在生成 MSI 卸载 Preview；尚未授权或启动卸载")

    def open_vendor_uninstall(
        self,
        user_goal: str,
        *,
        query: SoftwareTargetQuery,
    ) -> None:
        """Open the Stage 4D2B trusted Vendor workflow with cancellation as default."""
        dialog = VendorUninstallDialog(self._runtime, user_goal, query=query, parent=self)
        self._vendor_uninstall_dialogs.add(dialog)
        self.domain_dialog_opened.emit(dialog)
        dialog.finished.connect(
            lambda _result, value=dialog: self._vendor_uninstall_dialogs.discard(value)
        )
        dialog.show()
        self.status_message.emit("正在验证厂商卸载器可信身份；尚未授权或启动卸载")

    def open_winget_uninstall(
        self,
        user_goal: str,
        *,
        query: SoftwareTargetQuery,
    ) -> None:
        """Open the official-source Stage 4D2C1 workflow with cancellation as default."""
        dialog = WingetUninstallDialog(self._runtime, user_goal, query=query, parent=self)
        self._winget_uninstall_dialogs.add(dialog)
        self.domain_dialog_opened.emit(dialog)
        dialog.finished.connect(
            lambda _result, value=dialog: self._winget_uninstall_dialogs.discard(value)
        )
        dialog.show()
        self.status_message.emit("正在验证 winget Package 与软件身份；尚未授权或启动卸载")

    def open_msix_uninstall(
        self,
        user_goal: str,
        *,
        package_full_name: str,
    ) -> None:
        """Open the current-user Stage 4D2C2 WinRT workflow for one exact package instance."""
        dialog = MsixUninstallDialog(
            self._runtime,
            user_goal,
            query=MsixTargetQuery(full_name=package_full_name),
            parent=self,
        )
        self._msix_uninstall_dialogs.add(dialog)
        self.domain_dialog_opened.emit(dialog)
        dialog.finished.connect(
            lambda _result, value=dialog: self._msix_uninstall_dialogs.discard(value)
        )
        dialog.show()
        self.status_message.emit("正在验证 MSIX Package 类型、依赖与当前用户范围；尚未卸载")

    def open_routed_uninstall(
        self,
        user_goal: str,
        *,
        query: SoftwareTargetQuery,
    ) -> None:
        """Resolve MSI, Vendor, or winget off the UI thread and open only that workflow."""
        worker = SoftwareUninstallRouteWorker(self._runtime, query)
        self._uninstall_route_workers.add(worker)
        worker.signals.completed.connect(
            lambda value, current=worker, goal=user_goal: self._route_completed(
                current,
                goal,
                value,
            )
        )
        worker.signals.failed.connect(
            lambda message, current=worker: self._route_failed(current, message)
        )
        self.status_message.emit("正在只读识别 MSI、厂商或 winget 机制；尚未创建写操作确认")
        QThreadPool.globalInstance().start(worker)

    def _route_completed(
        self,
        worker: SoftwareUninstallRouteWorker,
        user_goal: str,
        value: object,
    ) -> None:
        """Open exactly the routed workflow or display a fail-closed explanation."""
        self._uninstall_route_workers.discard(worker)
        try:
            route = require_software_uninstall_route(value)
        except TypeError as exc:
            self._show_error(str(exc))
            return
        target = route.resolution.selected
        if target is None:
            self._show_error(route.reason)
            return
        query = SoftwareTargetQuery(identity_digest=target.identity.canonical_digest())
        if route.mechanism is SoftwareUninstallMechanism.MSI:
            if target.scope is SoftwareScope.LOCAL_MACHINE:
                self.open_machine_msi_uninstall(
                    user_goal,
                    identity_digest=target.identity.canonical_digest(),
                    display_name=target.display_name,
                )
            else:
                self.open_msi_uninstall(user_goal, query=query)
        elif route.mechanism is SoftwareUninstallMechanism.VENDOR:
            self.open_vendor_uninstall(user_goal, query=query)
        elif route.mechanism is SoftwareUninstallMechanism.WINGET:
            self.open_winget_uninstall(user_goal, query=query)
        elif route.mechanism is SoftwareUninstallMechanism.MSIX:
            package_full_name = target.identity.package_full_name
            if package_full_name is None:
                self._show_error("MSIX Package Full Name 缺失，操作已停止。")
                return
            self.open_msix_uninstall(user_goal, package_full_name=package_full_name)
        else:
            self._show_error(route.reason)

    def open_machine_msi_uninstall(
        self,
        user_goal: str,
        *,
        identity_digest: str,
        display_name: str,
    ) -> None:
        """Open the dedicated machine-MSI R3 flow; Vendor elevation remains deferred."""
        dialog = Stage4X3ActionDialog(
            self._runtime,
            Stage4X3UiRequest(
                action=Stage4X3UiAction.MSI_UNINSTALL_MACHINE,
                user_goal=user_goal,
                display_name=display_name,
                software_identity_digest=identity_digest,
            ),
            parent=self,
        )
        self._stage4x3_dialogs.add(dialog)
        dialog.finished.connect(lambda _result, value=dialog: self._stage4x3_dialogs.discard(value))
        dialog.show()
        self.status_message.emit("正在生成机器范围 MSI 的独立 R3 Preview；尚未显示 UAC 或启动卸载")

    def _route_failed(
        self,
        worker: SoftwareUninstallRouteWorker,
        message: str,
    ) -> None:
        """Remove the finished route worker and show its sanitized failure."""
        self._uninstall_route_workers.discard(worker)
        self._show_error(message)

    def _selected_software_query(self) -> SoftwareTargetQuery | None:
        rows = self.software_table.selectionModel().selectedRows()
        if len(rows) != 1:
            return None
        row = rows[0].row()
        values = tuple(self.software_table.item(row, column) for column in range(5))
        if any(item is None for item in values):
            return None
        name, version, publisher, scope, architecture = (
            item.text() for item in values if item is not None
        )
        try:
            parsed_scope = SoftwareScope(scope)
            parsed_architecture = SoftwareArchitecture(architecture)
        except ValueError:
            return None
        return SoftwareTargetQuery(
            display_name=name,
            display_version=version or None,
            publisher=publisher or None,
            scope=parsed_scope,
            architecture=parsed_architecture,
        )

    def _selected_process(self) -> tuple[int, str] | None:
        rows = self.process_table.selectionModel().selectedRows()
        if len(rows) != 1:
            return None
        row = rows[0].row()
        pid_item = self.process_table.item(row, 0)
        name_item = self.process_table.item(row, 1)
        if pid_item is None or name_item is None:
            return None
        try:
            pid = int(pid_item.text())
        except ValueError:
            return None
        return pid, name_item.text()

    def _show_error(self, message: str) -> None:
        QMessageBox.warning(self, "操作未执行", message)
        self.status_message.emit(message)
