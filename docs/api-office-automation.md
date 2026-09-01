# Stage 5A Office API 逐函数说明

本文件对应 `src/pc_manager_agent` 中的 Stage 5A Office 实现，逐一列出 **209 个函数/方法**，包括 Stage 5B 加入的取消入口、私有辅助函数和具名回调。Pydantic 自动生成的方法、Qt 信号及第三方库内部方法不属于本项目函数，未重复列出。

### `OfficeTab.cancel_current_work`

```python
OfficeTab.cancel_current_work(self) -> None
```

作用：供当前关联的文字/语音请求使用现有 Office 取消入口。只停止当前工作的后续步骤或丢弃
尚未执行的准备状态，不确认、不保存、不恢复文件，也不终止 Word/Excel；既有事务恢复条件不变。

## 阅读约定

签名同时说明参数名称、类型、默认值与返回类型。`self` 是当前服务实例；`UUID` 是本地解析的不透明引用而非路径授权；`token/cancellation` 是本任务协作取消令牌；`limits` 是确定性硬预算；`expected` 是已审查的预期旧值/结构；`original` 按签名表示原始字节或原事务，不能替换成任意路径。

`None` 表示不返回业务结果，不表示后续步骤也成功；`Self` 表示已校验的模型自身。`Iterator` 配合上下文管理器在 `with` 内提供句柄，退出时释放；不要把句柄带出作用域。

内容和操作模型只驻留本地会话。领域拒绝通常抛出带稳定代码的 `OfficeError`，模型字段错误为 Pydantic 校验错误；操作系统/存储异常在相应边界阻断，不自动重试。调用方必须区分“预览生成”“确认记录”“操作已验证”三个结果。以下纯函数/适配器不自行提供授权；生产调用必须通过对应编排服务。

## 主要调用顺序

读取：`select(READ) → prepare_read → confirm_read → read`。

写入：`select(OUTPUT) → prepare → [request_backup/confirm_backup] → request_plan_confirmation/confirm_plan → [request_immediate_confirmation/confirm_immediate] → execute`。

恢复：重新读取当前文件，`prepare_restore` 生成独立计划，然后重新备份、确认及执行。新建文件的 `prepare_undo_created` 只保留到同目录恢复文件，不删除。

模型：`DocumentContextBuilder.chunks → 显式选择片段 → OfficeModelService.prepare → confirm_and_propose`。模型结果只展示，不直接执行。

详细支持矩阵、风险及限制见 [Office 安全模型](office-automation-model.md)。下面的原始接口说明保留与源代码 docstring 一致的英文，关键内部边界附中文说明，便于对照维护。

## app/office.py

[查看源文件](../src/pc_manager_agent/app/office.py)。

组装 Office 服务及固定 Windows 适配器；不复用系统修改工具或提权 Broker。

### `OfficeServices.close`

```python
OfficeServices.close(self) -> None
```

作用：在后台工作停止后清除已解析正文缓存并释放 Office 数据库连接；保留备份和恢复材料。

接口约定：Close after workers stop; parsed document bodies are dropped, backups remain.

### `build_office_services`

```python
build_office_services(settings: AppSettings, audit_repository: AuditRepository, forbidden_roots: Callable[[], tuple[Path, ...]]) -> OfficeServices
```

作用：Inject fixed Windows adapters; Office never inherits a system tool or elevated Broker.

## audit/office_documents.py

[查看源文件](../src/pc_manager_agent/audit/office_documents.py)。

写入内容最小化的强制审计：标识、数量、大小、风险、稳定错误码和可选模型追踪。正文和完整 Diff 不进入日志。

### `OfficeAudit.__init__`

```python
OfficeAudit.__init__(self, repository: AuditRepository, git_commit: str | None=None) -> None
```

作用：注入审计存储与应用/Git 版本，后续事件共享这些元数据；不记录文档正文。

### `OfficeAudit.record`

```python
OfficeAudit.record(self, event: str, reference: str, *, risk: RiskLevel=RiskLevel.R0, count: int=0, size: int=0, code: str | None=None, provider: str | None=None, request_id: str | None=None, transaction: OfficeTransaction | None=None, duration_ms: int | None=None, tool_name: str | None=None) -> None
```

作用：Write mandatory identifiers/counts and an optional stable error code only.

提供 `transaction` 时只抽取模式、格式、操作类型/数量、身份摘要、预览/备份/确认标识和状态；不序列化路径或正文。`duration_ms` 是调用方实测耗时，`tool_name` 是固定工具名，不是可执行参数。

## authorization/office_documents.py

[查看源文件](../src/pc_manager_agent/authorization/office_documents.py)。

保存当前会话的精确读取/输出授权，反复检查禁止目录、路径重定向和授权撤销。一个文件授权不覆盖其父目录。

### `OfficePathGrant.canonical_digest`

```python
OfficePathGrant.canonical_digest(self) -> str
```

作用：Bind one path, format, grant kind and identifier.

### `OfficePathGrants.__init__`

```python
OfficePathGrants.__init__(self, forbidden_roots: Callable[[], tuple[Path, ...]]) -> None
```

作用：注入路径安全策略并建立空的会话授权表；不会自动授权用户目录。

### `OfficePathGrants.select`

```python
OfficePathGrants.select(self, path: Path, kind: OfficeGrantKind) -> OfficePathGrant
```

作用：Validate an exact user-selected file/output, without reading document contents.

### `OfficePathGrants.get`

```python
OfficePathGrants.get(self, grant_id: UUID, kind: OfficeGrantKind) -> OfficePathGrant
```

作用：Recheck scope, forbidden roots, redirects and existence on every use.

### `OfficePathGrants.revoke`

```python
OfficePathGrants.revoke(self, grant_id: UUID) -> None
```

作用：Revoke one exact grant; every later read/commit must fail.

### `OfficePathGrants._validate`

```python
OfficePathGrants._validate(self, path: Path, kind: OfficeGrantKind) -> None
```

作用：Use existing deny rules and additionally exclude system/application roots.

## backup/__init__.py

[查看源文件](../src/pc_manager_agent/backup/__init__.py)。

包命名空间，无独立执行行为。

本模块只有声明/数据模型或包标记，没有手写函数。

## backup/office_documents.py

[查看源文件](../src/pc_manager_agent/backup/office_documents.py)。

创建当前用户 DPAPI 加密备份；使用独占创建、配额、密文和明文哈希验证。备份不自动删除。

### `DocumentBackupService.__init__`

```python
DocumentBackupService.__init__(self, root: Path, repository: OfficeRepository, files: WindowsOfficeFiles, protector: WindowsOfficeDataProtector, limits: OfficeLimits) -> None
```

作用：注入备份根目录、加密器、文件适配器、存储和配额；这里只组装能力，备份创建走单独确认。

### `DocumentBackupService.create`

```python
DocumentBackupService.create(self, data: bytes, identity: OfficeDocumentIdentity, cancellation: CancellationToken, transaction_id: UUID) -> OfficeDocumentBackup
```

作用：Encrypt, exclusive-create, read back and hash before publishing a backup record.

### `DocumentBackupService.restore_bytes`

```python
DocumentBackupService.restore_bytes(self, backup_id: UUID, cancellation: CancellationToken) -> bytes
```

作用：Validate exact managed location, encrypted hash and original plaintext hash.

## config/office.py

[查看源文件](../src/pc_manager_agent/config/office.py)。

