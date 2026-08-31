"""Stage 2B Recycle Bin Preview and two-confirmation user interface."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from PySide6.QtCore import Qt, QThreadPool, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from pc_manager_agent.app.runtime import ApplicationRuntime, TrashServices
from pc_manager_agent.domain.optimization_receipts import (
    OptimizationReceiptKind,
    OptimizationTransactionReference,
)
from pc_manager_agent.orchestration.trash_service import (
    PreparedTrashOperation,
    RuntimeConfirmedTrashOperation,
)
from pc_manager_agent.ui.workers import (
    TrashExecutionWorker,
    TrashPreviewWorker,
    require_prepared_trash,
    require_trash_report,
)


class TrashTab(QWidget):
    """Show exact R2 impact while business services enforce every safety boundary."""

    status_message = Signal(str)
    domain_preview_ready = Signal(object)

    def __init__(self, runtime: ApplicationRuntime) -> None:
        super().__init__()
        self._runtime = runtime
        self._services: TrashServices | None = None
        self._prepared: PreparedTrashOperation | None = None
        self._runtime_prepared: RuntimeConfirmedTrashOperation | None = None
        self._preview_worker: TrashPreviewWorker | None = None
        self._execution_worker: TrashExecutionWorker | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        warning = QLabel(
            "R2：仅移入 Windows 回收站，不会永久删除。恢复能力为 MANUAL，需要从回收站手动还原。"
        )
        warning.setStyleSheet("color: #9b2c00; font-weight: 700;")
        source_actions = QHBoxLayout()
        add_files = QPushButton("添加文件")
        add_directory = QPushButton("添加文件夹")
        clear = QPushButton("清空选择")
        self.preview_button = QPushButton("生成 R2 Preview")
        add_files.clicked.connect(self._add_files)
        add_directory.clicked.connect(self._add_directory)
        clear.clicked.connect(self._clear_sources)
        self.preview_button.clicked.connect(self._prepare)
        source_actions.addWidget(add_files)
        source_actions.addWidget(add_directory)
        source_actions.addWidget(clear)
        source_actions.addWidget(self.preview_button)
        source_actions.addStretch(1)
        self.source_list = QListWidget()
        self.source_list.setMaximumHeight(110)
        self.source_list.itemChanged.connect(self._invalidate)
        self.summary = QTextBrowser()
        self.summary.setMaximumHeight(145)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ("状态", "路径", "对象数", "总大小", "隐藏", "系统/重解析", "阻止原因")
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        actions = QHBoxLayout()
        self.plan_confirm_button = QPushButton("第一次确认：批准 R2 计划")
        self.runtime_confirm_button = QPushButton("第二次确认并立即执行…")
        self.cancel_button = QPushButton("停止后续对象")
        for button in (
            self.plan_confirm_button,
            self.runtime_confirm_button,
            self.cancel_button,
        ):
            button.setEnabled(False)
            actions.addWidget(button)
        self.plan_confirm_button.clicked.connect(self._confirm_plan)
        self.runtime_confirm_button.clicked.connect(self._confirm_runtime)
        self.cancel_button.clicked.connect(self.cancel)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.recovery_view = QTextBrowser()
        self.recovery_view.setPlaceholderText(
            "操作后将在这里显示原路径、验证结果和 Windows 回收站手动还原步骤。"
        )
        layout.addWidget(warning)
        layout.addLayout(source_actions)
        layout.addWidget(self.source_list)
        layout.addWidget(self.summary)
        layout.addWidget(self.table, 1)
        layout.addLayout(actions)
        layout.addWidget(self.progress)
        layout.addWidget(QLabel("恢复说明"))
        layout.addWidget(self.recovery_view)

    def set_sources(self, paths: tuple[Path, ...]) -> None:
        """Replace pending targets with paths explicitly selected by the user."""
        self.source_list.blockSignals(True)
        self.source_list.clear()
        for path in paths:
            item = QListWidgetItem(str(path))
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.source_list.addItem(item)
        self.source_list.blockSignals(False)
        self._invalidate()
        self.status_message.emit(f"已传入 {len(paths)} 个明确选择；尚未执行回收站操作")

    @Slot()
    def _add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "选择准备移入回收站的文件")
        self.set_sources((*self._source_paths(), *(Path(path) for path in paths)))

    @Slot()
    def _add_directory(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择准备移入回收站的文件夹")
        if path:
            self.set_sources((*self._source_paths(), Path(path)))

    @Slot()
    def _clear_sources(self) -> None:
        self.set_sources(())

    @Slot()
    def _prepare(self) -> None:
        paths = self._source_paths()
        if not paths:
            self._show_error("请先明确选择文件或文件夹。")
            return
        try:
            self._services = self._runtime.create_trash_services()
            plan = self._services.compiler.compile(
                "将用户明确选择的对象移入 Windows 回收站",
                paths,
                self._all_root_ids(),
            )
        except Exception as exc:
            self._show_error(str(exc))
            return
        worker = TrashPreviewWorker(self._services, plan)
        worker.signals.completed.connect(self._preview_completed)
        worker.signals.failed.connect(self._worker_failed)
        self._preview_worker = worker
        self.preview_button.setEnabled(False)
        self.status_message.emit("正在进行 R2 路径、卷能力和目录快照检查…")
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _preview_completed(self, value: object) -> None:
        self._preview_worker = None
        self.preview_button.setEnabled(True)
        try:
            prepared = require_prepared_trash(value)
        except TypeError as exc:
            self._show_error(str(exc))
            return
        self._prepared = prepared
        self.domain_preview_ready.emit(
            OptimizationTransactionReference(
                kind=OptimizationReceiptKind.PERSONAL_TRASH,
                transaction_id=prepared.preview.transaction_id,
            )
        )
        preview = prepared.preview
        self.summary.setPlainText(
            f"风险：R2\n选择：{preview.selected_count}\n目录内对象：{preview.contained_object_count}\n"
            f"总大小：{preview.total_size_bytes} 字节\n影响：{preview.impact_level.value}\n"
            "回滚：MANUAL；应用不提供自动恢复，也不会清空回收站。"
        )
        self.table.setRowCount(len(preview.items))
        for row, item in enumerate(preview.items):
            snapshot = item.snapshot
            values = (
                item.status.value,
                str(item.source),
                str(snapshot.object_count if snapshot else 0),
                str(snapshot.total_size_bytes if snapshot else 0),
                str(snapshot.hidden_count if snapshot else 0),
                str((snapshot.system_count + snapshot.reparse_count) if snapshot else 0),
                "; ".join(issue.message for issue in item.issues),
            )
            for column, text in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(text))
        self.plan_confirm_button.setEnabled(True)
        self.runtime_confirm_button.setEnabled(False)
        self.status_message.emit("R2 Preview 已生成；等待第一次计划确认")

    @Slot()
    def _confirm_plan(self) -> None:
        if self._services is None or self._prepared is None:
            return
        preview = self._prepared.preview
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle("第一次确认：R2 回收站计划")
        dialog.setText(
            f"计划把 {preview.selected_count} 个选中对象（目录内共 "
            f"{preview.contained_object_count} 个对象、"
            f"{preview.total_size_bytes} 字节）移入回收站。"
        )
        dialog.setInformativeText("这不是执行确认。批准后仍会重新验证，并要求第二次即时确认。")
        approve = dialog.addButton("批准此计划", QMessageBox.ButtonRole.AcceptRole)
        cancel = dialog.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(cancel)
        dialog.exec()
        approved = dialog.clickedButton() is approve
        try:
            self._services.service.resolve_plan_confirmation(self._prepared, approved)
            if not approved:
                self._invalidate()
                return
            self._runtime_prepared = self._services.service.request_runtime_confirmation(
                self._prepared
            )
        except Exception as exc:
            self._show_error(str(exc))
            return
        self.plan_confirm_button.setEnabled(False)
        self.runtime_confirm_button.setEnabled(True)
        self.status_message.emit("计划已批准并重新验证；等待第二次即时确认")

    @Slot()
    def _confirm_runtime(self) -> None:
        if self._services is None or self._runtime_prepared is None:
            return
        preview = self._runtime_prepared.runtime_preview
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Critical)
        dialog.setWindowTitle("第二次确认：立即移入 Windows 回收站")
        dialog.setText(
            f"现在将把 {preview.selected_count} 个明确对象、共 {preview.total_size_bytes} 字节"
            "移入 Windows 回收站。"
        )
        dialog.setInformativeText(
            "恢复能力为 MANUAL，需要从 Windows 回收站手动还原。没有永久删除回退。是否立即执行？"
        )
        execute = dialog.addButton("立即移入回收站", QMessageBox.ButtonRole.DestructiveRole)
        cancel = dialog.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(cancel)
        dialog.exec()
        approved = dialog.clickedButton() is execute
        try:
            self._services.service.resolve_runtime_confirmation(self._runtime_prepared, approved)
        except Exception as exc:
            self._show_error(str(exc))
            return
        if not approved:
            self._invalidate()
            return
        worker = TrashExecutionWorker(self._services, self._runtime_prepared)
        worker.signals.completed.connect(self._execution_completed)
        worker.signals.failed.connect(self._worker_failed)
        self._execution_worker = worker
        self.runtime_confirm_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress.setRange(0, 0)
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _execution_completed(self, value: object) -> None:
        self._execution_worker = None
        self.cancel_button.setEnabled(False)
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        try:
            report = require_trash_report(value)
            if self._services is None:
                raise RuntimeError("Trash services are unavailable")
            records = self._services.service.recovery_records(report.transaction_id)
        except Exception as exc:
            self._show_error(str(exc))
            return
        lines = [
            f"事务 {report.transaction_id}",
            f"成功 {report.completed_count}，失败 {report.failed_count}，"
            f"跳过 {report.skipped_count}",
            "恢复等级：MANUAL",
            "恢复步骤：打开 Windows 回收站，按原名称和删除时间找到对象，右键选择“还原”。",
        ]
        lines.extend(f"- {record.original_path}: {record.status.value}" for record in records)
        self.recovery_view.setPlainText("\n".join(lines))
        self.status_message.emit("回收站事务结束；请查看验证结果和手动恢复说明")

    @Slot()
    def cancel(self) -> None:
        """Stop future items without interrupting an active Windows Shell call."""
        if self._execution_worker is not None:
            self._execution_worker.cancel()
            self.status_message.emit("已请求停止；当前 Shell 调用完成后不再处理后续对象")

    def shutdown(self) -> None:
        """Request cancellation before application shutdown waits for workers."""
        self.cancel()

    def _source_paths(self) -> tuple[Path, ...]:
        return tuple(
            Path(str(self.source_list.item(index).data(Qt.ItemDataRole.UserRole)))
            for index in range(self.source_list.count())
        )

    def _all_root_ids(self) -> tuple[UUID, ...]:
        return tuple(record.path_id for record in self._runtime.authorized_paths.list_authorized())

    @Slot()
    def _invalidate(self) -> None:
        self._prepared = None
        self._runtime_prepared = None
        self.plan_confirm_button.setEnabled(False)
        self.runtime_confirm_button.setEnabled(False)
        self.summary.clear()

    @Slot(str)
    def _worker_failed(self, message: str) -> None:
        self._preview_worker = None
        self._execution_worker = None
        self.preview_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.progress.setRange(0, 1)
        self._show_error(message)

    def _show_error(self, message: str) -> None:
        QMessageBox.warning(self, "回收站操作未执行", message)
        self.status_message.emit(message)
