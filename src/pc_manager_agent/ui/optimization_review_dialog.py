"""Domain-owned review UIs embedded in a sequential, non-authoritative review container."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QTabWidget, QVBoxLayout, QWidget

from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.domain.optimization_actions import (
    OptimizationActionPreparationResult,
    OptimizationUISurface,
)
from pc_manager_agent.domain.optimization_receipts import OptimizationTransactionReference
from pc_manager_agent.domain.system_cleanup_execution import SystemCleanupRequest
from pc_manager_agent.orchestration.optimization_handoffs import DomainReviewContext
from pc_manager_agent.ui.analysis_tab import FileAnalysisTab
from pc_manager_agent.ui.domain_review_events import ObservedDomainDialog
from pc_manager_agent.ui.operation_tab import FileOperationTab
from pc_manager_agent.ui.optimization_refresh_dialog import OptimizationRefreshDialog
from pc_manager_agent.ui.residual_analysis_dialog import ResidualAnalysisDialog
from pc_manager_agent.ui.startup_management_tab import StartupManagementTab
from pc_manager_agent.ui.system_cleanup_dialog import RecycleBinEmptyDialog, SystemCleanupDialog
from pc_manager_agent.ui.system_diagnostics_tab import SystemDiagnosticsTab
from pc_manager_agent.ui.trash_tab import TrashTab


class OptimizationDomainReviewDialog(QDialog):
    """Navigate to an existing flow; no tool, approval or executable is invoked here."""

    def __init__(
        self,
        runtime: ApplicationRuntime,
        session_id: UUID,
        preparation: OptimizationActionPreparationResult,
        context: DomainReviewContext,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._runtime = runtime
        self._services = runtime.create_optimization_review_services()
        self._session_id = session_id
        self._preparation = preparation
        self._context = context
        self._children: set[QDialog] = set()
        self._shutdown_callbacks: list[object] = []
        self._shutdown_requested = False
        self.setWindowTitle("复查一个优化建议 — 每项操作仍需业务确认")
        self.resize(1180, 820)
        layout = QVBoxLayout(self)
        self.status = QLabel(
            "尚未修改。请重新选择当前对象；确认、风险、权限与恢复均由下方原业务流程决定。"
        )
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self._pages = QTabWidget()
        layout.addWidget(self._pages, 1)
        self._build_destination()
        self.refresh_result_button = QPushButton("读取业务事务结果（不会执行动作）")
        self.refresh_result_button.clicked.connect(self.refresh_result)
        layout.addWidget(self.refresh_result_button)
        refresh = QPushButton("生成该领域的独立只读刷新计划")
        refresh.clicked.connect(self._open_refresh)
        layout.addWidget(refresh)
        self._poll = QTimer(self)
        self._poll.setInterval(2_000)
        self._poll.timeout.connect(self.refresh_result)
        self._poll.start()
        self.finished.connect(self._finished)

    def _build_destination(self) -> None:
        surface = self._preparation.next_ui_surface
        widget: QWidget
        if surface is OptimizationUISurface.STARTUP:
            startup = StartupManagementTab(self._runtime)
            startup.domain_dialog_opened.connect(self._observe_dialog)
            self._shutdown_callbacks.append(startup.shutdown)
            widget = startup
        elif surface in {
            OptimizationUISurface.PROCESS,
            OptimizationUISurface.SOFTWARE,
            OptimizationUISurface.SERVICE_READONLY,
            OptimizationUISurface.OVERVIEW,
        }:
            diagnostic = SystemDiagnosticsTab(self._runtime)
            diagnostic.domain_dialog_opened.connect(self._observe_dialog)
            self._shutdown_callbacks.append(diagnostic.shutdown)
            goals = {
                OptimizationUISurface.PROCESS: "查看进程",
                OptimizationUISurface.SOFTWARE: "查看已安装软件",
                OptimizationUISurface.SERVICE_READONLY: "查看服务列表",
                OptimizationUISurface.OVERVIEW: "查看磁盘空间",
            }
            diagnostic.start_planning(goals[surface])
            diagnostic.goal_input.setReadOnly(True)
            diagnostic.process_action_button.setVisible(surface is OptimizationUISurface.PROCESS)
            for button in (
                diagnostic.software_action_button,
                diagnostic.software_uninstall_button,
                diagnostic.vendor_uninstall_button,
            ):
                button.setVisible(surface is OptimizationUISurface.SOFTWARE)
            if surface is OptimizationUISurface.SOFTWARE:
                # The existing deterministic mechanism router chooses MSI/Vendor/winget/MSIX;
                # the optimization layer never selects an executable or fallback mechanism.
                diagnostic.software_uninstall_button.setText("独立审查选中软件的卸载方式")
                diagnostic.software_uninstall_button.clicked.disconnect()
                diagnostic.software_uninstall_button.clicked.connect(
                    lambda: self._route_software(diagnostic)
                )
                diagnostic.vendor_uninstall_button.hide()
            widget = diagnostic
        elif surface is OptimizationUISurface.CLEANUP:
            widget = SystemCleanupDialog(
                self._runtime,
                SystemCleanupRequest(
                    source_report_id=self._context.route.source_report_id,
                    selected_candidate_ids=self._context.candidate_ids,
                ),
                self,
            )
        elif surface is OptimizationUISurface.RECYCLE_BIN:
            widget = RecycleBinEmptyDialog(self._runtime, self)
        elif surface is OptimizationUISurface.RESIDUAL:
            if self._context.uninstall_transaction_id is None:
                raise ValueError("Missing uninstall provenance")
            residual = ResidualAnalysisDialog(
                self._runtime, self._context.uninstall_transaction_id, parent=self
            )
            residual.domain_dialog_opened.connect(self._observe_dialog)
            widget = residual
        elif surface is OptimizationUISurface.PERSONAL_STORAGE:
            self._build_personal_storage()
            return
        else:
            raise ValueError("Unsupported review surface")
        if isinstance(widget, QDialog):
            widget.setWindowFlags(Qt.WindowType.Widget)
            widget.finished.connect(self.accept)
            # Embedded dialogs still own their cooperative cancellation on close.
            self._shutdown_callbacks.append(widget.close)
        if isinstance(widget, ObservedDomainDialog):
            widget.domain_preview_ready.connect(self._preview_ready)
        self._pages.addTab(widget, "独立业务复查")

    def _build_personal_storage(self) -> None:
        analysis = FileAnalysisTab(
            self._runtime, allowed_root_ids=frozenset(self._context.authorized_root_ids)
        )
        operation = FileOperationTab(self._runtime)
        trash = TrashTab(self._runtime)
        operation.domain_preview_ready.connect(self._preview_ready)
        trash.domain_preview_ready.connect(self._preview_ready)
        self._pages.addTab(analysis, "重新分析原授权目录")
        self._pages.addTab(operation, "Stage 2 移动 / 重命名 / Undo")
        self._pages.addTab(trash, "Stage 2 回收站 / 手动恢复")
        analysis.move_selected_requested.connect(
            lambda value: self._files_selected(value, operation)
        )
        analysis.rename_selected_requested.connect(
            lambda value: self._files_selected(value, operation)
        )
        analysis.trash_selected_requested.connect(lambda value: self._files_selected(value, trash))
        self._shutdown_callbacks.extend((analysis.shutdown, operation.shutdown, trash.shutdown))

    def _files_selected(self, value: object, target: FileOperationTab | TrashTab) -> None:
        paths = (
            tuple(path for path in value if isinstance(path, Path))
            if isinstance(value, tuple)
            else ()
        )
        target.set_sources(paths)
        self._pages.setCurrentWidget(target)
        self.status.setText(
            "这里只传递新报告中勾选的文件；请生成新的 Stage 2 Preview 和确认。"
            "文件结果与恢复以 Stage 2 页面为准。"
        )

    @staticmethod
    def _route_software(diagnostic: SystemDiagnosticsTab) -> None:
        query = diagnostic._selected_software_query()
        if query is not None:
            diagnostic.open_routed_uninstall("审查本次明确选择的软件卸载", query=query)

    @Slot(object)
    def _observe_dialog(self, value: object) -> None:
        if not isinstance(value, QDialog):
            return
        self._children.add(value)
        value.finished.connect(lambda _result, dialog=value: self._children.discard(dialog))
        if isinstance(value, ObservedDomainDialog):
            value.domain_preview_ready.connect(self._preview_ready)
        else:
            self.status.setText(
                "已进入原业务流程。该流程的结果暂不汇入本会话；以业务审计为准，不代表执行成功。"
            )

    @Slot(object)
    def _preview_ready(self, value: object) -> None:
        if not isinstance(value, OptimizationTransactionReference):
            self.status.setText("业务引用无效；不会记录优化成功。")
            return
        try:
            self._services.outcomes.bind(self._session_id, self._preparation, self._context, value)
        except Exception as exc:
            self.status.setText(
                f"业务结果尚未关联（{type(exc).__name__}）；原业务确认不受替代。后续动作请以业务记录为准。"
            )
            return
        self.status.setText("已关联新的业务 Preview；尚未授权执行。下一步仍需该业务自己的确认。")

    @Slot()
    def refresh_result(self) -> None:
        """Read a bound result without changing the transaction or inferring success from UI."""
        try:
            outcome = self._services.outcomes.collect(self._preparation.handoff_id)
        except Exception as exc:
            self._poll.stop()
            self.status.setText(
                f"无法验证业务结果（{type(exc).__name__}）；请查看业务审计，不能认定成功。"
            )
            return
        if outcome is not None:
            self._poll.stop()
            self.status.setText(
                f"业务结果：{outcome.outcome.value}；风险 {outcome.domain_risk}；"
                f"恢复 {outcome.recovery_level.value}。性能收益尚未测量；没有一键撤销。"
            )

    @Slot()
    def _open_refresh(self) -> None:
        try:
            source = self._runtime.optimization_report_store.get(
                self._context.route.source_report_id
            )
        except Exception:
            self.status.setText("原报告已过期，无法作前后比较；请在系统诊断页生成新的只读计划。")
            return
        dialog = OptimizationRefreshDialog(
            self._runtime, self._preparation.target_domain, source.snapshot.system, self
        )
        self._children.add(dialog)
        dialog.finished.connect(lambda _result: self._children.discard(dialog))
        dialog.open()

    def shutdown(self) -> None:
        """Request existing controllers' safe shutdown; never kill an external uninstaller."""
        if self._shutdown_requested:
            return
        self._shutdown_requested = True
        self._poll.stop()
        for callback in self._shutdown_callbacks:
            if callable(callback):
                callback()

    @Slot(int)
    def _finished(self, _result: int) -> None:
        """Also stop polling/workers when accept/reject bypasses the window close event."""
        if not self._shutdown_requested:
            self.refresh_result()
            self.shutdown()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Require open domain dialogs to finish/stop monitoring before closing their container."""
        if any(dialog.isVisible() for dialog in self._children):
            self.status.setText("请先在业务窗口完成操作或停止监控；关闭优化窗口不会终止卸载器。")
            event.ignore()
            return
        self.refresh_result()
        self.shutdown()
        super().closeEvent(event)