OfficeLimits 是 Pydantic 硬上限配置；非法或越界配置在加载时拒绝，模型不能放宽限制。

本模块只有声明/数据模型或包标记，没有手写函数。

## confirmation/office_documents.py

[查看源文件](../src/pc_manager_agent/confirmation/office_documents.py)。

隔离读取、备份、编辑计划、即时执行和外部发送的确认用途；确认有期限、摘要绑定及单次消费。

### `DocumentConfirmations.__init__`

```python
DocumentConfirmations.__init__(self, repository: OfficeRepository, audit: OfficeAudit, now: Callable[[], datetime] | None=None) -> None
```

作用：注入确认存储、强制审计和时钟，为可测试的过期与单次消费提供边界。

### `DocumentConfirmations.request`

```python
DocumentConfirmations.request(self, binding: str, purpose: str, expires: datetime) -> UUID
```

作用：Journal a pending request without retaining the raw content behind its hash.

### `DocumentConfirmations.resolve`

```python
DocumentConfirmations.resolve(self, confirmation_id: UUID, binding: str, purpose: str, approved: bool) -> None
```

作用：Record explicit user approval/rejection; never invoked by a model.

### `DocumentConfirmations.consume`

```python
DocumentConfirmations.consume(self, confirmation_id: UUID, binding: str, purpose: str) -> None
```

作用：Fail before an operation if audit or atomic one-time consumption is unavailable.

## domain/office_documents.py

[查看源文件](../src/pc_manager_agent/domain/office_documents.py)。

定义与 GUI/模型无关的文件身份、源引用、格式、段落、工作表、单元格和严格标量。

### `OfficeError.__init__`

```python
OfficeError.__init__(self, code: str) -> None
```

作用：保存稳定错误码，使 UI 和审计无需输出可能包含正文或凭据的底层异常。

### `office_digest`

```python
office_digest(model: FrozenModel) -> str
```

作用：Hash a canonical validated model without logging its contents.

### `OfficeDocumentIdentity.matches`

```python
OfficeDocumentIdentity.matches(self, other: OfficeDocumentIdentity) -> bool
```

作用：Compare every execution-relevant observation, excluding access time.

### `OfficeValue.validate_value`

```python
OfficeValue.validate_value(self) -> Self
```

作用：Reject ambiguous numbers, malformed dates, booleans and empty values.

### `StructuredDocument.content_digest`

```python
StructuredDocument.content_digest(self) -> str
```

作用：Bind the complete structured view, including warnings and support.

## domain/office_plans.py

[查看源文件](../src/pc_manager_agent/domain/office_plans.py)。

定义有限操作、不可变编辑计划和完整本地 Diff；模型意图不包含路径、命令或权限。

### `DocumentEditPlan.require_valid_mode`

```python
DocumentEditPlan.require_valid_mode(self) -> Self
```

作用：Reject missing inputs, contradictory creation and invalid expiry.

### `DocumentEditPlan.canonical_digest`

```python
DocumentEditPlan.canonical_digest(self) -> str
```

作用：Hash inputs, output, all operations and resource-policy binding.

### `DocumentPreview.canonical_digest`

```python
DocumentPreview.canonical_digest(self) -> str
```

作用：Bind displayed diff and every authority-relevant observation.

## domain/office_transactions.py

[查看源文件](../src/pc_manager_agent/domain/office_transactions.py)。

定义仅存元数据的事务、备份和状态模型；完整正文及可执行指令不持久化。

本模块只有声明/数据模型或包标记，没有手写函数。

## office/__init__.py

[查看源文件](../src/pc_manager_agent/office/__init__.py)。

包命名空间，无独立执行行为。

本模块只有声明/数据模型或包标记，没有手写函数。

## office/adapters.py

[查看源文件](../src/pc_manager_agent/office/adapters.py)。

固定格式分派器。只接收已经授权的字节；格式库不能决定读取路径或覆盖原文件。

### `parse_document`

```python
parse_document(data: bytes, format_: DocumentFormat, limits: OfficeLimits, delimiter: str=',') -> StructuredDocument
```

作用：Parse one bounded format; callers must authorize and stabilize its input first.

### `serialize_document`

```python
serialize_document(document: StructuredDocument, original: bytes | None=None) -> bytes
```

作用：Serialize only supported editable structures, never activate native Office.

## office/commit.py

[查看源文件](../src/pc_manager_agent/office/commit.py)。

受控提交与恢复边界。持有文件/父目录句柄、重新验证内容和身份、不覆盖重命名、记录状态和结果。两次重命名不是全局原子事务。

### `OfficeCodec.parse`

```python
OfficeCodec.parse(self, data: bytes, format_: DocumentFormat, cancellation: CancellationToken, delimiter: str=',') -> StructuredDocument
```

作用：Parse exact authorized bytes to structured data.

### `OfficeCodec.render`

```python
OfficeCodec.render(self, document: StructuredDocument, original: bytes | None, cancellation: CancellationToken) -> bytes
```

作用：Return a serialized output without opening any path.

### `DocumentCommitter.__init__`

```python
DocumentCommitter.__init__(self, files: WindowsOfficeFiles, codec: OfficeCodec, repository: OfficeRepository, audit: OfficeAudit, limits: OfficeLimits) -> None
```

作用：注入文件句柄适配器、编解码器、事务存储、审计和限制；调用方必须先完成确定性确认。

### `DocumentCommitter.execute`

```python
DocumentCommitter.execute(self, transaction: OfficeTransaction, plan: DocumentEditPlan, output: bytes, expected: StructuredDocument, token: CancellationToken, final_check: Callable[[], None]) -> OfficeTransaction
```

作用：Write new temp, reopen/verify, revalidate source, commit without replacement, verify.

### `DocumentCommitter.undo_created`

```python
DocumentCommitter.undo_created(self, transaction: OfficeTransaction, token: CancellationToken, final_check: Callable[[], None]) -> OfficeTransaction
```

作用：Move one unchanged Agent-created output to a unique retained sibling; never delete.

### `DocumentCommitter.validate_restore_material`

```python
DocumentCommitter.validate_restore_material(self, original: OfficeTransaction, token: CancellationToken) -> OfficeDocumentIdentity
```

作用：Check the exact retained original before Preview/confirmation, without changing it.

### `DocumentCommitter.restore_original`

```python
DocumentCommitter.restore_original(self, transaction: OfficeTransaction, original: OfficeTransaction, expected: StructuredDocument, token: CancellationToken, final_check: Callable[[], None]) -> OfficeTransaction
```

作用：Restore the retained ORIGINAL object, preserving identity, times and permissions.

### `DocumentCommitter._state`

```python
DocumentCommitter._state(self, current: OfficeTransaction, state: OfficeTransactionState) -> OfficeTransaction
```

作用：通过存储的比较后更新切换事务状态并返回新记录；不能覆盖并发的新版本。

### `DocumentCommitter._require_source`

```python
DocumentCommitter._require_source(self, lease: OfficeFileLease, expected: OfficeDocumentIdentity, token: CancellationToken) -> None
```

作用：从已持有的同一文件句柄重新读取完整字节并比较身份；不匹配时阻止后续重命名。

### `DocumentCommitter._cancel`

```python
DocumentCommitter._cancel(token: CancellationToken) -> None
```

作用：检查取消令牌，已取消则抛出稳定拒绝错误；不删除临时文件或恢复材料。

## office/context.py

