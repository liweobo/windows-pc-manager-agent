"""Structured Office workspace delegating every operation to independent domain services."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import Qt, QThreadPool, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from pc_manager_agent.app.office import OfficeServices
from pc_manager_agent.authorization.office_documents import OfficeGrantKind
from pc_manager_agent.domain.office_documents import (
    DocumentBlock,
    DocumentFormat,
    OfficeError,
    OfficeValue,
    StructuredDocument,
    ValueKind,
    office_digest,
)
from pc_manager_agent.domain.office_plans import (
    DocumentEditPlan,
    DocumentOperation,
    DocumentOperationKind,
    DocumentPreview,
    OutputMode,
    OutputSpecification,
)
from pc_manager_agent.domain.office_transactions import OfficeTransaction, OfficeTransactionState
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.office.context import (
    DocumentContextBuilder,
    OfficeModelProposal,
    spreadsheet_statistics,
)
from pc_manager_agent.office.text import parse_text
from pc_manager_agent.office.transform import convert_document
from pc_manager_agent.orchestration.office_model import OfficeDisclosure
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.tools.office_tools.read import OfficeReadResult
from pc_manager_agent.ui.office_worker import OfficeWorker


class OfficeTab(QWidget):
    """Present local content, explicit operations, Diff and separate concrete confirmations."""

    def __init__(self, services: OfficeServices) -> None:
        super().__init__()
        self._services = services
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._worker: OfficeWorker | None = None
        self._completion: Callable[[object], None] | None = None
        self._results: dict[UUID, OfficeReadResult] = {}
        self._operations: list[DocumentOperation] = []
        self._preview: DocumentPreview | None = None
        self._output_grant_id: UUID | None = None
        self._buttons: list[QPushButton] = []
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel("文件级办公自动化：不运行宏、不刷新外部连接、不打开 Office、不执行脚本。")
        )
        top = QHBoxLayout()
        self._button(top, "选择并确认读取文档", self._choose_documents)
        self._button(top, "选择输出文件", self._choose_output)
        self._output = QLineEdit()
        self._output.setReadOnly(True)
        self._output.setPlaceholderText("另存为默认不覆盖；读取和输出授权相互独立")
        top.addWidget(self._output)
        layout.addLayout(top)
        self._status = QLabel("先选择明确的文件；不会自动获得上级目录权限。")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)
        tabs = QTabWidget()
        self._work_tabs = tabs
        layout.addWidget(tabs)
        editor = QWidget()
        edit_layout = QVBoxLayout(editor)
        splitter = QSplitter()
        self._documents = QListWidget()
        self._documents.currentItemChanged.connect(self._show_document)
        splitter.addWidget(self._documents)
        self._content = QPlainTextEdit()
        self._content.setReadOnly(True)
        splitter.addWidget(self._content)
        edit_layout.addWidget(splitter)
        row = QHBoxLayout()
        self._mode = QComboBox()
        for mode in (OutputMode.SAVE_AS, OutputMode.CREATE_NEW, OutputMode.EDIT_IN_PLACE):
            self._mode.addItem(mode.value, mode)
        self._mode.currentIndexChanged.connect(self._editor_changed)
        self._documents.itemChanged.connect(self._editor_changed)
        self._kind = QComboBox()
        for kind in DocumentOperationKind:
            self._kind.addItem(kind.value, kind)
        self._target = QLineEdit()
        self._target.setPlaceholderText("精确引用，例如 body、p:0、s:0:A1")
        self._value_kind = QComboBox()
        for value_kind in ValueKind:
            self._value_kind.addItem(value_kind.value, value_kind)
        row.addWidget(self._mode)
        row.addWidget(self._kind)
        row.addWidget(self._target)
        row.addWidget(self._value_kind)
        self._button(row, "添加结构化编辑", self._add_operation)
        edit_layout.addLayout(row)
        self._value = QPlainTextEdit()
        self._value.textChanged.connect(self._editor_changed)
        self._value.setPlaceholderText(
            "编辑的新值；CREATE_NEW 模式下为新文档内容。CSV/XLSX 新建使用逗号分隔文本。"
        )
        self._value.setMaximumHeight(90)
        edit_layout.addWidget(self._value)
        self._operation_view = QPlainTextEdit()
        self._operation_view.setReadOnly(True)
        self._operation_view.setMaximumHeight(80)
        edit_layout.addWidget(self._operation_view)
        row = QHBoxLayout()
        self._button(row, "清空编辑 / 作废预览", self._discard)
        self._button(row, "生成预览与差异", self._prepare)
        self._button(row, "创建已验证备份", self._backup)
        self._button(row, "确认编辑计划", self._confirm_plan)
        self._button(row, "即时确认并保存", self._execute)
        edit_layout.addLayout(row)
        self._diff = QPlainTextEdit()
        self._diff.setReadOnly(True)
        edit_layout.addWidget(self._diff)
        tabs.addTab(editor, "文档与编辑")
        self._build_model_tab(tabs)
        self._build_history_tab(tabs)
        self._cancel = QPushButton("取消当前工作")
        self._cancel.clicked.connect(self._cancel_work)
        layout.addWidget(self._cancel)

    def _button(self, row: QHBoxLayout, text: str, action: Callable[[], None]) -> None:
        button = QPushButton(text)
        button.clicked.connect(action)
        row.addWidget(button)
        self._buttons.append(button)

    def _build_model_tab(self, tabs: QTabWidget) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(
            QLabel("可选模型建议：只发送勾选片段；摘要为可核对的原文摘录，不能直接触发编辑。")
        )
        self._goal = QLineEdit()
        self._goal.setPlaceholderText("说明办公任务，例如：选择与结论有关的原文摘录")
        layout.addWidget(self._goal)
        self._chunks = QListWidget()
        layout.addWidget(self._chunks)
        row = QHBoxLayout()
        self._button(row, "列出已勾选文档的片段", self._show_chunks)
        self._button(row, "预览模型发送内容", self._prepare_model)
        layout.addLayout(row)
        self._model_result = QPlainTextEdit()
        self._model_result.setReadOnly(True)
        layout.addWidget(self._model_result)
        tabs.addTab(page, "模型建议（可选）")

    def _build_history_tab(self, tabs: QTabWidget) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        self._history = QListWidget()
        layout.addWidget(self._history)
        row = QHBoxLayout()
        self._button(row, "刷新事务与备份历史", self._refresh_history)
        self._button(row, "准备从备份恢复", lambda: self._prepare_recovery(False))
        self._button(row, "准备撤销新建文件", lambda: self._prepare_recovery(True))
        layout.addLayout(row)
        layout.addWidget(
            QLabel(
                "恢复前请重新读取并勾选当前文件，选择同一路径作为输出。\n"
                "若文件已被修改，恢复会拒绝。中断的事务不自动继续；备份和临时文件保留。"
            )
        )
        tabs.addTab(page, "历史与恢复")

    def _choose_documents(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择明确授权的文档",
            "",
            "办公文档 (*.txt *.md *.csv *.json *.docx *.xlsx *.pdf *.docm *.xlsm *.pptx *.pptm)",
        )
        if not paths:
            return
        message = "R0 只读解析以下文件，不修改、不上传：\n" + "\n".join(paths)
        if not self._ask("读取计划确认", message):
            return
        selected = tuple(Path(path) for path in paths)

        def read(token: CancellationToken) -> object:
            grants = tuple(
                self._services.reads.grants.select(path, OfficeGrantKind.READ) for path in selected
            )
            prepared = self._services.reads.prepare_read(tuple(grant.grant_id for grant in grants))
            self._services.reads.confirm_read(prepared.plan.plan_id, True)
            return self._services.reads.read(prepared.plan.plan_id, token)

        self._run(read, self._read_completed)

    def _read_completed(self, value: object) -> None:
        if not isinstance(value, tuple) or any(
            not isinstance(item, OfficeReadResult) for item in value
        ):
            raise OfficeError("OFFICE_UI_RESULT_INVALID")
        for result in value:
            self._results[result.reference.document_id] = result
            item = QListWidgetItem(
                f"{result.reference.identity.state.path} — {result.document.support.value}"
            )
            item.setData(Qt.ItemDataRole.UserRole, result.reference.document_id)
            item.setCheckState(Qt.CheckState.Unchecked)
            self._documents.addItem(item)
        self._status.setText("读取完成。勾选要编辑的文件；选中一行可查看内容和精确引用。")

    def _show_document(self) -> None:
        if self._documents.currentRow() < 0:
            return
        item = self._documents.currentItem()
        result = self._results[item.data(Qt.ItemDataRole.UserRole)]
        lines = [
            str(result.reference.identity.state.path),
            f"支持情况：{result.document.support.value}",
            "警告：" + ", ".join(result.document.warnings),
        ]
        lines.extend(f"[{block.reference}] {block.text}" for block in result.document.blocks)
        for sheet in result.document.sheets:
            lines.append(f"[{sheet.reference}] {sheet.name}，{sheet.rows} 行 / {sheet.columns} 列")
            lines.extend(
                f"[{cell.reference}] ({cell.value.kind.value}) {cell.value.value}"
                for cell in sheet.cells[:2_000]
            )
            if len(sheet.cells) > 2_000:
                lines.append("界面仅展示前 2000 个单元格，解析结果未被删减。")
        if result.document.sheets:
            stats = spreadsheet_statistics(result)
            lines.append(
                f"确定性统计：{stats.numeric_cells} 个数值单元格合计 {stats.total}；"
                f"{stats.formula_cells} 个公式未计算。跨列求和不代表业务总额。"
            )
        self._content.setPlainText("\n".join(lines)[:200_000])

    def _selected(self) -> tuple[OfficeReadResult, ...]:
        return tuple(
            self._results[self._documents.item(index).data(Qt.ItemDataRole.UserRole)]
            for index in range(self._documents.count())
            if self._documents.item(index).checkState() is Qt.CheckState.Checked
        )

    def _choose_output(self) -> None:
        name, _ = QFileDialog.getSaveFileName(
            self,
            "选择精确输出；已存在文件只允许显式原地模式",
            "",
            "可写文档 (*.txt *.md *.csv *.json *.docx *.xlsx)",
            options=QFileDialog.Option.DontConfirmOverwrite,
        )
        if name:
            try:
                self._invalidate()
                grant = self._services.reads.grants.select(Path(name), OfficeGrantKind.OUTPUT)
                self._output_grant_id = grant.grant_id
                self._output.setText(str(grant.path))
            except Exception as exc:
                self._failure(exc.code if isinstance(exc, OfficeError) else "OUTPUT_PATH_REJECTED")

    def _add_operation(self) -> None:
        try:
            self._invalidate()
            operation = DocumentOperation(
                kind=DocumentOperationKind(self._kind.currentData()),
                target=self._target.text(),
                value=OfficeValue(
                    kind=ValueKind(self._value_kind.currentData()), value=self._value.toPlainText()
                ),
                heading_level=1,
            )
            self._operations.append(operation)
            self._operation_view.setPlainText(
                "\n".join(item.model_dump_json() for item in self._operations)
            )
        except Exception:
            self._failure("EDIT_VALUE_OR_TYPE_INVALID")

    def _prepare(self) -> None:
        try:
            self._invalidate()
            if self._output_grant_id is None:
                raise OfficeError("OUTPUT_SELECTION_REQUIRED")
            grant = self._services.reads.grants.get(self._output_grant_id, OfficeGrantKind.OUTPUT)
            mode = OutputMode(self._mode.currentData())
            selected = self._selected()
            operations = tuple(self._operations)
            initial_text = self._value.toPlainText()
            now = datetime.now(UTC)

            def prepare(token: CancellationToken) -> object:
                initial = (
                    self._initial(grant.format, initial_text)
                    if mode is OutputMode.CREATE_NEW
                    else None
                )
                plan = DocumentEditPlan(
                    inputs=() if initial else tuple(item.reference for item in selected),
                    operations=operations,
                    output=OutputSpecification(
                        mode=mode, destination_grant_id=grant.grant_id, format=grant.format
                    ),
                    initial_document=initial,
                    created_at=now,
                    expires_at=now
                    + timedelta(seconds=self._services.reads.limits.preview_ttl_seconds),
                    policy_digest=office_digest(self._services.reads.limits),
                )
                return self._services.edits.prepare(plan, token)

            self._run(prepare, self._preview_completed)
        except Exception as exc:
            self._failure(exc.code if isinstance(exc, OfficeError) else "EDIT_PLAN_INVALID")

    def _initial(self, format_: DocumentFormat, text: str) -> StructuredDocument:
        if format_ is DocumentFormat.DOCX:
            return StructuredDocument(
                format=format_,
                blocks=tuple(
                    DocumentBlock(reference=f"p:{index}", text=line)
                    for index, line in enumerate(text.splitlines())
                ),
            )
        if format_ is DocumentFormat.XLSX:
            return convert_document(
                parse_text(text.encode(), DocumentFormat.CSV, self._services.reads.limits), format_
            )
        return parse_text(text.encode(), format_, self._services.reads.limits)

    def _preview_completed(self, value: object) -> None:
        if not isinstance(value, DocumentPreview):
            raise OfficeError("OFFICE_UI_PREVIEW_INVALID")
        self._preview = value
        lines = [self._preview_summary(value), *value.warnings]
        lines.extend(
            f"[{item.reference}] {item.before_type} → {item.after_type}\n"
            f"之前：{item.before}\n之后：{item.after}"
            for item in value.differences
        )
        self._diff.setPlainText("\n\n".join(lines))
        self._status.setText("预览已生成。原地编辑/恢复需要先备份，再确认计划和即时确认。")

    def _preview_summary(self, preview: DocumentPreview) -> str:
        transaction = self._services.repository.transaction(preview.transaction_id)
        return (
            f"{transaction.mode.value} | {preview.risk_level.value} | "
            "回滚 FULL（身份不变且材料完好时）\n"
            f"目标：{transaction.output_path}\n输入字节：{preview.source_bytes}；"
            f"差异：{len(preview.differences)}；公式变更：{preview.changed_formulas}\n"
            f"备份：{preview.backup_id or '尚未创建 / 新建不需要'}\n"
            f"有效至：{preview.expires_at.isoformat()}\n"
            "不提权、不静默覆盖。原地提交使用两次不覆盖重命名，不承诺整段原子性。"
        )

    def _backup(self) -> None:
        preview = self._preview
        if preview is None:
            self._failure("PREVIEW_REQUIRED")
            return
        identifier = preview.transaction_id

        def requested(_: object) -> None:
            approved = self._ask(
                "单独确认 R1 备份",
                self._preview_summary(preview)
                + "\n将创建当前用户加密备份；不会自动删除旧备份。这不批准编辑。",
            )
            self._run(
                lambda token: self._services.edits.confirm_backup(identifier, approved, token),
                self._preview_completed,
            )

        self._run(lambda token: self._services.edits.request_backup(identifier), requested)

    def _confirm_plan(self) -> None:
        preview = self._preview
        if preview is None:
            self._failure("PREVIEW_REQUIRED")
            return
        identifier = preview.transaction_id

        def requested(_: object) -> None:
            approved = self._ask(
                "编辑计划确认",
                self._preview_summary(preview) + "\n请检查差异。是否批准此精确计划？",
            )
            self._run(
                lambda token: self._services.edits.confirm_plan(identifier, approved),
                lambda _: self._status.setText("已记录计划决定；R2 仍需即时确认。"),
            )

        self._run(
            lambda token: self._services.edits.request_plan_confirmation(identifier), requested
        )

    def _execute(self) -> None:
        preview = self._preview
        if preview is None:
            self._failure("PREVIEW_REQUIRED")
            return
        identifier = preview.transaction_id
        if preview.risk_level is RiskLevel.R1:
            self._run(
                lambda token: self._services.edits.execute(identifier, token), self._write_completed
            )
            return

        def requested(_: object) -> None:
            approved = self._ask(
                "即时确认：现在执行",
                self._preview_summary(preview)
                + "\n仅对本次具体对象生效，不可重复使用。是否现在执行？",
            )

            def execute(token: CancellationToken) -> object:
                self._services.edits.confirm_immediate(identifier, approved)
                return self._services.edits.execute(identifier, token) if approved else None

            self._run(execute, self._write_completed)

        self._run(
            lambda token: self._services.edits.request_immediate_confirmation(identifier), requested
        )

    def _write_completed(self, value: object) -> None:
        self._preview = None
        if isinstance(value, OfficeTransaction):
            self._status.setText(
                f"事务状态：{value.state.value}；输出：{value.output_path}。"
                f"保留原件：{value.retained_original_path or '不适用'}。请在历史中查看恢复入口。"
            )
        else:
            self._status.setText("未执行写入。")

    def _invalidate(self) -> None:
        if self._preview is not None:
            transaction = self._services.repository.transaction(self._preview.transaction_id)
            if transaction.state is OfficeTransactionState.PREVIEWED:
                self._services.edits.cancel(transaction.transaction_id)
            elif transaction.state not in {
                OfficeTransactionState.COMPLETED,
                OfficeTransactionState.FAILED,
                OfficeTransactionState.CANCELLED,
                OfficeTransactionState.INTERRUPTED,
                OfficeTransactionState.RESTORED,
            }:
                raise OfficeError("OFFICE_PREVIEW_STILL_EXECUTING")
            self._preview = None
            self._diff.clear()

    @Slot()
    def _editor_changed(self) -> None:
        if self._worker is None:
            try:
                self._invalidate()
            except OfficeError as exc:
                self._failure(exc.code)

    def set_user_goal(self, text: str) -> None:
        """Accept chat intent without choosing files, uploading content or preparing an edit."""
        self._goal.setText(text)

    def suggest_downloaded_document(self, path: Path) -> None:
        """Display a Browser handoff hint without granting read access or starting parsing."""
        self._goal.setText(f"审查浏览器下载文档：{path.name}")
        self._status.setText(
            f"浏览器已交接文件提示：{path}。请点击“选择并只读解析文档”重新选择它，"
            "并完成独立 Stage 5A 读取确认；当前提示不授予读取或编辑权限。"
        )

    def _discard(self) -> None:
        try:
            self._invalidate()
            self._operations.clear()
            self._operation_view.clear()
        except OfficeError as exc:
            self._failure(exc.code)

    def _show_chunks(self) -> None:
        self._chunks.clear()
        try:
            builder = DocumentContextBuilder(self._services.reads.limits)
            for result in self._selected():
                for chunk in builder.chunks(result):
                    item = QListWidgetItem(
                        f"{chunk.source_reference} +{chunk.offset}: {chunk.text[:100]}"
                    )
                    item.setData(Qt.ItemDataRole.UserRole, chunk.chunk_id)
                    item.setCheckState(Qt.CheckState.Unchecked)
                    self._chunks.addItem(item)
        except OfficeError as exc:
            self._failure(exc.code)

    def _prepare_model(self) -> None:
        model = self._services.model
        if model is None:
            self._failure("MODEL_DISABLED_CONFIGURE_OPENAI_ENVIRONMENT")
            return
        identifiers = tuple(item.reference.document_id for item in self._selected())
        chunks = tuple(
            self._chunks.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(self._chunks.count())
            if self._chunks.item(index).checkState() is Qt.CheckState.Checked
        )
        goal = self._goal.text()
        self._run(lambda token: model.prepare(identifiers, chunks, goal), self._disclosure_ready)

    def _disclosure_ready(self, value: object) -> None:
        if not isinstance(value, OfficeDisclosure) or self._services.model is None:
            raise OfficeError("OFFICE_UI_DISCLOSURE_INVALID")
        model = self._services.model
        self._model_result.setPlainText(value.request.model_dump_json(indent=2))
        approved = self._ask(
            "单独确认外部数据发送",
            f"发送到：{value.destination}\n"
            "仅发送下方明确列出的片段和用户请求。store=False 不等于零保留；可能产生 API 费用。\n"
            + value.request.model_dump_json(indent=2),
        )
        self._run(
            lambda token: asyncio.run(model.confirm_and_propose(value.disclosure_id, approved)),
            self._model_completed,
        )

    def _model_completed(self, value: object) -> None:
        if isinstance(value, OfficeModelProposal):
            self._model_result.setPlainText(
                "模型建议（没有执行权限，编辑需单独生成本地预览）：\n"
                + value.model_dump_json(indent=2)
            )

    def _refresh_history(self) -> None:
        def show(value: object) -> None:
            if not isinstance(value, tuple):
                raise OfficeError("OFFICE_UI_HISTORY_INVALID")
            self._history.clear()
            for record in value:
                if not isinstance(record, OfficeTransaction):
                    raise OfficeError("OFFICE_UI_HISTORY_INVALID")
                item = QListWidgetItem(
                    f"{record.state.value} | {record.mode.value} | {record.output_path}\n"
                    f"备份：{record.backup_id}；保留原件：{record.retained_original_path}；临时：{record.temporary_path}"
                )
                item.setData(Qt.ItemDataRole.UserRole, record.transaction_id)
                self._history.addItem(item)

        self._run(lambda token: self._services.edits.history(), show)

    def _prepare_recovery(self, undo: bool) -> None:
        item, selected = self._history.currentItem(), self._selected()
        if item is None or len(selected) != 1 or self._output_grant_id is None:
            self._failure("SELECT_HISTORY_CURRENT_DOCUMENT_AND_OUTPUT")
            return
        try:
            self._invalidate()
        except OfficeError as exc:
            self._failure(exc.code)
            return
        original = UUID(str(item.data(Qt.ItemDataRole.UserRole)))
        current, grant = selected[0].reference.document_id, self._output_grant_id
        operation = (
            self._services.edits.prepare_undo_created
            if undo
            else self._services.edits.prepare_restore
        )
        self._run(lambda token: operation(original, current, grant, token), self._preview_completed)

    def _run(
        self, task: Callable[[CancellationToken], object], completion: Callable[[object], None]
    ) -> None:
        if self._worker is not None:
            return
        self._completion = completion
        self._worker = OfficeWorker(task)
        self._worker.signals.completed.connect(self._completed)
        self._worker.signals.failed.connect(self._failure)
        for button in self._buttons:
            button.setEnabled(False)
        self._work_tabs.setEnabled(False)
        self._status.setText("后台处理中；可以取消，不会强制关闭 Office。")
        self._pool.start(self._worker)

    @Slot(object)
    def _completed(self, value: object) -> None:
        completion = self._completion
        self._worker = None
        for button in self._buttons:
            button.setEnabled(True)
        self._work_tabs.setEnabled(True)
        try:
            if completion:
                completion(value)
        except Exception as exc:
            self._failure(exc.code if isinstance(exc, OfficeError) else "OFFICE_UI_RESULT_INVALID")

    @Slot(str)
    def _failure(self, code: str) -> None:
        self._worker = None
        for button in self._buttons:
            button.setEnabled(True)
        self._work_tabs.setEnabled(True)
        self._status.setText(
            f"未继续操作：{code}。请检查文件是否变化、被占用、输出冲突或确认过期，然后重新生成预览。"
        )

    def _cancel_work(self) -> None:
        if self._worker:
            self._worker.cancel()
            self._status.setText("已请求取消；等待当前安全边界结束，不删除恢复材料。")
        else:
            self._discard()

    def cancel_current_work(self) -> None:
        """Accept a current-page cancellation request, never approval or automatic Undo."""
        self._cancel_work()

    def _ask(self, title: str, text: str) -> bool:
        dialog = QMessageBox(
            QMessageBox.Icon.Warning,
            title,
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            self,
        )
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setDefaultButton(QMessageBox.StandardButton.No)
        return dialog.exec() == QMessageBox.StandardButton.Yes

    def shutdown(self) -> bool:
        """Cancel owned work and wait boundedly; false means the caller must defer shutdown."""
        if self._worker:
            self._worker.cancel()
        return self._pool.waitForDone(35_000)
