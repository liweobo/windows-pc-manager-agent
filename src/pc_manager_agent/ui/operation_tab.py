"""Stage 2A file-operation Preview, confirmation, execution, and rollback page."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from PySide6.QtCore import Qt, QThreadPool, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from pc_manager_agent.app.runtime import ApplicationRuntime, FileOperationServices
from pc_manager_agent.domain.file_operations import (
    FileOperationPlan,
    OperationType,
    RenameRule,
    RenameRuleType,
)
from pc_manager_agent.domain.transactions import OperationProgress
from pc_manager_agent.orchestration.file_operation_service import PreparedFileOperation
from pc_manager_agent.rollback.manager import PreparedRollback
from pc_manager_agent.ui.workers import (
    FileOperationPlanningWorker,
    OperationExecutionWorker,
    OperationPreviewWorker,
    RollbackExecutionWorker,
    require_operation_plan,
    require_operation_report,
    require_operation_transaction,
    require_prepared_operation,
)


class FileOperationTab(QWidget):
    """Keep user interaction visible while delegating every write to Stage 2A services."""

    status_message = Signal(str)
    progress_changed = Signal(object)

    def __init__(self, runtime: ApplicationRuntime) -> None:
        super().__init__()
        self._runtime = runtime
        self._services: FileOperationServices | None = None
        self._plan: FileOperationPlan | None = None
        self._prepared: PreparedFileOperation | None = None
        self._prepared_rollback: PreparedRollback | None = None
        self._planning_worker: FileOperationPlanningWorker | None = None
        self._preview_worker: OperationPreviewWorker | None = None
        self._execution_worker: OperationExecutionWorker | None = None
        self._rollback_worker: RollbackExecutionWorker | None = None
        self._build_ui()
        self.progress_changed.connect(self._progress_changed)
        self.refresh_history()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        self.interrupted_label = QLabel()
        self.interrupted_label.setStyleSheet("color: #a15c00; font-weight: 600;")
        if self._runtime.interrupted_operation_ids:
            self.interrupted_label.setText(
                f"检测到 {len(self._runtime.interrupted_operation_ids)} 个异常中断事务；"
                "不会自动继续，请在历史记录中检查并生成回滚预览。"
            )

        source_actions = QHBoxLayout()
        add_files = QPushButton("添加文件")
        add_directory = QPushButton("添加文件夹")
        clear_sources = QPushButton("清空选择")
        self.source_list = QListWidget()
        self.source_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.source_list.setMaximumHeight(105)
        add_files.clicked.connect(self._add_files)
        add_directory.clicked.connect(self._add_directory)
        clear_sources.clicked.connect(self.source_list.clear)
        source_actions.addWidget(QLabel("准备处理的对象"))
        source_actions.addWidget(add_files)
        source_actions.addWidget(add_directory)
        source_actions.addWidget(clear_sources)
        source_actions.addStretch(1)
        move_row = QHBoxLayout()
        self.destination_input = QLineEdit()
        self.destination_input.setPlaceholderText("移动目标目录（必须位于已授权目录）")
        choose_destination = QPushButton("选择目标")
        self.move_preview_button = QPushButton("生成移动 Preview")
        choose_destination.clicked.connect(self._choose_destination)
        self.move_preview_button.clicked.connect(self._prepare_move)
        move_row.addWidget(self.destination_input)
        move_row.addWidget(choose_destination)
        move_row.addWidget(self.move_preview_button)

        rename_row = QHBoxLayout()
        self.rename_type = QComboBox()
        for label, rule_type in (
            ("添加前缀", RenameRuleType.PREFIX),
            ("添加后缀", RenameRuleType.SUFFIX),
            ("连续编号", RenameRuleType.SEQUENCE),
            ("转为小写", RenameRuleType.LOWERCASE),
            ("转为大写", RenameRuleType.UPPERCASE),
            ("替换文字", RenameRuleType.REPLACE_TEXT),
            ("修改日期前缀", RenameRuleType.DATE_PREFIX),
        ):
            self.rename_type.addItem(label, rule_type.value)
        self.rename_value = QLineEdit()
        self.rename_value.setPlaceholderText("前缀/后缀/查找文字；编号默认 item_")
        self.rename_replacement = QLineEdit()
        self.rename_replacement.setPlaceholderText("替换为")
        self.rename_start = QSpinBox()
        self.rename_start.setRange(0, 999_999)
        self.rename_start.setValue(1)
        self.rename_width = QSpinBox()
        self.rename_width.setRange(1, 8)
        self.rename_width.setValue(3)
        self.rename_preview_button = QPushButton("生成重命名 Preview")
        self.rename_preview_button.clicked.connect(self._prepare_rename)
        rename_row.addWidget(self.rename_type)
        rename_row.addWidget(self.rename_value)
        rename_row.addWidget(self.rename_replacement)
        rename_row.addWidget(QLabel("起始"))
        rename_row.addWidget(self.rename_start)
        rename_row.addWidget(QLabel("位数"))
        rename_row.addWidget(self.rename_width)
        rename_row.addWidget(self.rename_preview_button)

        self.risk_label = QLabel("风险：R1；尚未生成真实文件系统 Preview")
        self.risk_label.setStyleSheet("font-weight: 600;")
        self.preview_summary = QTextBrowser()
        self.preview_summary.setMaximumHeight(115)
        self.preview_table = QTableWidget(0, 5)
        self.preview_table.setHorizontalHeaderLabels(
            ("状态", "操作", "源路径", "目标/恢复路径", "冲突或阻止原因")
        )
        self.preview_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.preview_table.horizontalHeader().setStretchLastSection(True)

        operation_actions = QHBoxLayout()
        self.confirm_button = QPushButton("确认并允许执行此 Preview")
        self.reject_button = QPushButton("拒绝")
        self.execute_button = QPushButton("执行已确认操作")
        self.cancel_button = QPushButton("停止后续操作")
        for button in (
            self.confirm_button,
            self.reject_button,
            self.execute_button,
            self.cancel_button,
        ):
            button.setEnabled(False)
            operation_actions.addWidget(button)
        self.confirm_button.clicked.connect(self._confirm_forward)
        self.reject_button.clicked.connect(self._reject_forward)
        self.execute_button.clicked.connect(self._execute_forward)
        self.cancel_button.clicked.connect(self.cancel)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress_label = QLabel("未执行任何写操作")

        history_actions = QHBoxLayout()
        refresh = QPushButton("刷新操作历史")
        self.rollback_preview_button = QPushButton("为选中事务生成回滚 Preview")
        self.rollback_confirm_button = QPushButton("确认并执行此回滚 Preview")
        self.rollback_confirm_button.setEnabled(False)
        refresh.clicked.connect(self.refresh_history)
        self.rollback_preview_button.clicked.connect(self._prepare_rollback)
        self.rollback_confirm_button.clicked.connect(self._confirm_and_execute_rollback)
        history_actions.addWidget(QLabel("事务历史与 Undo"))
        history_actions.addWidget(refresh)
        history_actions.addWidget(self.rollback_preview_button)
        history_actions.addWidget(self.rollback_confirm_button)
        history_actions.addStretch(1)
        self.history_table = QTableWidget(0, 7)
        self.history_table.setHorizontalHeaderLabels(
            ("更新时间", "事务 ID", "状态", "计划", "成功", "失败", "跳过")
        )
        self.history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.history_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.history_table.horizontalHeader().setStretchLastSection(True)

        layout.addWidget(self.interrupted_label)
        layout.addLayout(source_actions)
        layout.addWidget(self.source_list)
        layout.addLayout(move_row)
        layout.addLayout(rename_row)
        layout.addWidget(self.risk_label)
        layout.addWidget(self.preview_summary)
        layout.addWidget(self.preview_table, 1)
        layout.addLayout(operation_actions)
        layout.addWidget(self.progress)
        layout.addWidget(self.progress_label)
        layout.addLayout(history_actions)
        layout.addWidget(self.history_table, 1)

    def set_sources(self, paths: tuple[Path, ...]) -> None:
        """Replace the pending selection with paths supplied by Stage 1 results."""
        self.source_list.clear()
        for path in paths:
            item = QListWidgetItem(str(path))
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.source_list.addItem(item)
        self.status_message.emit(f"已选择 {len(paths)} 个对象；尚未执行写操作")

    def start_planning(self, goal: str) -> None:
        """Start consented provider intent planning for a natural-language operation goal."""
        try:
            services = self._runtime.create_file_operation_services(self.progress_changed.emit)
            if services.planner is None:
                raise ValueError(
                    "当前未启用模型供应商。可在本页选择文件后使用确定性的移动或重命名 Preview。"
                )
            consent = services.planner.request_external_consent(goal)
            approved = (
                QMessageBox.question(self, "确认发送操作意图", consent.object_summary)
                == QMessageBox.StandardButton.Yes
            )
            self._runtime.external_consent.resolve(consent.confirmation_id, approved)
            if not approved:
                return
            worker = FileOperationPlanningWorker(services, goal, consent.confirmation_id)
        except Exception as exc:
            self._show_error(str(exc))
            return
        worker.signals.completed.connect(self._natural_plan_completed)
        worker.signals.failed.connect(self._worker_failed)
        self._services = services
        self._planning_worker = worker
        self._set_planning_busy(True, "模型只生成受限意图；正在本地发现并计算具体路径……")
        QThreadPool.globalInstance().start(worker)

    @Slot()
    def _add_files(self) -> None:
        selected, _filter = QFileDialog.getOpenFileNames(self, "选择授权目录内的文件")
        if selected:
            self.set_sources(tuple(Path(path) for path in selected))

    @Slot()
    def _add_directory(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "选择授权目录内的文件夹")
        if selected:
            self.set_sources((*self._source_paths(), Path(selected)))

    @Slot()
    def _choose_destination(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "选择已授权的移动目标")
        if selected:
            self.destination_input.setText(selected)

    @Slot()
    def _prepare_move(self) -> None:
        sources = self._source_paths()
        destination = self.destination_input.text().strip()
        if not sources or not destination:
            self._show_error("请先选择对象并填写已授权的目标目录。")
            return
        try:
            services = self._runtime.create_file_operation_services(self.progress_changed.emit)
            plan = services.compiler.compile_selected_move(
                "移动 GUI 中明确选择的对象",
                sources,
                Path(destination),
                self._all_root_ids(),
            )
        except Exception as exc:
            self._show_error(str(exc))
            return
        self._start_preview(services, plan)

    @Slot()
    def _prepare_rename(self) -> None:
        sources = self._source_paths()
        if not sources:
            self._show_error("请先选择准备重命名的对象。")
            return
        try:
            rule_type = RenameRuleType(str(self.rename_type.currentData()))
            value = self.rename_value.text() or None
            if rule_type is RenameRuleType.SEQUENCE and value is None:
                value = "item_"
            rule = RenameRule(
                rule_type=rule_type,
                value=value,
                replacement=self.rename_replacement.text(),
                start=self.rename_start.value(),
                width=self.rename_width.value(),
            )
            services = self._runtime.create_file_operation_services(self.progress_changed.emit)
            plan = services.compiler.compile_selected_rename(
                "重命名 GUI 中明确选择的对象",
                sources,
                rule,
                self._all_root_ids(),
            )
        except Exception as exc:
            self._show_error(str(exc))
            return
        self._start_preview(services, plan)

    def _start_preview(
        self,
        services: FileOperationServices,
        plan: FileOperationPlan,
    ) -> None:
        self._invalidate_prepared()
        self._services = services
        self._plan = plan
        worker = OperationPreviewWorker(services, plan)
        worker.signals.completed.connect(self._preview_completed)
        worker.signals.failed.connect(self._worker_failed)
        self._preview_worker = worker
        self._set_planning_busy(True, "正在只读检查身份、目标冲突、卷和回滚能力……")
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _natural_plan_completed(self, value: object) -> None:
        self._planning_worker = None
        try:
            plan = require_operation_plan(value)
        except TypeError as exc:
            self._worker_failed(str(exc))
            return
        if self._services is None:
            self._worker_failed("操作服务已失效")
            return
        self._start_preview(self._services, plan)

    @Slot(object)
    def _preview_completed(self, value: object) -> None:
        self._preview_worker = None
        try:
            prepared = require_prepared_operation(value)
        except TypeError as exc:
            self._worker_failed(str(exc))
            return
        self._prepared = prepared
        self._set_planning_busy(False, "Preview 已生成；没有执行任何写操作")
        preview = prepared.preview
        create_count = sum(
            item.operation_type is OperationType.CREATE_DIRECTORY for item in preview.items
        )
        self.preview_summary.setPlainText(
            f"计划 {len(preview.items)} 项；可安全执行 {preview.ready_count}；"
            f"冲突 {preview.conflict_count}；阻止 {preview.blocked_count}；\n"
            f"创建目录 {create_count}；涉及 {preview.total_size_bytes:,} 字节；"
            f"FULL 回滚 {preview.full_rollback_count} 项。\n"
            "不会覆盖、不会删除用户文件、不会跨磁盘移动。"
        )
        self.risk_label.setText(
            "风险：R1；确认绑定当前 plan/preview 哈希；执行前仍会重新验证路径和身份"
        )
        self._populate_forward_preview(prepared)
        self.confirm_button.setEnabled(True)
        self.reject_button.setEnabled(True)
        self.execute_button.setEnabled(False)
        self.status_message.emit("Preview 已通过安全审查，等待明确确认")

    def _populate_forward_preview(self, prepared: PreparedFileOperation) -> None:
        preview = prepared.preview
        self.preview_table.setRowCount(len(preview.items))
        for row, item in enumerate(preview.items):
            values = (
                item.status.value,
                item.operation_type.value,
                str(item.source) if item.source else "（新建）",
                str(item.destination),
                "；".join(issue.message for issue in item.issues),
            )
            for column, text in enumerate(values):
                self.preview_table.setItem(row, column, QTableWidgetItem(text))
        self.preview_table.resizeColumnsToContents()

    @Slot()
    def _confirm_forward(self) -> None:
        if self._services is None or self._prepared is None:
            return
        preview = self._prepared.preview
        message = (
            f"将执行 {preview.ready_count} 项 R1 文件操作，涉及 "
            f"{preview.total_size_bytes:,} 字节。\n"
            f"冲突/阻止的 {preview.conflict_count + preview.blocked_count} 项不会执行。\n"
            "不会覆盖现有对象；正常成功项预计支持 FULL 自动回滚。是否确认？"
        )
        if (
            QMessageBox.question(self, "确认当前操作 Preview", message)
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            self._services.service.resolve_confirmation(self._prepared, True)
        except Exception as exc:
            self._show_error(str(exc))
            return
        self.confirm_button.setEnabled(False)
        self.reject_button.setEnabled(False)
        self.execute_button.setEnabled(True)
        self.status_message.emit("当前 Preview 已确认；尚未开始执行")

    @Slot()
    def _reject_forward(self) -> None:
        if self._services is None or self._prepared is None:
            return
        try:
            self._services.service.resolve_confirmation(self._prepared, False)
        except Exception as exc:
            self._show_error(str(exc))
            return
        self.confirm_button.setEnabled(False)
        self.reject_button.setEnabled(False)
        self.execute_button.setEnabled(False)
        self.status_message.emit("已拒绝当前 Preview；没有修改文件")

    @Slot()
    def _execute_forward(self) -> None:
        if self._services is None or self._prepared is None:
            return
        worker = OperationExecutionWorker(self._services, self._prepared)
        worker.signals.completed.connect(self._execution_completed)
        worker.signals.failed.connect(self._worker_failed)
        self._execution_worker = worker
        self.execute_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress.setRange(0, self._prepared.preview.ready_count)
        self.progress.setValue(0)
        self.progress_label.setText("正在执行；停止按钮只阻止尚未开始的后续操作")
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _progress_changed(self, value: object) -> None:
        if not isinstance(value, OperationProgress):
            return
        self.progress.setRange(0, value.operation_count)
        self.progress.setValue(value.completed_count + value.failed_count + value.skipped_count)
        self.progress_label.setText(
            f"成功 {value.completed_count}；失败 {value.failed_count}；"
            f"跳过 {value.skipped_count}；当前 {value.current_path or '—'}"
        )

    @Slot(object)
    def _execution_completed(self, value: object) -> None:
        self._execution_worker = None
        self.cancel_button.setEnabled(False)
        try:
            report = require_operation_report(value)
        except TypeError as exc:
            self._worker_failed(str(exc))
            return
        pending = sum(item.state.value == "PENDING" for item in report.items)
        transaction = report.transaction
        self.progress.setRange(0, transaction.operation_count)
        self.progress.setValue(
            transaction.completed_count + transaction.failed_count + transaction.skipped_count
        )
        self.progress_label.setText(
            f"{transaction.state.value}：成功 {transaction.completed_count}；"
            f"失败 {transaction.failed_count}；跳过 {transaction.skipped_count}；"
            f"未执行 {pending}；可回滚 {report.rollback_available_count}"
        )
        self.status_message.emit("操作已停止在持久化终态；可从历史记录生成回滚 Preview")
        self.refresh_history()

    @Slot()
    def refresh_history(self) -> None:
        """Load local transaction summaries without reading file contents."""
        try:
            rows = self._runtime.operation_repository.list_recent(100)
        except Exception as exc:
            self._show_error(str(exc))
            return
        self.history_table.setRowCount(len(rows))
        for row, transaction in enumerate(rows):
            values = (
                transaction.updated_at.isoformat(),
                str(transaction.transaction_id),
                transaction.state.value,
                str(transaction.plan_id),
                str(transaction.completed_count),
                str(transaction.failed_count),
                str(transaction.skipped_count),
            )
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, str(transaction.transaction_id))
                self.history_table.setItem(row, column, item)

    @Slot()
    def _prepare_rollback(self) -> None:
        row = self.history_table.currentRow()
        if row < 0:
            self._show_error("请先选择一个已执行或异常中断的事务。")
            return
        identity_item = self.history_table.item(row, 0)
        if identity_item is None:
            return
        try:
            services = self._runtime.create_file_operation_services(self.progress_changed.emit)
            prepared = services.rollback.prepare(
                UUID(str(identity_item.data(Qt.ItemDataRole.UserRole)))
            )
        except Exception as exc:
            self._show_error(str(exc))
            return
        self._services = services
        self._prepared_rollback = prepared
        self._populate_rollback_preview(prepared)
        ready = sum(item.status.value == "READY" for item in prepared.plan.items)
        conflicts = len(prepared.plan.items) - ready
        self.preview_summary.setPlainText(
            f"回滚事务 {prepared.plan.transaction_id}\n"
            f"可安全恢复 {ready} 项；冲突或阻止 {conflicts} 项；按原操作逆序执行。\n"
            "不会覆盖新对象；事务创建的目录只有保持原身份且为空时才会移除。"
        )
        self.risk_label.setText("回滚风险：R1；需要与正向操作独立的即时确认")
        self.rollback_confirm_button.setEnabled(ready > 0)
        self.status_message.emit("回滚 Preview 已生成；没有修改文件")

    def _populate_rollback_preview(self, prepared: PreparedRollback) -> None:
        self.preview_table.setRowCount(len(prepared.plan.items))
        for row, item in enumerate(prepared.plan.items):
            values = (
                item.status.value,
                "ROLLBACK",
                str(item.current_path),
                str(item.restore_path) if item.restore_path else "（移除事务创建的空目录）",
                "；".join(issue.message for issue in item.issues),
            )
            for column, text in enumerate(values):
                self.preview_table.setItem(row, column, QTableWidgetItem(text))
        self.preview_table.resizeColumnsToContents()

    @Slot()
    def _confirm_and_execute_rollback(self) -> None:
        if self._services is None or self._prepared_rollback is None:
            return
        ready = sum(item.status.value == "READY" for item in self._prepared_rollback.plan.items)
        message = (
            f"将按逆序恢复 {ready} 项；冲突项不会执行。\n"
            "回滚前仍会重新验证文件身份、原路径空闲和目录状态。是否确认？"
        )
        if (
            QMessageBox.question(self, "确认当前回滚 Preview", message)
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            self._services.rollback.resolve_confirmation(self._prepared_rollback, True)
        except Exception as exc:
            self._show_error(str(exc))
            return
        worker = RollbackExecutionWorker(self._services.rollback, self._prepared_rollback)
        worker.signals.completed.connect(self._rollback_completed)
        worker.signals.failed.connect(self._worker_failed)
        self._rollback_worker = worker
        self.rollback_confirm_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress.setRange(0, ready)
        self.progress.setValue(0)
        self.progress_label.setText("正在执行已确认的逆序回滚")
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _rollback_completed(self, value: object) -> None:
        self._rollback_worker = None
        self.cancel_button.setEnabled(False)
        try:
            transaction = require_operation_transaction(value)
        except TypeError as exc:
            self._worker_failed(str(exc))
            return
        self.progress_label.setText(f"回滚终态：{transaction.state.value}")
        self.status_message.emit("回滚已停止在持久化终态；请查看详情确认结果")
        self.refresh_history()

    @Slot()
    def cancel(self) -> None:
        """Stop future transaction items while preserving completed operations and Undo."""
        if self._execution_worker is not None:
            self._execution_worker.cancel()
        if self._rollback_worker is not None:
            self._rollback_worker.cancel()
        self.status_message.emit("已请求停止后续操作；当前 Win32 调用会先完成并验证")

    def shutdown(self) -> None:
        """Request fail-safe stop before application shutdown waits for workers."""
        self.cancel()

    def _source_paths(self) -> tuple[Path, ...]:
        return tuple(
            Path(str(self.source_list.item(index).data(Qt.ItemDataRole.UserRole)))
            for index in range(self.source_list.count())
        )

    def _all_root_ids(self) -> tuple[UUID, ...]:
        records = self._runtime.authorized_paths.list_authorized()
        if not records:
            raise ValueError("请先在文件分析页添加授权目录。")
        return tuple(record.path_id for record in records)

    def _invalidate_prepared(self) -> None:
        self._prepared = None
        self.confirm_button.setEnabled(False)
        self.reject_button.setEnabled(False)
        self.execute_button.setEnabled(False)

    def _set_planning_busy(self, busy: bool, message: str) -> None:
        self.move_preview_button.setEnabled(not busy)
        self.rename_preview_button.setEnabled(not busy)
        self.progress.setRange(0, 0 if busy else 1)
        if not busy:
            self.progress.setValue(1)
        self.progress_label.setText(message)

    @Slot(str)
    def _worker_failed(self, message: str) -> None:
        self._planning_worker = None
        self._preview_worker = None
        self._execution_worker = None
        self._rollback_worker = None
        self.cancel_button.setEnabled(False)
        self._set_planning_busy(False, "操作未执行或已安全停止")
        self._show_error(message)
        self.refresh_history()

    def _show_error(self, message: str) -> None:
        QMessageBox.warning(self, "操作未执行", message)
        self.status_message.emit(message)