[查看源文件](../src/pc_manager_agent/office/context.py)。

生成源定位片段、限制显式发送范围、验证引用及模型建议；数字统计由 Decimal 完成，公式不执行。

### `DocumentContextBuilder.__init__`

```python
DocumentContextBuilder.__init__(self, limits: OfficeLimits) -> None
```

作用：保存片段与上下文预算，不读文件、不联系模型。

### `DocumentContextBuilder.chunks`

```python
DocumentContextBuilder.chunks(self, result: OfficeReadResult) -> tuple[DocumentChunk, ...]
```

作用：Chunk by paragraph/page/cell and exact offsets; never blend sources or follow links.

### `DocumentContextBuilder.select`

```python
DocumentContextBuilder.select(self, results: tuple[OfficeReadResult, ...], selected: tuple[str, ...], goal: str) -> OfficeModelRequest
```

作用：Fail on missing/duplicate/oversized selections and known secret patterns.

### `validate_proposal`

```python
validate_proposal(request: OfficeModelRequest, proposal: OfficeModelProposal) -> None
```

作用：Reject fabricated citations/quotes and intent targets outside the disclosed selection.

### `spreadsheet_statistics`

```python
spreadsheet_statistics(result: OfficeReadResult) -> SpreadsheetStatistics
```

作用：Sum only explicit numeric scalars with Decimal; cached/formula cells never count.

## office/docx.py

[查看源文件](../src/pc_manager_agent/office/docx.py)。

处理简单 DOCX 段落/标题/表格；复杂结构降为只读。不会启动 Word 或运行 VBA。

### `parse_docx`

```python
parse_docx(data: bytes, format_: DocumentFormat, warnings: tuple[str, ...], limits: OfficeLimits) -> StructuredDocument
```

作用：Extract simple paragraphs/tables and explicitly restrict complex structures.

### `serialize_docx`

```python
serialize_docx(document: StructuredDocument, original: bytes | None) -> bytes
```

作用：Apply only explicit supported paragraph/table values; keep untouched simple formatting.

## office/pdf.py

[查看源文件](../src/pc_manager_agent/office/pdf.py)。

只提取 PDF 静态分页文本；加密、过量页数/文本等拒绝。没有 PDF 改写、OCR 或脚本执行。

### `parse_pdf`

```python
parse_pdf(data: bytes, limits: OfficeLimits) -> StructuredDocument
```

作用：Extract bounded page text; run inside the resource-limited parser worker.

## office/resolution.py

[查看源文件](../src/pc_manager_agent/office/resolution.py)。

只在当前已授权并成功读取的会话对象中解析目标；同名候选不自动选择，不搜索磁盘。

### `DocumentTargetResolver.candidates`

```python
DocumentTargetResolver.candidates(self, name: str, available: tuple[OfficeReadResult, ...]) -> tuple[DocumentReference, ...]
```

作用：Return exact-basename matches among already authorized/read session documents.

### `DocumentTargetResolver.resolve`

```python
DocumentTargetResolver.resolve(self, document_id: UUID, available: tuple[OfficeReadResult, ...]) -> DocumentReference
```

作用：Resolve one opaque user-selected identity; zero or multiple matches fail closed.

## office/text.py

[查看源文件](../src/pc_manager_agent/office/text.py)。

严格处理 UTF 文本、Markdown、JSON 和显式 CSV 方言；保持 JSON Decimal 精度并防止 CSV 公式注入。

### `decode_text`

```python
decode_text(data: bytes) -> tuple[str, str, str]
```

作用：Decode only explicit UTF BOMs or strict UTF-8, without guessed lossy conversion.

### `strict_json`

```python
strict_json(text: str) -> object
```

作用：Reject duplicate keys, non-finite numbers and excessive nesting.

### `strict_json.pairs`

```python
strict_json.pairs(items: list[tuple[str, object]]) -> dict[str, object]
```

作用：JSON 对象解析回调，拒绝重复键，避免前后解析器对相同文档给出不同解释。

### `strict_json.invalid_constant`

```python
strict_json.invalid_constant(_value: str) -> None
```

作用：JSON 数值解析回调，拒绝 NaN 和 Infinity 等非标准常量。

### `parse_text`

```python
parse_text(data: bytes, format_: DocumentFormat, limits: OfficeLimits, *, delimiter: str=',') -> StructuredDocument
```

作用：Create a source-addressed text/table view with explicit CSV dialect.

### `serialize_text`

```python
serialize_text(document: StructuredDocument) -> bytes
```

作用：Serialize only the structured view; CSV text is safe for spreadsheet import.

### `json_set`

```python
json_set(text: str, pointer: str, value: OfficeValue) -> str
```

作用：Set an existing JSON pointer only; pointer segments never become file paths.

### `encode_json_exact`

```python
encode_json_exact(value: object, depth: int=0) -> str
```

作用：Encode parsed JSON without converting decimal values to binary floating point.

## office/transform.py

[查看源文件](../src/pc_manager_agent/office/transform.py)。

纯结构化转换与差异计算；有限操作不执行 Python、Shell、VBA 或文档里的命令。

### `transform_documents`

```python
transform_documents(sources: tuple[StructuredDocument, ...], plan: DocumentEditPlan, limits: OfficeLimits) -> StructuredDocument
```

作用：Compile explicit edits; conversion never silently carries active Office structures.

### `convert_document`

```python
convert_document(source: StructuredDocument, target: DocumentFormat) -> StructuredDocument
```

作用：Create only documented derived formats; preserve sources as explicit text references.

### `merge_documents`

```python
merge_documents(sources: tuple[StructuredDocument, ...], target: DocumentFormat, limits: OfficeLimits) -> StructuredDocument
```

作用：Merge only explicitly selected one-sheet workbooks with identical typed headers.

### `apply_operation`

```python
apply_operation(document: StructuredDocument, operation: DocumentOperation) -> StructuredDocument
```

作用：Dispatch one finite edit; unsupported combinations reject rather than guess.

### `edit_blocks`

```python
edit_blocks(document: StructuredDocument, operation: DocumentOperation) -> StructuredDocument
```

作用：Apply exact whole-block changes; never guess a repeated heading or section.

### `edit_cells`

```python
edit_cells(document: StructuredDocument, operation: DocumentOperation) -> StructuredDocument
```

作用：Edit one unmerged unprotected exact cell and preserve formula ownership.

### `edit_sheet_names`

```python
edit_sheet_names(document: StructuredDocument, operation: DocumentOperation) -> StructuredDocument
```

作用：Reject name collisions and reference-sensitive rename rather than corrupt formulas.

### `edit_csv_rows`

```python
edit_csv_rows(document: StructuredDocument, operation: DocumentOperation) -> StructuredDocument
```

作用：Filter/sort explicitly selected CSV rows while keeping its header and all bad data.

### `document_diff`

```python
document_diff(before: StructuredDocument | None, after: StructuredDocument) -> tuple[DocumentDifference, ...]
```

作用：Compare complete source-addressed values, types and sheet names for local Preview.

### `document_diff.values`

```python
document_diff.values(document: StructuredDocument | None) -> dict[str, tuple[str, str]]
```

作用：把完整段落、单元格、类型和工作表名称映射为源引用索引，供 before/after 比较。

## office/worker.py

[查看源文件](../src/pc_manager_agent/office/worker.py)。

固定一次性解析/序列化工作进程；Windows Job 限制内存和进程数，支持超时与取消。只传字节，不传用户路径权限；不是完整受限令牌沙箱。

### `parser_entry`

```python
parser_entry(connection: Connection, ready: Any) -> None
```

作用：Wait for parent resource limits, then parse one bounded frame and exit.

### `BoundedOfficeParser.__init__`

```python
BoundedOfficeParser.__init__(self, limits: OfficeLimits) -> None
```

作用：注入硬资源限制和固定工作进程上下文，不启动 Office 应用。

### `BoundedOfficeParser.parse`

```python
BoundedOfficeParser.parse(self, data: bytes, format_: DocumentFormat, cancellation: CancellationToken, delimiter: str=',') -> StructuredDocument
```

作用：Return a typed parsed view or fail on cancellation, timeout and resource exhaustion.

### `BoundedOfficeParser.render`

```python
BoundedOfficeParser.render(self, document: StructuredDocument, original: bytes | None, cancellation: CancellationToken) -> bytes
```

作用：Serialize inside the same bounded fixed worker; never hand it filesystem authority.

### `BoundedOfficeParser._run`

```python
BoundedOfficeParser._run(self, data: bytes, format_: DocumentFormat, cancellation: CancellationToken, delimiter: str, document: StructuredDocument | None) -> bytes
```

作用：创建一次性工作进程，先配置 Job 再发送有界请求；检查取消、超时和有界响应，最后关闭所拥有的进程/IPC 资源。

## office/xlsx.py

[查看源文件](../src/pc_manager_agent/office/xlsx.py)。

处理简单工作簿，区分数值/文本/日期/布尔/公式，保持公式文本但不计算、不刷新外链。

### `scalar`

```python
scalar(value: object, *, formula: bool=False) -> OfficeValue
```

作用：Convert supported native scalars without conflating text and formulas.

### `parse_xlsx`

```python
parse_xlsx(data: bytes, format_: DocumentFormat, warnings: tuple[str, ...], limits: OfficeLimits) -> StructuredDocument
```

作用：Read actual cells, formulas and protection; do not refresh linked resources.

### `set_cell`

```python
set_cell(sheet: Worksheet, row: int, column: int, value: OfficeValue, number_format: str) -> None
```

作用：Assign a typed value; text beginning '=' explicitly remains text.

### `serialize_xlsx`

```python
serialize_xlsx(document: StructuredDocument, original: bytes | None) -> bytes
```

作用：Write supported cells to an in-memory package, never to an original path.

## orchestration/office_documents.py

[查看源文件](../src/pc_manager_agent/orchestration/office_documents.py)。

R0 读取闭环：精确授权 → 计划确认 → 文件句柄身份检查 → 有界解析 → 会话缓存及审计。

### `OfficeParser.parse`

```python
OfficeParser.parse(self, data: bytes, format_: DocumentFormat, cancellation: CancellationToken, delimiter: str=',') -> StructuredDocument
```

作用：Parse only already-authorized bytes under configured resource budgets.

### `OfficeReadPlan.canonical_digest`

```python
OfficeReadPlan.canonical_digest(self) -> str
```

作用：Bind the exact selected files, CSV dialect and resource policy.

### `OfficeDocumentService.__init__`

```python
OfficeDocumentService.__init__(self, grants: OfficePathGrants, files: WindowsOfficeFiles, parser: OfficeParser, confirmations: DocumentConfirmations, audit: OfficeAudit, limits: OfficeLimits) -> None
```

作用：组装路径授权、确认、解析、审计和读取注册表，建立有界会话缓存；正文不进入数据库。

### `OfficeDocumentService.prepare_read`

```python
OfficeDocumentService.prepare_read(self, grant_ids: tuple[UUID, ...], delimiter: str=',') -> PreparedOfficeRead
```

作用：Resolve only metadata; prepare exact plan and never read contents before approval.

### `OfficeDocumentService.confirm_read`

```python
OfficeDocumentService.confirm_read(self, plan_id: UUID, approved: bool) -> None
```

作用：Resolve the exact pending R0 plan following a user decision.

### `OfficeDocumentService.read`

```python
OfficeDocumentService.read(self, plan_id: UUID, cancellation: CancellationToken | None=None) -> tuple[OfficeReadResult, ...]
```

作用：Consume one plan, read selected files sequentially and audit truthful failure.

### `OfficeDocumentService.result`

```python
OfficeDocumentService.result(self, document_id: UUID) -> OfficeReadResult
```

作用：Return session-local data only; a report/reference is never write authority.

### `OfficeDocumentService.clear_results`

```python
OfficeDocumentService.clear_results(self) -> None
```

作用：Drop volatile parsed content; no user file or backup is removed.

### `OfficeDocumentService.revalidate`

```python
OfficeDocumentService.revalidate(self, document_id: UUID) -> None
```

作用：Recheck a previously confirmed exact document before disclosing a cached span.

### `OfficeDocumentService._pending_read`

```python
OfficeDocumentService._pending_read(self, plan_id: UUID) -> PreparedOfficeRead
```

作用：只解析当前会话的待确认读取计划；缺失、过期或不匹配的计划拒绝。

### `OfficeDocumentService._validate_read`

```python
OfficeDocumentService._validate_read(self, plan: OfficeReadPlan) -> None
```

作用：重新检查计划、策略及精确授权文件，不允许读取范围在确认后变化。

### `OfficeDocumentService._read_one`

```python
OfficeDocumentService._read_one(self, request: OfficeReadRequest, token: CancellationToken) -> OfficeReadResult
```

作用：在已确认活动读取计划内固定父目录和文件句柄，读取有界字节、验证身份、解析并返回结构化结果。

## orchestration/office_edits.py

[查看源文件](../src/pc_manager_agent/orchestration/office_edits.py)。

编辑闭环：结构化计划 → Preview/Diff → 必需备份 → 计划/即时确认 → 单次工具权限 → 验证提交 → 独立恢复计划。

### `OfficeEditService.__init__`

```python
OfficeEditService.__init__(self, reads: OfficeDocumentService, files: WindowsOfficeFiles, codec: OfficeCodec, repository: OfficeRepository, backups: DocumentBackupService, confirmations: DocumentConfirmations, audit: OfficeAudit, limits: OfficeLimits, is_elevated: Callable[[], bool]) -> None
```

作用：注入独立安全子系统，仅注册有限 Office 写工具并建立内部单次执行权限；不会授予 UI 直接写能力。

### `OfficeEditService.prepare`

```python
OfficeEditService.prepare(self, plan: DocumentEditPlan, cancellation: CancellationToken | None=None) -> DocumentPreview
```

作用：Compile a plan from exact previously read references, without modifying any file.

### `OfficeEditService.preview`

```python
OfficeEditService.preview(self, transaction_id: UUID) -> DocumentPreview
```

作用：Return the current immutable preview; backup creation invalidates its predecessor.

### `OfficeEditService.request_backup`

```python
OfficeEditService.request_backup(self, transaction_id: UUID) -> UUID
```

作用：Request a separate exact R1 backup copy; this cannot approve an in-place edit.

### `OfficeEditService.confirm_backup`

```python
OfficeEditService.confirm_backup(self, transaction_id: UUID, approved: bool, cancellation: CancellationToken | None=None) -> DocumentPreview
```

作用：Resolve explicit backup consent, create a verified copy, then publish a new Preview.

### `OfficeEditService.request_plan_confirmation`

```python
OfficeEditService.request_plan_confirmation(self, transaction_id: UUID) -> UUID
```

作用：Prepare exact edit approval only after required backup and current identity pass.

### `OfficeEditService.confirm_plan`

```python
OfficeEditService.confirm_plan(self, transaction_id: UUID, approved: bool) -> None
```

作用：Resolve the exact plan without performing or implicitly approving an R2 write.

### `OfficeEditService.request_immediate_confirmation`

```python
OfficeEditService.request_immediate_confirmation(self, transaction_id: UUID) -> UUID
```

作用：Freshly validate before issuing a short-lived independent object-specific R2 consent.

### `OfficeEditService.confirm_immediate`

```python
OfficeEditService.confirm_immediate(self, transaction_id: UUID, approved: bool) -> None
```

作用：Resolve only the current Fresh Preview; a missing/expired/changed one cannot execute.

### `OfficeEditService.execute`

```python
OfficeEditService.execute(self, transaction_id: UUID, cancellation: CancellationToken | None=None) -> OfficeTransaction
```

作用：Consume both approvals atomically, then dispatch one fixed tool; never auto-retry.

### `OfficeEditService.history`

```python
OfficeEditService.history(self) -> tuple[OfficeTransaction, ...]
```

作用：Return metadata and verification state, never bodies or resumable commands.

### `OfficeEditService.cancel`

```python
OfficeEditService.cancel(self, transaction_id: UUID) -> None
```

作用：Invalidate a pending Preview and all usable session authority; never delete material.

### `OfficeEditService.prepare_undo_created`

```python
OfficeEditService.prepare_undo_created(self, original_id: UUID, current_document_id: UUID, destination_grant_id: UUID, cancellation: CancellationToken | None=None) -> DocumentPreview
```

作用：Prepare a new exact R1/R2 plan to retain, not delete, an unchanged created result.

### `OfficeEditService.prepare_restore`

```python
OfficeEditService.prepare_restore(self, original_id: UUID, current_document_id: UUID, destination_grant_id: UUID, cancellation: CancellationToken | None=None) -> DocumentPreview
```

作用：Prepare independent restoration only while the current file exactly equals our result.

### `OfficeEditService._publish`

```python
OfficeEditService._publish(self, plan: DocumentEditPlan, view: StructuredDocument, data: bytes, before: StructuredDocument | None, *, recovery_of: UUID | None=None, backup_id: UUID | None=None) -> DocumentPreview
```

作用：构建完整本地 Diff、风险和摘要，持久化内容最小化事务，并把实际输出字节留在受限会话内存中。

### `OfficeEditService._validate`

```python
OfficeEditService._validate(self, plan: DocumentEditPlan, token: CancellationToken) -> tuple[bytes, ...]
```

作用：复核计划有效期、普通用户权限、输入引用和文件完整身份、输出授权/冲突、模式及总容量；返回已验证输入字节。

### `OfficeEditService._validate_ready`

```python
OfficeEditService._validate_ready(self, session: PreparedOfficeEdit, token: CancellationToken) -> None
```

作用：确认/执行前重新验证计划、输出摘要、备份绑定、恢复原始事务和保留原件；任一变化均拒绝。

### `OfficeEditService._backup`

```python
OfficeEditService._backup(self, identifier: UUID, token: CancellationToken) -> UUID
```

作用：仅接受内部活动备份工具分派，创建并验证备份后换发 Preview；旧编辑确认不再可用。

### `OfficeEditService._commit`

```python
OfficeEditService._commit(self, identifier: UUID, token: CancellationToken) -> UUID
```

作用：仅接受内部活动写分派，按有限模式选择提交、恢复或 Undo；恢复成功后更新被恢复事务状态。

### `OfficeEditService._validate_bindings`

```python
OfficeEditService._validate_bindings(self, session: PreparedOfficeEdit, token: CancellationToken) -> None
```

作用：文件句柄已固定后的最后检查：授权、策略、取消、普通用户状态及分派期限；避免通过重新打开源文件造成分享模式冲突。

### `OfficeEditService._dispatch`

```python
OfficeEditService._dispatch(self, session: PreparedOfficeEdit, name: str, token: CancellationToken) -> None
```

作用：签发当前事务/工具/参数绑定的内部单次权限，经注册表调用固定工具，并清除活动分派上下文。

### `OfficeEditService._require_active`

```python
OfficeEditService._require_active(self, identifier: UUID, name: str) -> None
```

作用：确保回调属于当前服务内部正在执行的精确事务和工具，阻断直接绕过服务的调用。

### `OfficeEditService._session`

```python
OfficeEditService._session(self, identifier: UUID) -> PreparedOfficeEdit
```

作用：解析当前会话待处理编辑，拒绝缺失/无效引用；数据库历史不是可恢复执行的会话。

## orchestration/office_model.py

[查看源文件](../src/pc_manager_agent/orchestration/office_model.py)。

模型调用前独立显示目的端和精确片段，重新验证来源并消费发送确认。返回建议不连接执行器。

### `OfficeModelService.__init__`

```python
OfficeModelService.__init__(self, reads: OfficeDocumentService, provider: OfficeModelProvider, confirmations: DocumentConfirmations, audit: OfficeAudit) -> None
```

作用：注入模型供应商、读取服务、上下文预算、确认与审计，建立少量待发送请求；不发送正文。

### `OfficeModelService.prepare`

```python
OfficeModelService.prepare(self, document_ids: tuple[UUID, ...], chunk_ids: tuple[str, ...], user_goal: str) -> OfficeDisclosure
```

作用：Show precisely the selected spans, never automatically send the whole document.

### `OfficeModelService.confirm_and_propose`

```python
OfficeModelService.confirm_and_propose(self, disclosure_id: UUID, approved: bool) -> OfficeModelProposal | None
```

作用：Consume one exact decision and call one provider; never dispatch the returned intent.

## persistence/office_documents.py

[查看源文件](../src/pc_manager_agent/persistence/office_documents.py)。

SQLite 事务元数据、完整性摘要、比较后更新和原子确认消费。启动时中断旧活动事务，不恢复执行；摘要不是对恶意同用户数据库改写的认证。

### `OfficeRepository.__init__`

```python
OfficeRepository.__init__(self, path: Path) -> None
```

作用：打开专用元数据表并校验结构，建立新实例标识，令旧确认失效并把未完成事务标为 INTERRUPTED。

### `OfficeRepository.put_backup`

```python
OfficeRepository.put_backup(self, backup: OfficeDocumentBackup) -> None
```

作用：Persist immutable backup metadata only after binary readback verification.

### `OfficeRepository.backup`

```python
OfficeRepository.backup(self, backup_id: UUID) -> OfficeDocumentBackup
```

作用：Load and checksum-validate one exact binary-backup record.

### `OfficeRepository.put_transaction`

```python
OfficeRepository.put_transaction(self, transaction: OfficeTransaction) -> None
```

作用：Write the exact metadata Preview before allowing confirmation.

### `OfficeRepository.transaction`

```python
OfficeRepository.transaction(self, transaction_id: UUID) -> OfficeTransaction
```

作用：Read a checksummed transaction without treating terminal state as verification.

### `OfficeRepository.recent`

```python
OfficeRepository.recent(self, limit: int=50) -> tuple[OfficeTransaction, ...]
```

作用：Return bounded journal history without content or automatically resumed authority.

### `OfficeRepository.change`

```python
OfficeRepository.change(self, previous: OfficeTransaction, updated: OfficeTransaction) -> None
```

作用：CAS update; concurrent callers cannot overwrite a newer transaction revision.

### `OfficeRepository.request`

```python
OfficeRepository.request(self, binding: str, purpose: str, expires: datetime) -> UUID
```

作用：Create one pending purpose-bound approval in the current process instance.

### `OfficeRepository.resolve`

```python
OfficeRepository.resolve(self, confirmation_id: UUID, binding: str, purpose: str, approved: bool, now: datetime) -> None
```

作用：Resolve a pending exact approval; expired or changed requests fail closed.

### `OfficeRepository.consume`

```python
OfficeRepository.consume(self, confirmation_id: UUID, binding: str, purpose: str, now: datetime) -> None
```

作用：Atomically consume once; concurrent execution and replay fail closed.

### `OfficeRepository.begin_write`

```python
OfficeRepository.begin_write(self, transaction: OfficeTransaction, approvals: tuple[tuple[UUID, str, str], ...], now: datetime) -> OfficeTransaction
```

作用：Consume every user approval and journal the write atomically, or do neither.

### `OfficeRepository._transition_consent`

```python
OfficeRepository._transition_consent(self, confirmation_id: UUID, binding: str, purpose: str, before: str, after: str, now: datetime) -> None
```

作用：在指定数据库连接内校验用途、绑定、实例、状态与期限，再原子转换确认状态。

### `OfficeRepository._insert`

```python
OfficeRepository._insert(self, record_id: UUID, kind: str, body: str) -> None
```

作用：插入带内容摘要的不可变元数据记录；重复标识或存储错误不会静默覆盖。

### `OfficeRepository._get`

```python
OfficeRepository._get(self, record_id: UUID, kind: str) -> str
```

作用：按精确标识和记录种类读取、验摘要并解析 Pydantic 元数据；损坏或缺失则拒绝。

### `OfficeRepository._interrupt_previous`

```python
OfficeRepository._interrupt_previous(self) -> None
```

作用：Invalidate all old consents and mark unfinished writes INTERRUPTED, never resume.

### `OfficeRepository.close`

```python
OfficeRepository.close(self) -> None
```

作用：Release SQLite handles without deleting history or recovery material.

## platform_support/windows/office_files.py

[查看源文件](../src/pc_manager_agent/platform_support/windows/office_files.py)。

固定 Win32 文件句柄能力；拒绝重解析点、硬链接和特殊文件，禁止静默覆盖，原地替换限普通单流 NTFS 文件。

### `_raw`

```python
_raw(path: Path) -> str
```

作用：Convert a previously authorized absolute local path to Unicode Win32 form.

### `office_main_is_elevated`

```python
office_main_is_elevated() -> bool
```

作用：Query only the current process token; unknown token state raises and blocks writing.

### `OfficeFileLease.__init__`

```python
OfficeFileLease.__init__(self, handle: Any, path: Path) -> None
```

作用：包装调用者已打开的 Win32 句柄和授权路径，后续身份观察与操作使用该句柄。

### `OfficeFileLease.read`

```python
OfficeFileLease.read(self, maximum: int, cancellation: CancellationToken) -> bytes
```

作用：Read through the held handle with a strict size and cancellation bound.

### `OfficeFileLease.identity`

```python
OfficeFileLease.identity(self, data: bytes, format_: DocumentFormat) -> OfficeDocumentIdentity
```

作用：Observe IDs, size, times and attributes from the same held handle as content.

### `OfficeFileLease.write_new`

```python
OfficeFileLease.write_new(self, data: bytes) -> None
```

作用：Fill a newly and exclusively created output, then flush it to the filesystem.

### `OfficeFileLease.require_replaceable`

```python
OfficeFileLease.require_replaceable(self, identity: OfficeDocumentIdentity) -> None
```

作用：Allow only ordinary single-stream NTFS files; never lose links or special metadata.

### `OfficeFileLease.security_digest`

```python
OfficeFileLease.security_digest(self) -> str
```

作用：Bind owner, group and DACL; custom permissions are never silently replaced.

### `OfficeFileLease.rename_absent`

```python
OfficeFileLease.rename_absent(self, destination: Path) -> None
```

作用：Rename this exact open object; an occupied target is always an error.

### `WindowsOfficeFiles.pin_parents`

```python
WindowsOfficeFiles.pin_parents(self, path: Path) -> Iterator[None]
```

作用：Keep every parent non-reparse and non-renamable until the operation ends.

### `WindowsOfficeFiles.open`

```python
WindowsOfficeFiles.open(self, path: Path, *, mutable: bool=False, create: bool=False) -> Iterator[OfficeFileLease]
```

作用：Open/create a non-redirected file; existing writers cause a safe denial.

## platform_support/windows/office_protection.py

[查看源文件](../src/pc_manager_agent/platform_support/windows/office_protection.py)。

仅用当前用户 DPAPI 保护应用自己的备份信封，不读取系统或浏览器凭据。

### `WindowsOfficeDataProtector.protect`

```python
WindowsOfficeDataProtector.protect(self, plaintext: bytes) -> bytes
```

作用：Encrypt a bounded backup using an Office-specific application purpose.

### `WindowsOfficeDataProtector.unprotect`

```python
WindowsOfficeDataProtector.unprotect(self, ciphertext: bytes) -> bytes
```

作用：Decrypt only this application's own backup envelope.

## providers/llm/office.py

[查看源文件](../src/pc_manager_agent/providers/llm/office.py)。

可替换模型供应商协议；输入是经过确认的最小上下文，输出是没有执行权限的结构化建议。

### `OfficeModelProvider.destination`

```python
OfficeModelProvider.destination(self) -> str
```

作用：Return the exact provider/endpoint/model disclosure label to bind in consent.

### `OfficeModelProvider.propose`

```python
OfficeModelProvider.propose(self, request: OfficeModelRequest) -> OfficeModelResult
```

作用：Return untrusted structured intent/quotes; never run it or select output paths.

## providers/llm/openai_office.py

[查看源文件](../src/pc_manager_agent/providers/llm/openai_office.py)。

独立官方 OpenAI Responses 适配器：固定官方地址、Pydantic 解析、store=False、无工具、零自动重试。密钥不写入日志。

### `OpenAIOfficeProvider.__init__`

```python
OpenAIOfficeProvider.__init__(self, model: str, api_key: str, client: AsyncOpenAI | None=None) -> None
```

作用：保存配置的模型和密钥或注入测试客户端；创建对象本身不请求网络，目的端固定为官方地址。

### `OpenAIOfficeProvider.destination`

```python
OpenAIOfficeProvider.destination(self) -> str
```

作用：Declare the fixed official endpoint and configured model; never a hidden proxy.

### `OpenAIOfficeProvider.propose`

```python
OpenAIOfficeProvider.propose(self, request: OfficeModelRequest) -> OfficeModelResult
```

作用：Use strict Responses parsing without storage/tools; errors never expose content/key.

## safety/office/__init__.py

[查看源文件](../src/pc_manager_agent/safety/office/__init__.py)。

包命名空间，无独立执行行为。

本模块只有声明/数据模型或包标记，没有手写函数。

## safety/office/content.py

[查看源文件](../src/pc_manager_agent/safety/office/content.py)。

静态内容安全：有界 ZIP/XML、防实体/DTD/外部引用、有限公式、CSV 注入转义和已知敏感内容拒绝。

### `safe_xml`

```python
safe_xml(data: bytes) -> Element
```

作用：Parse bounded XML with entities, DTD and external access forbidden.

### `inspect_package`

```python
inspect_package(data: bytes, format_: DocumentFormat, limits: OfficeLimits) -> tuple[str, ...]
```

作用：Validate the whole ZIP directory and static XML before a format library sees it.

### `require_safe_formula`

```python
require_safe_formula(value: str) -> None
```

作用：Allow only simple local arithmetic/reducer formulas; no links or named code.

### `safe_spreadsheet_text`

```python
safe_spreadsheet_text(value: str) -> str
```

作用：Escape formula-like CSV text, including leading Unicode/control whitespace.

### `require_disclosable`

```python
require_disclosable(text: str) -> None
```

作用：Block known secret/credential forms; this is not a complete DLP claim.

## safety/office/editing.py

[查看源文件](../src/pc_manager_agent/safety/office/editing.py)。

确定性编辑安全、结构唯一性、序列化往返验证和风险分级；确认不能覆盖拒绝。

### `validate_edit_plan`

```python
validate_edit_plan(plan: DocumentEditPlan, limits: OfficeLimits, now: datetime) -> None
```

作用：Reject expired, oversized, duplicate or unsupported output plans before preparation.

### `validate_structure`

```python
validate_structure(document: StructuredDocument, limits: OfficeLimits) -> None
```

作用：Prevent duplicate references, oversized dimensions and ambiguous cell coordinates.

### `require_roundtrip`

```python
require_roundtrip(expected: StructuredDocument, observed: StructuredDocument) -> None
```

作用：Verify reopened values, types, formula text, headings and table/sheet structure.

### `edit_risk`

```python
edit_risk(plan: DocumentEditPlan, changed_cells: int, limits: OfficeLimits) -> RiskLevel
```

作用：Classify from deterministic impact, never a model-provided risk label.

## tools/office_tools/__init__.py

[查看源文件](../src/pc_manager_agent/tools/office_tools/__init__.py)。

包命名空间，无独立执行行为。

本模块只有声明/数据模型或包标记，没有手写函数。

## tools/office_tools/read.py

[查看源文件](../src/pc_manager_agent/tools/office_tools/read.py)。

只有 office.document.read 的 R0 工具定义；即使直接调用注册表，也必须存在服务内部的已确认活动读取计划。

### `OfficeReadTool.__init__`

```python
OfficeReadTool.__init__(self, reader: Callable[[OfficeReadRequest, CancellationToken], OfficeReadResult]) -> None
```

作用：注入固定读取回调和活动计划检查器；调用者不能提供自定义工具名称。

### `OfficeReadTool.manifest`

```python
OfficeReadTool.manifest(self) -> ToolManifest
```

作用：Return the fixed R0 manifest, isolated from system-management registries.

### `OfficeReadTool.execute`

```python
OfficeReadTool.execute(self, request: BaseModel, cancellation: CancellationToken) -> OfficeReadResult
```

作用：Require the service's active confirmed read scope even for direct registry calls.

## tools/office_tools/write.py

[查看源文件](../src/pc_manager_agent/tools/office_tools/write.py)。

五个固定 UUID 引用工具及单次分派权限；没有通用执行器、任意路径参数或命令接口。

### `capability_digest`

```python
capability_digest(authorization: ExecutionAuthorization) -> str
```

作用：Bind the dispatch to transaction, plan, preview, tool and exact arguments.

### `OfficeWriteGuard.__init__`

```python
OfficeWriteGuard.__init__(self, repository: OfficeRepository) -> None
```

作用：注入持久化单次消费存储；权限由编排服务签发，不接受模型或 UI 的批准声明。

### `OfficeWriteGuard.issue`

```python
OfficeWriteGuard.issue(self, reference_id: UUID, plan_id: UUID, preview_id: UUID, tool_name: str) -> ExecutionAuthorization
```

作用：Reserve one exact dispatch after the owning service consumes its user approvals.

### `OfficeWriteGuard.require`

```python
OfficeWriteGuard.require(self, authorization: ExecutionAuthorization, tool_name: str, arguments: Mapping[str, JsonValue]) -> None
```

作用：Reject arbitrary caller parameters and atomically consume one reserved call.

### `OfficeWriteTool.__init__`

```python
OfficeWriteTool.__init__(self, name: str, risk: RiskLevel, handler: Callable[[UUID, CancellationToken], UUID]) -> None
```

作用：验证工具名称和风险属于有限白名单，再绑定确定性回调；拒绝任意工具或低报风险。

### `OfficeWriteTool.manifest`

```python
OfficeWriteTool.manifest(self) -> ToolManifest
```

作用：Return this fixed writer's manifest; no generic command dispatch exists.

### `OfficeWriteTool.execute`

```python
OfficeWriteTool.execute(self, request: BaseModel, cancellation: CancellationToken) -> OfficeWriteResult
```

作用：Invoke one prepared operation after the registry consumes the dispatch capability.

## ui/office_tab.py

[查看源文件](../src/pc_manager_agent/ui/office_tab.py)。

界面只选择对象、显示本地内容/Diff、收集用户决定及调用领域服务。后台任务串行；高风险对话默认拒绝。

### `OfficeTab.__init__`

```python
OfficeTab.__init__(self, services: OfficeServices) -> None
```

作用：建立编辑、模型、历史页面及独有单线程池，连接交互信号；默认不选中文档或发送片段。

### `OfficeTab._button`

```python
OfficeTab._button(self, row: QHBoxLayout, text: str, action: Callable[[], None]) -> None
```

作用：创建按钮、连接指定 UI 回调并登记到任务忙碌时禁用的按钮集合。

### `OfficeTab._build_model_tab`

```python
OfficeTab._build_model_tab(self, tabs: QTabWidget) -> None
```

作用：建立片段选择、请求和模型建议展示控件；片段默认不勾选。

### `OfficeTab._build_history_tab`

```python
OfficeTab._build_history_tab(self, tabs: QTabWidget) -> None
```

作用：建立事务列表和独立恢复/Undo 入口，不自动恢复历史事务。

### `OfficeTab._choose_documents`

```python
OfficeTab._choose_documents(self) -> None
```

作用：让用户选择精确文件，生成 R0 读取计划并显示确认；批准后才进入后台读取。

### `OfficeTab._choose_documents.read`

```python
OfficeTab._choose_documents.read(token: CancellationToken) -> object
```

作用：后台读取回调：提交用户的计划决定，随后调用受控读取服务。

### `OfficeTab._read_completed`

```python
OfficeTab._read_completed(self, value: object) -> None
```

作用：把成功读取的结构化结果加入列表；新结果默认不勾选，不自动准备写入。

### `OfficeTab._show_document`

```python
OfficeTab._show_document(self) -> None
```

作用：显示当前选中结果的源引用和有界本地内容，供精确编辑/统计；不上传正文。

### `OfficeTab._selected`

```python
OfficeTab._selected(self) -> tuple[OfficeReadResult, ...]
```

作用：只收集用户明确勾选的会话文档对象，不根据名称替用户选择目标。

### `OfficeTab._choose_output`

```python
OfficeTab._choose_output(self) -> None
```

作用：选择单个输出路径，建立独立输出授权，并令旧预览失效；不立即创建目标文件。

### `OfficeTab._add_operation`

```python
OfficeTab._add_operation(self) -> None
```

作用：将编辑控件内容校验为有限类型操作并加入待预览列表；控件文本不是代码。

### `OfficeTab._prepare`

```python
OfficeTab._prepare(self) -> None
```

作用：从已选文档、输出授权和有限操作构造本地计划，后台生成 Preview，不写原文件。

### `OfficeTab._initial`

```python
OfficeTab._initial(self, format_: DocumentFormat, text: str) -> StructuredDocument
```

作用：把显式新建内容转换为结构化文档；DOCX 使用段落，XLSX 使用 CSV 表格输入，其他文本格式严格解析。

### `OfficeTab._preview_completed`

```python
OfficeTab._preview_completed(self, value: object) -> None
```

作用：校验后台结果类型并显示风险、目标、备份和完整本地差异；展示本身不批准操作。

### `OfficeTab._preview_summary`

```python
OfficeTab._preview_summary(self, preview: DocumentPreview) -> str
```

作用：读取事务元数据，生成具体目标、数量/大小、风险、恢复条件、备份和到期说明。

### `OfficeTab._backup`

```python
OfficeTab._backup(self) -> None
```

作用：启动独立 R1 备份确认流程，不同时确认编辑。

### `OfficeTab._backup.requested`

```python
OfficeTab._backup.requested(_: object) -> None
```

作用：备份请求准备好后显示默认拒绝的具体确认，再后台提交决定并接收新 Preview。

### `OfficeTab._confirm_plan`

```python
OfficeTab._confirm_plan(self) -> None
```

作用：请求领域服务完成 Fresh 检查并准备编辑计划确认。

### `OfficeTab._confirm_plan.requested`

```python
OfficeTab._confirm_plan.requested(_: object) -> None
```

作用：显示完整编辑计划摘要，记录用户批准或拒绝；R2 此时仍不能执行。

### `OfficeTab._execute`

```python
OfficeTab._execute(self) -> None
```

作用：R1 交给服务消费已批准计划；R2 先请求独立即时确认，不把普通通知当确认。

### `OfficeTab._execute.requested`

```python
OfficeTab._execute.requested(_: object) -> None
```

作用：收到 Fresh 即时请求后，显示本次具体对象、风险和恢复条件，收集用户决定。

### `OfficeTab._execute.requested.execute`

```python
OfficeTab._execute.requested.execute(token: CancellationToken) -> object
```

作用：先提交即时决定，仅在批准时调用受控执行服务；拒绝返回未执行结果。

### `OfficeTab._write_completed`

```python
OfficeTab._write_completed(self, value: object) -> None
```

作用：清除界面预览并展示真实事务状态及保留材料；不把未执行结果说成成功。

### `OfficeTab._invalidate`

```python
OfficeTab._invalidate(self) -> None
```

作用：取消待处理 Preview；已结束事务只清除本地展示。活动提交拒绝切换，不重用旧确认。

### `OfficeTab._editor_changed`

```python
OfficeTab._editor_changed(self) -> None
```

作用：无后台任务时，编辑内容变化立即使旧预览失效；将稳定错误显示给用户。

### `OfficeTab.set_user_goal`

```python
OfficeTab.set_user_goal(self, text: str) -> None
```

作用：Accept chat intent without choosing files, uploading content or preparing an edit.

### `OfficeTab.suggest_downloaded_document`

```python
OfficeTab.suggest_downloaded_document(self, path: Path) -> None
```

作用：只显示 Stage 5C 已验证下载的文件名/路径提示，不创建 READ/OUTPUT grant，不解析文档，也不
生成编辑计划。用户必须在 Stage 5A 重新选择同一文件并完成独立的读取、Preview 和确认流程。

### `OfficeTab._discard`

```python
OfficeTab._discard(self) -> None
```

作用：丢弃待预览编辑操作并取消其会话权限，不删除文件或恢复材料。

### `OfficeTab._show_chunks`

```python
OfficeTab._show_chunks(self) -> None
```

作用：为明确选中的已读文档列出有来源定位的片段，所有片段默认不勾选。

### `OfficeTab._prepare_model`

```python
OfficeTab._prepare_model(self) -> None
```

作用：仅使用用户勾选的片段和请求准备发送预览；未配置模型时明确拒绝，不隐式上传。

### `OfficeTab._disclosure_ready`

```python
OfficeTab._disclosure_ready(self, value: object) -> None
```

作用：显示精确供应商、模型、完整待发送内容、费用/保留提醒，收集独立发送确认。

### `OfficeTab._model_completed`

```python
OfficeTab._model_completed(self, value: object) -> None
```

作用：显示结构化建议/引用，明确没有执行权限；不自动应用模型提出的编辑。

### `OfficeTab._refresh_history`

```python
OfficeTab._refresh_history(self) -> None
```

作用：后台读取有界事务历史，结果回主线程展示。

### `OfficeTab._refresh_history.show`

```python
OfficeTab._refresh_history.show(value: object) -> None
```

作用：验证历史结果类型后显示状态、模式、目标及备份/临时/保留路径，保存精确事务 UUID。

### `OfficeTab._prepare_recovery`

```python
OfficeTab._prepare_recovery(self, undo: bool) -> None
```

作用：要求选中原事务、重新读取的当前文档和精确输出授权；先取消旧预览，再准备新的恢复或 Undo 计划。

### `OfficeTab._run`

```python
OfficeTab._run(self, task: Callable[[CancellationToken], object], completion: Callable[[object], None]) -> None
```

作用：同一 Office 页面只启动一个后台任务，禁用可变输入并连接完成/失败信号。

### `OfficeTab._completed`

```python
OfficeTab._completed(self, value: object) -> None
```

作用：主线程收回任务状态、恢复控件并执行类型明确的展示回调；异常转为稳定错误码。

### `OfficeTab._failure`

```python
OfficeTab._failure(self, code: str) -> None
```

作用：恢复界面可操作状态并解释稳定失败码，不展示可能含正文/凭据的异常文本，不自动重试。

### `OfficeTab._cancel_work`

```python
OfficeTab._cancel_work(self) -> None
```

作用：有活动任务时只发送协作取消请求；否则丢弃预览，不杀 Office 进程。

### `OfficeTab._ask`

```python
OfficeTab._ask(self, title: str, text: str) -> bool
```

作用：构造纯文本、默认 No 的显式确认对话；只有用户点 Yes 才返回批准。

### `OfficeTab.shutdown`

```python
OfficeTab.shutdown(self) -> bool
```

作用：Cancel owned work and wait boundedly; false means the caller must defer shutdown.

## ui/office_worker.py

[查看源文件](../src/pc_manager_agent/ui/office_worker.py)。

Qt 后台任务与安全信号转换。取消仅影响本任务，不终止 Word/Excel 或系统进程。

### `OfficeWorker.__init__`

```python
OfficeWorker.__init__(self, task: Callable[[CancellationToken], object]) -> None
```

作用：包装一个领域任务并创建本任务取消令牌和 Qt 信号，不在构造阶段执行任务。

### `OfficeWorker.run`

```python
OfficeWorker.run(self) -> None
```

作用：Convert all worker-boundary failures to stable codes without content/credentials.

### `OfficeWorker.cancel`

```python
OfficeWorker.cancel(self) -> None
```

作用：Cancel future work cooperatively; never terminate Word/Excel or unlock a file.
