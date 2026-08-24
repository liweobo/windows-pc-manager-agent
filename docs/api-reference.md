# API reference

## Stage 4D4 安全残留清理 API

本节逐一说明 Stage 4D4 新增或改变的生产对象、函数和方法。旧 `ResidualReport`、旧候选勾选和
Stage 4D3 R0 确认都只是用户意图；只有本节的 Fresh Revalidation、独立持久化计划、两级确认和
reference-only 写工具能产生 Windows 回收站操作。所有真实清理的恢复等级均为 `MANUAL`，没有
永久删除、注册表清理、自动 Restore 或 Shell 命令后备路径。

### 领域模型 `domain.residual_cleanup`

| 对象 / 函数 | 详细作用、输入输出和安全约束 |
|---|---|
| `CleanupEligibilityDecision` | 有限决策 `ELIGIBLE/BLOCKED/MANUAL_REVIEW`；只有 `ELIGIBLE` 能进入计划，用户确认不能覆盖另外两种结果。 |
| `ResidualCleanupAction` | 只有 `MOVE_TO_RECYCLE_BIN`；模型中不存在 DELETE、PURGE 或 permanent action。 |
| `ResidualCleanupTransactionState` | 表示 PREVIEW、两级确认、dispatch、逐项执行、验证和终态；`INTERRUPTED/BLOCKED/CANCELLED` 都没有自动继续边。 |
| `ResidualCleanupItemState` | 单项 `PLANNED→VALIDATING→TRASHING→VERIFYING` 及 VERIFIED/FAILED/BLOCKED_CHANGED/SKIPPED；用于真实呈现部分完成。 |
| `ResidualVerificationStatus` | 区分原 identity 消失、原 identity 消失但同路径出现新对象、原 identity 仍在和 UNKNOWN；不把 Shell 返回值直接当成功。 |
| `ResidualCleanupRequest` | 只接受一个 `source_report_id` 与显式、唯一的 candidate UUID 集合；没有 path/action 字段，因此 UI/LLM 不能提供最终执行路径。 |
| `ResidualCleanupRequest.require_unique_selection()` | Pydantic 构造期拒绝重复 UUID，防止同一对象重复进入批次。 |
| `ResidualClassificationCount` / `ResidualProtectionCount` | 对完整 Fresh tree 中分类与保护等级做隐私最小化计数。 |
| `ResidualMaterialSnapshot` | 保存 Stage 2B tree snapshot、文件/目录数、mtime 范围、分类/保护计数和禁止子项数；不读取内容。 |
| `ResidualMaterialSnapshot.validate_counts()` | 强制文件+目录数、分类计数和保护计数都等于 tree object count，并拒绝倒置时间范围。 |
| `ResidualMaterialSnapshot.canonical_digest()` | 哈希完整 material evidence，供 Preview、确认和最终 TOCTOU 对比。 |
| `ResidualPathSafetyDecision` | 记录 exact context path、普通用户访问、shared/reparse/network 等独立路径结论。 |
| `ResidualPathSafetyDecision.canonical_digest()` | 把所有 path gate 和 reason code 绑定到确认。 |
| `ResidualRecentActivityDecision` | 保存卸载完成时间、tree 最新 mtime、是否在卸载后更新以及原因。 |
| `ResidualRecentActivityDecision.canonical_digest()` | 绑定 recent-activity 事实；变化使确认失效。 |
| `ResidualRecoverabilityDecision` | 包装 Stage 2B volume capability，恢复固定 `MANUAL`；capability 不确定即不可执行。 |
| `ResidualRecoverabilityDecision.require_manual_recycle_bin()` | 拒绝把回收站恢复谎称 FULL 自动回滚。 |
| `ResidualRecoverabilityDecision.canonical_digest()` | 哈希文件系统/卷/回收站能力和恢复声明。 |
| `FreshResidualCandidate` | 从本地旧 UUID 解析后重新获得 identity、完整 material、分类、ownership、protection、path、activity 和 recoverability；旧 candidate 不能直接构造执行项。 |
| `FreshResidualCandidate.validate_authority()` | ELIGIBLE 时要求所有 fresh evidence 完整、路径一致、ownership HIGH、保护 NONE/CAUTION、路径安全、无近期活动、回收站可用且无禁止子项。 |
| `FreshResidualCandidate.invariant_digest()` | 排除观察时间，哈希全部执行相关事实，供多次扫描比较。 |
| `ResidualCleanupAssessment` | 保留原选择中每一行及其 decision；mixed batch 不会静默丢弃 blocked 项。 |
| `ResidualCleanupAssessment.validate_totals()` | 校验 selected/eligible/blocked/manual 数与实际 rows 完全一致。 |
| `ResidualCleanupAssessment.all_eligible` | 仅当原选择全为 ELIGIBLE 时返回 true。 |
| `ResidualCleanupAssessment.canonical_digest()` | 哈希包括临时 ID/时间的本次完整 assessment。 |
| `ResidualCleanupAssessment.invariant_digest()` | 排除 assessment ID/生成时间，保留 request 和每项 fresh evidence，供 plan revalidation。 |
| `PlannedResidualCleanupItem` | 为一个 Fresh candidate 分配内部 item/operation reference、顺序、固定 tool/action 和 MANUAL recovery。 |
| `PlannedResidualCleanupItem.require_eligible_candidate()` | 拒绝 blocked/manual candidate、错误工具名、错误 action 或虚假回滚级别。 |
| `PlannedResidualCleanupItem.canonical_digest()` | 哈希单项完整执行契约。 |
| `ResidualCleanupPlan` | all-eligible、无重叠、显式选择的有序 batch；保存影响总数、大小、动态 R2 风险和有效期。 |
| `ResidualCleanupPlan.validate_plan()` | 强制 R2/R2_HIGH、两级确认、MANUAL recovery、连续顺序、唯一 path/ref 和准确 material totals。 |
| `ResidualCleanupPlan.canonical_digest()` | 生成持久化、确认与写守卫使用的 canonical plan hash。 |
| `ResidualCleanupPreview` | 用户可见的短时 exact item set、identity/material 影响、风险和恢复摘要；只为可执行 batch 建立。 |
| `ResidualCleanupPreview.validate_preview()` | 校验有效期、item count、MANUAL recovery 和 executable=true。 |
| `ResidualCleanupPreview.canonical_digest()` | 哈希该次具体 Preview ID、内容和期限。 |
| `ResidualCleanupTrashRequest` | 写工具唯一输入：transaction/plan/Preview/item reference；没有本地路径、命令或自由参数。 |
| `ResidualCleanupTrashResult` | 低层返回原始 `FileState` 与 Windows Recycle Bin 证据，交给 service 独立验证。 |
| `ResidualCleanupItemResult` | 保存每项真实终态、identity-aware verification、可选回收证据/recovery ID 和用户消息。 |
| `ResidualCleanupExecutionReport` | 保存 batch 终态及 completed/failed/skipped 分项，防止把部分执行说成全成功或全取消。 |

### Eligibility、Fresh Revalidation 与 Preview

| 函数 / 方法 | 详细作用、输入输出和失败语义 |
|---|---|
| `ResidualCleanupPathPolicy.__init__(scope, ...)` | 注入 Stage 4D3 scope、网络检测、普通用户访问检测和测试 profile；不授予新 root。 |
| `validate_candidate(candidate, evidence)` | 只把 old UUID 映射到完全相同的 context root/selected path，再执行语法、protected root、reparse component、network、shared/system/access 检查；返回 normalized path + decision。 |
| `entry_rejection_reason(path, selected_root)` | 对 tree 每个后代检查 scope escape、保护路径、network 和 reparse；任一原因阻止整个候选。 |
| `is_shared(path, evidence)` | 识别显式 shared signal、Common Files/Public/Shared 和过宽 ProgramData/Program Files root。 |
| `ResidualRecentModificationPolicy.evaluate(context, material)` | 比较完整 tree 最新 mtime 与 durable uninstall completion；V1 对卸载后活动 fail closed。 |
| `CleanupEligibilityPolicy.evaluate(...)` | 纯 Python 有限矩阵：仅 HIGH 的 PROGRAM_RESIDUAL/CACHE/LOG/SHORTCUT 且所有独立 gate 通过才 ELIGIBLE；配置、用户数据、数据库、插件、MSIX 数据、未知、shared/recent/reparse/unsupported volume 全部阻止。 |
| `CleanupRiskPolicy.__init__(...)` | 接受可配置的普通 item/object/total/single-size 阈值，非法或非正配置直接拒绝。 |
| `CleanupRiskPolicy.classify(...)` | 任一阈值超出即 `R2_HIGH_IMPACT`，否则 R2；分类不降低 Eligibility 要求。 |
| `FreshResidualRevalidator.__init__(...)` | 注入 report repository、所有本地安全策略、identity/recycle adapters 和 hard batch/object/byte budgets。 |
| `assess(request, cancellation)` | 只读取指定 report 的 selected UUID，拒绝跨报告/未知项，逐项做完整 metadata scan，保留 blocked rows 并审计前返回 Assessment。 |
| `require_unchanged(expected, cancellation)` | 对一个内部 planned candidate 再做完整 scan，要求仍 ELIGIBLE 且 invariant digest 完全相同；最终 Shell 调用前使用。 |
| `_assess_candidate(...)` | 顺序执行 old identity、path、fresh identity、material、classification、ownership、protection、activity、capability 和 eligibility；可预期异常转换为 blocked row，不猜测。 |
| `_snapshot(source, evidence, cancellation)` | 使用显式 stack + `scandir/lstat` 完整遍历 selected tree，按路径排序构建稳定 digest，统计 hidden/system/reparse/offline/大小/mtime/分类/保护；不跟链接、不读内容、超限即阻止。 |
| `_classify(path, evidence)` | 只按 selected root 及其相对 descendants 分类，避免外部父目录名错误影响候选，也不观察 sibling/parent。 |
| `_protect(path, classification, evidence)` | 默认复用 UserDataProtectionPolicy；唯一窄例外是卸载前记录了 exact path+target 的 shortcut，仍仅降到 CAUTION，后续还要求 target 已不存在。 |
| `_evidence_for(candidate, context)` | 要求 candidate scan root/source 在 context 中有且只有一条 exact evidence。 |
| `_old_identity_matches(candidate)` | 用 `lstat` 比对 normalized path、device/inode、类型、大小和 mtime，并拒绝 reparse/symlink，阻止同路径新对象继承旧授权。 |
| `_blocked(...)` | 构造不携带 fresh execution capability 的 BLOCKED row，分类/ownership/protection 均采用保守 UNKNOWN。 |
| `ResidualCleanupPreviewEngine.__init__(...)` | 注入 revalidator、risk policy 以及 plan/Preview TTL。 |
| `compile(assessment)` | mixed/blocked 或 parent-child overlap 直接拒绝；仅从全 eligible rows 生成固定 MOVE_TO_RECYCLE_BIN plan/Preview。 |
| `revalidate(plan, request, cancellation)` | 第二/第三次完整扫描，逐 UUID 与 invariant digest 比较；identity/material/classification/protection/capability/risk 变化全部拒绝。 |
| `require_current(plan, preview)` | 校验 plan/Preview 有效期、IDs、canonical digest、exact item-set digest 和 risk。 |
| `_preview_for_plan(plan)` | 生成受 plan expiry 限制的新短时 Preview。 |
| `_item_set_digest(items)` | 哈希 item/operation refs、顺序、candidate UUID/invariant、action/tool，防止增删换序。 |
| `_reject_overlapping_items(assessment)` | 拒绝同时选择目录及其 child，避免重复/模糊 Shell 操作。 |
| `ResidualCleanupSafetyValidator.review(plan)` | 独立检查 risk/recovery/action/evidence，以及 registry manifest 必须是唯一 residual trash 工具、max R2_HIGH、支持当前 risk、Preview 和两级确认。 |

### 两级确认与持久化

| 函数 / 方法 | 详细作用、输入输出和失败语义 |
|---|---|
| `ResidualCleanupConfirmationTier/State` | 区分 PLAN/RUNTIME 和 PENDING/APPROVED/REJECTED/EXPIRED/CONSUMED；两级是独立 durable records。 |
| `ResidualCleanupConfirmation` | 绑定 transaction/plan/Preview/item set、identity、material、classification、eligibility、recovery capability、数量/大小/risk/object summary 和 expiry。 |
| `ResidualCleanupConfirmationStore.save/get/update/consume_confirmation_pair` | Protocol：持久保存、精确读取、状态更新并原子消费 parent+runtime approval。 |
| `ResidualCleanupConfirmationService.__init__(...)` | 注入 store、Preview engine、两种 TTL 和可测试时钟。 |
| `request_plan(plan, preview)` | 先要求 current Preview，再创建第一级 pending approval。 |
| `resolve(id, approved, plan, preview)` | 只解析 PENDING、未过期、全 binding 相同的确认；重复点击、changed plan/Preview 会拒绝。 |
| `request_runtime(plan_confirmation_id, plan, runtime_preview)` | 只有仍有效且 APPROVED 的 parent 才能为新 Fresh Preview 创建短时即时确认。 |
| `consume_runtime(runtime_id, plan, preview)` | 最后检查 parent/runtime bindings 与 expiry，再通过 store 原子消费两条记录；只能成功一次。 |
| `_create(...)` | 计算所有证据 digest、具体对象/大小/MANUAL 文案和受 Preview 限制的 expiry。 |
| `_require_binding(...)` | 比较所有 ID/digest/count/size/risk；PLAN 可接受 invariant 相同的新 runtime Preview，但不能接受 item/evidence 变化。 |
| `_require_not_expired(...)` | 到期时先 durable 标记 EXPIRED 再抛确认错误。 |
| `_identity_digest/_material_digest/_classification_digest/_eligibility_digest/_recovery_digest` | 分别哈希五组安全事实，使任何独立维度变化都能失效确认。 |
| `ResidualCleanupRepository.__init__(database_path)` | 建立与 Stage 4D3 report、Stage 2B transaction 隔离的 engine/session；尚未建表。 |
| `initialize()` | 创建 transaction/item/confirmation 表；重启时把所有非终态 batch 标记 INTERRUPTED、PENDING/APPROVED 确认标记 EXPIRED，并返回 interrupted IDs。 |
| `create(plan, preview)` | 一个事务内保存 immutable plan、Preview 与全部 reference-only items；已有 active batch 时拒绝。 |
| `bind_runtime_preview(plan, preview)` | 仅 PLAN_CONFIRMED 状态可替换为第二次 Fresh Preview，并重新计算每项 reference request digest。 |
| `save_confirmation/get_confirmation/update_confirmation` | 持久化、读取和解析确认决定；PLAN approval 推进状态，reject/expiry 终止 batch。 |
| `consume_confirmation_pair(plan_confirmation, runtime_confirmation)` | 在一个 SQLite transaction 中检查两者仍 APPROVED、同时改 CONSUMED 并把 batch 预留为 DISPATCHING。 |
| `begin_item_validation(transaction_id, item_ref)` | 只允许当前 batch 的下一个 PLANNED item 进入最终验证。 |
| `prepare_recovery(item_ref, recovery)` | Shell 前写入 PREPARED MANUAL recovery evidence 和 integrity digest。 |
| `ResidualCleanupExecutionGuard.require(...)` | ToolRegistry 写边界：原子比较 authorization、plan/Preview/operation/item refs、argument digest、consumed runtime confirmation 和 item state，再标记 TRASHING/EXECUTING。 |
| `mark_item_verifying(item_ref)` | 仅 Shell 已派发的 TRASHING item 能进入 VERIFYING。 |
| `complete_item(result, recovery)` | 保存 terminal result 和可选 recovery evidence；非 terminal state 拒绝。 |
| `skip_pending(transaction_id, message)` | failure/cancel 后只把未来 PLANNED rows 标为 SKIPPED，不改写已执行事实。 |
| `transition(transaction_id, state, error_message)` | 推进 batch；终态不能重新打开为另一个状态。 |
| `state/load_plan/load_preview/load_item` | 精确加载 durable typed state；cross-transaction item ref 拒绝。 |
| `list_results(transaction_id)` | 按顺序返回已有 terminal rows，不为未触及 interrupted item 伪造结果。 |
| `list_recovery(transaction_id)` | 校验 recovery payload digest 后返回内部全部记录；公开 service 只显示 AVAILABLE 且有 recycle identifier 的记录。 |
| `request_for_item(plan, preview_id, item)` | 生成唯一 reference-only tool request，不暴露 path。 |
| `_item_row/_confirmation_row` | 把 typed model 转成带 digest/state 的 SQL rows。 |
| `_transaction/_item/_require_initialized` | 内部精确 lookup 和 fail-closed repository health gate。 |
| `close()` | dispose engine 并禁用后续访问。 |

### Recycle Bin 工具、编排、验证与审计

| 函数 / 方法 | 详细作用、输入输出和失败语义 |
|---|---|
| `VerifiedRecycleBinExecutor.__init__(identity_platform, recycle_platform)` | Stage 2B 与 4D4 共用的 identity-aware Recycle Bin primitive；没有 permanent API dependency。 |
| `recycle(source, expected_state, expected_snapshot, snapshotter, cancellation)` | Shell 前检查取消、identity、完整 tree digest，再检查取消并调用一次 `RecycleBinPlatform.recycle`；失败直接上抛，无 unlink/rmtree fallback。 |
| `SoftwareResidualPrepareCleanupTool.manifest` | `software.residuals.prepare_cleanup`：R0、read-only、可取消，只接受 report/candidate references。 |
| `SoftwareResidualPrepareCleanupTool.execute(...)` | 强校验 request 后调用 Fresh assessment。 |
| `SoftwareResidualTrashTool.manifest` | `software.residuals.trash`：manifest 最大 R2_HIGH、允许实际 R2/R2_HIGH、双确认、Preview、MANUAL、batch 1。 |
| `SoftwareResidualTrashTool.execute(...)` | 从 durable item ref 内部解析 path/evidence，调用 `require_unchanged` 和 shared executor，返回 Shell 证据；调用者不能改 path。 |
| `PreparedResidualCleanup` | 把 request/assessment/plan/review/Preview/第一级 pending confirmation 作为不可变 UI state。 |
| `RuntimeResidualCleanup` | 把第二次 Fresh Preview 与 pending immediate confirmation 组合。 |
| `ResidualCleanupService.__init__(...)` | 组合 registry、Preview、独立 reviewer、confirmation、transaction repository、identity verifier 与 mandatory audit。 |
| `assess(request, cancellation)` | 只经 registered R0 tool 做 Fresh scan 并记录 aggregate audit。 |
| `prepare(assessment)` | 编译 all-eligible plan、独立 review、durable create、创建 PLAN confirmation 并审计；尚不调用 Recycle Bin。 |
| `resolve_plan_confirmation(prepared, approved)` | 持久解析第一级决定并审计。 |
| `request_runtime_confirmation(prepared, cancellation)` | 要求 PLAN_CONFIRMED，做第二次 Fresh scan；变化时把 batch BLOCKED 并审计，不允许旧 active plan 卡住或重用。 |
| `resolve_runtime_confirmation(runtime, approved)` | 持久解析对象级即时决定并审计；仍不执行。 |
| `execute(runtime, cancellation)` | 要求 exact awaiting-runtime state；第三次 whole-batch revalidation 后原子消费两级确认，再逐项 write-ahead recovery→mandatory audit→guarded tool→identity verify；failure/change/cancel 停止未来 items。 |
| `recovery_records(transaction_id)` | 只返回 VERIFIED 成功项的 AVAILABLE MANUAL recovery records；失败项不会显示虚假恢复能力。 |
| `_verify_result(...)` | 结合 non-aborted HRESULT、recycle identifier 与原 path fresh identity；区分同 identity、不同新 identity、已不存在和 inspect unknown。 |
| `_complete_pre_dispatch_failure(...)` | 对缺失 fresh evidence 的 item 保存 BLOCKED_CHANGED 并审计，不派发 Shell。 |
| `_finalize(...)` | 从 durable per-item rows 计算 COMPLETED/PARTIALLY_COMPLETED/CANCELLED/FAILED，保存终态并返回 truthful report。 |
| `ResidualCleanupAuditLogger.assessed/previewed/confirmation_resolved` | 记录 selected UUID、aggregates、plan/Preview/证据 digests 和确认状态；不存内容或明文路径。 |
| `workflow_blocked(...)` | 记录 runtime/final revalidation fail-closed phase 和异常类型，不记录可能含本地数据的错误文本。 |
| `item_started(...)` | mandatory pre-dispatch audit；记录 path digest、identity metadata 和 PREPARED recovery，写失败会阻止 Shell。 |
| `item_completed(...)` | 保存脱敏 per-item terminal evidence；排除 source path、Recycle Bin identifier 和 Shell text。 |
| `transaction_completed(...)` | 保存 batch totals 与脱敏 item summaries。 |
| `_path_digest(path)` | 对 normcase+abspath 做 SHA-256，仅用于本地审计关联，不作为授权。 |
| `_item_result_payload(result)` | 生成无 path/identifier/Shell text 的 audit JSON。 |

### Qt UI、Runtime 与配置

| 函数 / 方法 | 详细作用、输入输出和失败语义 |
|---|---|
| `ResidualCleanupPrepareWorker.run/cancel` | 后台做第一次 Fresh assessment/plan；mixed batch 返回 blocked rows，取消协作停止 metadata traversal。 |
| `ResidualCleanupRuntimeWorker.run/cancel` | 后台做第二次 Fresh scan 并创建即时确认；不写文件。 |
| `ResidualCleanupExecuteWorker.run/cancel` | 后台运行逐项 transaction；取消只停止未来项，不强行打断正在进行的 Shell 调用。 |
| `require_cleanup_prepared/require_cleanup_runtime/require_cleanup_report` | Qt signal payload type guards；错误 worker payload 显式失败。 |
| `ResidualCleanupDialog.__init__()` | 以 old report UUID selection 启动独立 Fresh workflow，默认取消，窗口不直接调用工具。 |
| `_build_ui()` | 构建 eligibility 表、影响/风险/MANUAL 文案和两个阶段复用的显式按钮；不存在 permanent delete。 |
| `_start_prepare/_prepared_completed` | 启动后台扫描；blocked/mixed 显示逐项原因并停止，all-eligible 才显示第一次确认。 |
| `_show_plan_confirmation/_primary_clicked` | 先处理 PLAN approval+第二次扫描，再处理 RUNTIME approval+后台 execution；一次点击不能跨越两级。 |
| `_runtime_prepared/_execution_completed` | 显示即时对象数量/大小/风险/手动恢复，以及真实 success/failure/skipped 结果。 |
| `_populate_assessment` | 展示 Fresh classification、ownership、protection、file/dir/size、recoverability 和 reason codes。 |
| `_failed/_cancel_clicked` | 默认安全停止；执行中 cancel 只标记后续 item，确认阶段 cancel durable 记录 reject。 |
| `_require_prepared/closeEvent` | 检查内部 state；关闭时协作取消 worker，不后台自动继续。 |
| `_format_size/_completion_html` | 只负责显示；completion 明确 MANUAL recovery 和“没有永久删除”。 |
| `ResidualAnalysisDialog._update_cleanup_button/_selected_cleanup_ids/_open_cleanup` | Stage 4D3 页面只把用户勾选转换成 UUID intent 并打开独立 D4 dialog；不传 path、不产生 R2 token。 |
| `_potential_cleanup_intent(candidate)` | 只隐藏旧报告中明显受保护项；结果不声称 eligible，Fresh policy 仍是唯一 authority。 |
| `ApplicationRuntime.create_residual_cleanup_services()` | 组合独立 repository、Fresh policies、两个 registered tools、write guard、confirmation、audit 与 Windows identity/recycle adapters。 |
| `ApplicationRuntime.close()` | 同时关闭 D4 repository；restart initialization 已先把 active work标为 INTERRUPTED。 |
| `Settings.residual_cleanup_*` | 配置 hard selected/object/byte budgets、R2 normal thresholds 和 runtime confirmation TTL；变更会影响新计划，不会修改既有 confirmed plan。 |
| `ToolManifest.allowed_risk_levels` / `supports_risk(risk)` | 支持一个 manifest 声明安全最大风险并有限允许 R2/R2_HIGH dynamic plan risk；R0/R1/R3/R4 混用在模型校验期拒绝。 |

## Stage 4D2C1 受控 winget Package 卸载 API

本节逐一说明 Stage 4D2C1 新增的生产对象、函数和方法。执行边界只有
`software.uninstall.winget`：调用方不能传入命令、可执行文件、Source URL 或自由参数；Windows
适配器只接受内部生成的 `ValidatedWingetUninstallAction`，再由代码生成唯一参数数组。

### 领域模型 `domain.winget_uninstall`

| 对象 / 函数 | 详细作用、输入输出和安全约束 |
|---|---|
| `WingetAvailabilityState` | `AVAILABLE` 表示 App Installer 别名身份已证明；`UNAVAILABLE` 表示未安装/环境缺失；`UNTRUSTED` 表示存在但身份不可证明。后两者都不能确认执行。 |
| `WingetInventoryState` | 表示 JSON Package 清单完整、失败或因安全数量上限截断；只有 `COMPLETE` 能参与执行。 |
| `WingetMappingConfidence` | Package 与 Installed Software 的 HIGH/MEDIUM/LOW/NONE 关系；只有唯一 `HIGH` 可执行。 |
| `WingetCapabilityDecision` | 只描述 winget 机制是 SUPPORTED/BLOCKED/UNSUPPORTED，不替代软件安全分类。 |
| `WingetExecutionDecision` | 最终软件策略的 ALLOW/BLOCK；用户确认不能覆盖 BLOCK。 |
| `WingetPreflightState` | 只读进程、服务、winget busy 与全局事务检查的 READY/BLOCKED/UNKNOWN。 |
| `WingetProcessResultCategory` | 记录进程退出 0/非 0、启动失败、权限/重启证据、启动前取消或停止监控；不是卸载成功结论。 |
| `WingetVerificationState` | Package+Software 双重刷新结论，区分双方消失、单方仍存在、清单未知、仍安装、实例变化、中断与失败。 |
| `WingetUninstallTransactionState` | durable Preview→两级确认→dispatch→execute→verify 生命周期；`INTERRUPTED` 没有自动重试边。 |
| `WingetExecutableIdentity` | 保存精确 alias path、App Installer full/family name、alias target、reparse tag、大小/时间与 reparse bytes SHA-256。 |
| `WingetExecutableIdentity.require_desktop_app_installer()` | 构造后校验 family 必须为 `Microsoft.DesktopAppInstaller_8wekyb3d8bbwe`，target 仅允许 winget/AppInstallerCLI。 |
| `WingetExecutableIdentity.invariant_digest()` | 排除观察时间后哈希稳定 alias 事实，用于 TOCTOU 重验和确认绑定。 |
| `WingetAvailability` | 将 availability state、可选 executable identity 和可显示原因组合为不可变结果。 |
| `WingetAvailability.bind_state_to_identity()` | 强制只有 AVAILABLE 才能携带 executable，避免“不可用但仍有执行身份”的矛盾对象。 |
| `RawWingetPackage` | `winget export` 的短生命周期输入：Package ID、版本、源名、源标识和范围；不保存 Source URL。 |
| `WingetPackageIdentity` | 执行核心身份：Package ID、已安装版本、官方源名/标识、current-user scope。 |
| `WingetPackageIdentity.require_narrow_identity()` | 拒绝空白/控制/命令字符、自定义源、Store 源、未知 source identifier 与 machine scope。 |
| `WingetPackageIdentity.canonical_digest()` | 哈希 Package ID、版本、源和范围；任何变化使旧确认失效。 |
| `NormalizedWingetPackage` | 安全 UI/策略投影，只暴露绑定后的 ID 与版本。 |
| `NormalizedWingetPackage.bind_visible_fields()` | 防止 UI 显示版本/ID 与实际执行 identity 不同。 |
| `WingetPackageInventory` | 有界 Package tuple、采集时间、完整性状态与警告。 |
| `WingetPackageQuery` | 只允许 identity digest 或明确 Package ID/版本选择，不提供 Package Name 模糊执行。 |
| `WingetPackageQuery.require_selector()` | 拒绝无选择器查询并先校验 Package ID 字符集。 |
| `ResolvedWingetPackage` | 返回唯一 selected 或显式 candidates/ambiguous；不自动选“最像”的包。 |
| `ResolvedWingetPackage.validate_resolution()` | 强制 selected 与 candidates/ambiguous 互斥。 |
| `WingetSoftwareMapping` | 绑定 Package digest、可选 Software digest、置信度、证据与警告。 |
| `WingetSoftwareMapping.executable` | 仅当 HIGH 且有唯一 Software digest 时为 true。 |
| `WingetSoftwareMapping.canonical_digest()` | 将 mapping 事实绑定到计划和两次确认。 |
| `WingetCapabilityAssessment` | 保存机制决定、理由、Package digest 与可信 executable digest。 |
| `WingetCapabilityAssessment.canonical_digest()` | 生成机制能力摘要。 |
| `WingetExecutionAssessment` | 保存 safety class、ALLOW/BLOCK、R2/R2_HIGH 风险、理由和证据。 |
| `WingetExecutionAssessment.require_r2_for_allow()` | ALLOW 只允许 R2/R2_HIGH_IMPACT；R3/R4 不能伪装成可执行 Preview。 |
| `WingetExecutionAssessment.canonical_digest()` | 绑定软件策略事实。 |
| `WingetRelatedProcess` | 相关进程的 PID、名称和路径摘要；结构中没有 terminate 权限。 |
| `WingetRelatedService` | 相关服务的名称/显示名/状态；结构中没有 stop 权限。 |
| `WingetExecutionPreflight` | 保存 probe 完整性、相关对象、winget busy、全局 transaction、blocker 与 warning。 |
| `WingetExecutionPreflight.canonical_digest()` | 将全部运行态事实绑定到确认。 |
| `WingetUninstallPlan` | 单 Package、单工具、双确认、Rollback NONE 的不可变计划，只持有安全摘要。 |
| `WingetUninstallPlan.validate_contract()` | 强制工具名、R2 风险、两级确认与不可自动回滚。 |
| `WingetUninstallPlan.canonical_digest()` | 哈希所有授权相关字段。 |
| `WingetUninstallPreview` | 本地短时 Preview，包含 Package、Software、mapping、winget identity、capability、policy、preflight 和恢复说明。 |
| `WingetUninstallPreview.bind_evidence()` | 校验有效期、Package/Software mapping、所有 ALLOW/READY 条件、executable 布尔与 Rollback NONE 一致。 |
| `WingetUninstallPreview.invariant_digest()` | 排除刷新时间/Preview ID，保留所有执行事实，供第二次确认重现。 |
| `WingetUninstallPreview.canonical_digest()` | 哈希该次具体 Preview，包括 ID 和有效期。 |
| `ValidatedWingetUninstallAction` | 写适配器唯一输入；只有 transaction、Package identity、Software digest、可信 executable identity 和验证时间，没有 argv 字段。 |
| `ValidatedWingetUninstallAction.canonical_digest()` | 哈希 typed action。 |
| `WingetUninstallRequest` | ToolRegistry 输入，绑定 transaction/operation/plan/Preview ID 与 validated action。 |
| `WingetProcessExecutionResult` | 保存启动/监控/退出证据、PID、exit code、时间与错误类别。 |
| `WingetUninstallResult` | 工具输出：两个 identity digest 与 process evidence，不声称卸载成功。 |
| `WingetResidualReport` | 只报告 exact install path 是否存在/重解析；`deletion_performed` 固定 false。 |
| `WingetUninstallVerification` | Package 与 Software 两个清单是否完整刷新、原身份是否存在、证据与警告。 |
| `WingetUninstallExecutionReport` | GUI/audit 最终报告，分开呈现 process、dual verification、residual 和 recovery guidance。 |
| `fixed_winget_uninstall_arguments(identity)` | 从已验证 identity 生成唯一 flags：`uninstall --id … --exact --source winget --version … --scope user --interactive --disable-interactivity`；调用方不能追加 flag。 |

### 平台协议与 Windows 适配器

| 函数 / 方法 | 详细作用、输入输出和失败语义 |
|---|---|
| `WingetAvailabilityPlatform.inspect()` | Protocol：只读发现可信 App Installer alias；实现不得搜索 PATH。 |
| `WingetPackageInventoryPlatform.inventory(max_items, cancellation)` | Protocol：返回有界结构化清单，失败必须显式。 |
| `WingetUninstallPlatform.uninstall(action, cancellation)` | Protocol：仅接受 validated action；启动后取消只能停止监控。 |
| `_Process.poll()` / `_PopenFactory.__call__()` | 内部依赖注入协议，使测试能证明 argv、环境和 `shell=False`，而不启动真实 winget。 |
| `WindowsWingetAvailabilityPlatform.__init__(local_app_data)` | 默认读当前进程 `LOCALAPPDATA`；测试可注入根目录。 |
| `WindowsWingetAvailabilityPlatform.inspect()` | 只检查固定 `%LOCALAPPDATA%\Microsoft\WindowsApps\winget.exe`，直接读 AppExecLink reparse data 并验证 App Installer family；任何异常返回 UNTRUSTED。 |
| `WindowsWingetPackageInventoryPlatform.__init__(availability, data_directory, timeout_seconds)` | 注入 alias 身份服务、Agent 数据目录和有限超时。 |
| `WindowsWingetPackageInventoryPlatform.inventory(max_items, cancellation)` | 在 Agent 临时目录运行只读 `winget export`，DEVNULL 标准流、脱敏环境、`shell=False`；只解析有界 JSON，失败返回 FAILED。 |
| `IndependentWingetSoftwarePackageProvider.collect(...)` | Installed Software 清单的无重复占位：Package 清单由独立 typed service 提供，因此这里不制造同一包的第二个 Software identity，也不报告虚假缺失。 |
| `WindowsWingetUninstallPlatform.__init__(availability, poll_seconds, process_factory, environment)` | 注入 alias 重验、监控间隔、测试进程工厂和环境来源。 |
| `WindowsWingetUninstallPlatform.uninstall(action, cancellation)` | 先比较 fresh executable invariant，再以 absolute executable、固定 tuple、固定 cwd、DEVNULL、allow-list env、`close_fds=True`、`shell=False` 启动一次；从不 terminate/kill/retry/elevate/restart。 |
| `_local_app_data()` | 只读取 `LOCALAPPDATA` 并转成 Path；缺失返回 None。 |
| `_read_app_execution_alias(path)` | 用 `CreateFileW(FILE_FLAG_OPEN_REPARSE_POINT)` 与 `DeviceIoControl(FSCTL_GET_REPARSE_POINT)` 读取 `IO_REPARSE_TAG_APPEXECLINK`；只解析身份，不跟随成任意命令。 |
| `_package_family_from_full_name(full_name)` | 从 full package name 保守导出 family，并要求精确 Desktop App Installer family。 |
| `_parse_export(path, max_items)` | `lstat` 检查 regular/bounded JSON，忽略 Source URL/未知字段，只接受官方 source name+identifier；非官方 Source 整体丢弃，官方 Source 内不完整或危险记录产生警告。 |
| `_sanitized_environment(source)` | 仅保留普通 Windows 运行变量；丢弃 PATH、API key、token 和 winget 自定义变量。 |
| `_process_result(...)` | 统一构造进程事实和 monotonic duration，不把 exit code 解释成 success。 |

### 只读库存、解析、映射和 Preflight

| 函数 / 方法 | 详细作用 |
|---|---|
| `WingetAvailabilityService.__init__(platform)` | 注入只读 alias 平台。 |
| `WingetAvailabilityService.inspect()` | 返回当前 alias 证据。 |
| `PackageInventoryService.__init__(platform)` | 注入结构化 Package provider。 |
| `PackageInventoryService.inventory(max_items, cancellation)` | 创建缺省 cancellation token 并返回 bounded inventory。 |
| `PackageTargetResolver.__init__(inventory)` | 注入 fresh inventory service。 |
| `PackageTargetResolver.resolve(query, max_items, cancellation)` | 每次重新采集并按 digest/Package ID/版本精确解析。 |
| `PackageTargetResolver.inspect(identity_digest, max_items, cancellation)` | 用于运行前/运行后的 exact identity 检查。 |
| `PackageTargetResolver.resolve_from_inventory(query, inventory)` | 纯函数式解析；partial inventory、零个或多个匹配都返回 ambiguous。 |
| `_unresolved(query, candidates, reason)` | 构造有界 unresolved 结果。 |
| `WingetSoftwareMapper.map(package, software_entries)` | 只有 Package ID、`winget` manager、版本、current-user scope 唯一匹配才返回 HIGH；名称相似最多 MEDIUM，不能执行。 |
| `_same(left, right)` | 仅用于产生启发式警告的规范化文本相等，不参与 HIGH 授权。 |
| `WingetExecutionPreflightService.__init__(platform, max_items)` | 注入只读 Stage 3 diagnostics。 |
| `WingetExecutionPreflightService.inspect(software, cancellation, another_uninstall_active)` | exact install-root 关联进程/服务；进程仅 warning，running service、winget busy、probe incomplete、取消或 active transaction 均 block。 |
| `_canonical(path)` | `abspath`+`normcase` 规范文本，不跟随 reparse。 |
| `_is_within(path, root)` | 用路径组件关系验证 containment，避免前缀绕过。 |
| `WingetResidualAnalyzer.analyze(install_location)` | 只对 exact path 做一次 `lstat`；不枚举、跟随或删除。 |

### 能力、安全策略、Preview 和审查

| 函数 / 方法 | 详细作用 |
|---|---|
| `WingetCapabilityPolicy.assess(package, mapping, availability)` | 独立判断官方源机制、HIGH mapping 与可信 alias 是否齐全；不读取软件安全 class。 |
| `WingetUninstallPolicy.assess(scope, analysis)` | current-user USER_APPLICATION/DEVELOPER_TOOL 为 R2；developer runtime/database/background 为 R2_HIGH；shared/driver/hardware/Windows/security/network/Agent/enterprise/package-manager/unknown 全部 BLOCK。 |
| `_blocked(analysis, reason)` | 统一构造安全的 R2/BLOCK 结果，确保“winget 支持”不能绕过 class。 |
| `WingetUninstallPreviewEngine.__init__(ttl_seconds)` | 配置正数 Preview TTL。 |
| `WingetUninstallPreviewEngine.build(...)` | 比较 plan 内七类 evidence digest，随后生成 expiring Preview；摘要不同直接异常。 |
| `WingetUninstallSafetyError` | 运行时 identity/policy/Preview 变化的 fail-closed 错误。 |
| `WingetUninstallSafetyValidator.validate(plan, approved, fresh)` | 比较完整 approved/fresh Preview invariant、有效期、计划与 executable 状态。 |
| `WingetUninstallSafetyValidator.validate_invariant(plan, approved_digest, fresh)` | 使用第一确认已持久化的 invariant digest 独立验证 fresh Preview。 |

### 两级确认 `confirmation.winget_uninstall`

| 函数 / 方法 | 详细作用 |
|---|---|
| `WingetUninstallConfirmationTier` / `WingetUninstallConfirmationState` | 区分 PLAN/RUNTIME 与 pending/approved/rejected/expired/consumed；consumed 不能重放。 |
| `WingetUninstallConfirmation` | 绑定 parent、transaction/operation/plan/Preview、plan/preview/invariant/Package/Software/mapping/executable/capability/safety/preflight digests、risk、对象摘要和过期时间。 |
| `WingetConfirmationStore.save_plan_confirmation()` | Protocol：durable 保存第一 gate。 |
| `WingetConfirmationStore.save_runtime_confirmation()` | Protocol：保存 fresh Preview 和短时 gate。 |
| `WingetConfirmationStore.get_confirmation()` | Protocol：读取 durable capability。 |
| `WingetConfirmationStore.resolve_confirmation()` | Protocol：持久化用户决定或 expiry。 |
| `WingetConfirmationStore.consume_confirmation_pair()` | Protocol：原子消费 parent-child pair。 |
| `WingetUninstallConfirmationError` | stale、mismatch、expiry、absent 或 replay 的安全错误。 |
| `WingetUninstallConfirmationService.__init__(store, plan_ttl_seconds, runtime_ttl_seconds, now)` | 注入 durable store、两个正 TTL 与测试时钟。 |
| `request_plan(plan, preview)` | 要求当前 executable Preview，创建并保存第一确认。 |
| `resolve_plan(id, approved, plan, preview)` | 核对 tier/state/expiry/全部 binding，再保存批准或拒绝。 |
| `request_runtime(parent_id, plan, preview)` | 要求已批准 parent 与相同 fresh invariant，创建短时即时确认。 |
| `resolve_runtime(id, approved, plan, preview)` | 保存对象级即时决定。 |
| `consume_runtime(id, plan, preview)` | 重新校验 parent/child 与有效期，通过 store 原子消费，只授权一次 dispatch。 |
| `_resolve(...)` | PLAN/RUNTIME 共用 pending→approved/rejected 逻辑。 |
| `_create(...)` | 生成 digest-only confirmation；不复制命令、path、URL 或环境。 |
| `_require_not_expired(confirmation)` | 到期即 durable 标 EXPIRED 并拒绝。 |
| `_require_executable(plan, preview)` | 核对 plan/transaction/operation/identity/risk 与 Preview。 |
| `_require_current(confirmation, plan, preview)` | 逐字段比较所有 confirmation binding。 |

### 持久事务、写 Guard、Tool 与审计

| 函数 / 方法 | 详细作用 |
|---|---|
| `WingetUninstallRepository.__init__(database_path)` | 创建独立 SQLite engine/session；路径由运行时传入用户数据目录。 |
| `initialize()` | 建表、把重启前非终态改为 INTERRUPTED、过期所有 pending/approved gate；从不 redispatch。 |
| `create(plan, preview)` | 检查 MSI/Vendor/winget 全局互斥后写 PREVIEWED 与 exact request digest。 |
| `has_active_uninstall(exclude_winget_transaction)` | 查询三种卸载表的非终态；runtime 可排除自身。 |
| `transition(...)` | 按 allow-list 推进状态并只保存最小 process/verification 事实。 |
| `state(transaction_id)` | 读取当前 durable 状态。 |
| `save_plan_confirmation()` / `save_runtime_confirmation()` | 原子保存 gate，并在 runtime gate 时更新 fresh Preview/request binding。 |
| `get_confirmation()` | 从 JSON 载荷恢复 typed confirmation，并以 row state 为准。 |
| `resolve_confirmation()` | 原子更新 gate 与对应 transaction。 |
| `consume_confirmation_pair()` | 检查 parent-child、state、Preview/invariant/identity/executable 后把两者设 CONSUMED、transaction 设 DISPATCHING。 |
| `close()` | 释放 SQLite pool 并使 repository 失效。 |
| `_save_confirmation(...)` | 两级 gate 共用的内部事务写入。 |
| `_transaction(session, id)` | 精确读取 row；未知 ID fail closed。 |
| `_require_initialized()` | 防止数据库初始化失败后继续高风险操作。 |
| `WingetUninstallExecutionGuard.__init__(repository)` | 注入 durable store。 |
| `WingetUninstallExecutionGuard.require(authorization, tool_name, arguments)` | 同一数据库事务中核对 exact request digest、IDs、consumed runtime gate，并把 DISPATCHING 改为 EXECUTING。 |
| `_request_for_preview(preview)` | 从 Preview 内部构造唯一 typed request；没有自由 argv。 |
| `_any_active_uninstall(session, exclude)` | 查询 winget ORM 与 MSI/Vendor additive tables，执行全局互斥。 |
| `WingetUninstallTool.__init__(platform)` | 声明唯一 R2、batch1、双确认、irreversible、Rollback NONE manifest。 |
| `WingetUninstallTool.manifest` | 返回不可变 tool manifest。 |
| `WingetUninstallTool.execute(request, cancellation)` | 类型检查 request，调用平台一次并返回 process-only result。 |
| `WingetUninstallAuditLogger.__init__(repository, app_version, git_commit)` | 注入 append-only audit 与构建身份。 |
| `previewed(plan, preview)` | 记录 evidence digests、可执行决定与 Rollback NONE。 |
| `confirmation_resolved(plan, confirmation)` | 记录 tier、binding 和用户决定。 |
| `started(plan, preview, runtime_confirmation_id)` | mandatory pre-launch 事件；没有它就不启动。 |
| `completed(plan, report)` | 分开记录 process 和 dual verification，并声明无 shell/elevation/control/restart/deletion。 |
| `failed(plan, phase, error_code, mutation_may_have_started)` | 脱敏记录失败、是否可能已启动与禁止自动 retry。 |

### 编排、验证与 GUI

| 函数 / 方法 | 详细作用 |
|---|---|
| `WingetUninstallExecutionError` | 任一计划/身份/策略/运行时 gate 失败的安全错误。 |
| `PreparedWingetUninstall` | 包含 plan、Preview、第一确认或 package candidates。 |
| `PreparedWingetRuntimeConfirmation` | 包含 fresh Preview 与短时即时确认。 |
| `WingetUninstallService.__init__(...)` | 依赖注入全部独立边界；服务本身不构造任意命令。 |
| `prepare(user_goal, software_query, cancellation)` | 阻止 elevated Agent，fresh 解析 Software/Package、HIGH mapping、能力/class/preflight，创建 plan/Preview/transaction/audit/第一确认。 |
| `resolve_plan_confirmation(...)` | 保存并审计第一次用户决定。 |
| `prepare_runtime_confirmation(...)` | 再次读取 Package、Software、mapping、alias、policy、preflight/互斥；独立比较 invariant 后创建第二确认。 |
| `resolve_runtime_confirmation(...)` | 保存并审计即时决定。 |
| `execute(...)` | 消费 pair，先写 audit，构造 typed request，经 ToolRegistry/guard 单次 dispatch，随后 dual verify、exact-path residual、终态与 audit；不 retry。 |
| `_build_evidence(...)` | 统一构造 availability/capability/safety/preflight，不持有写平台。 |
| `_block(plan, code, message)` | best-effort 将失败 runtime revalidation 记为 BLOCKED。 |
| `_terminal_state(verification)` | 将双重验证映射为 verified/completed-unverified/interrupted/failed。 |
| `WingetUninstallVerifier.__init__(package_resolver, software_resolver)` | 注入两个彼此独立的 fresh inventory。 |
| `WingetUninstallVerifier.verify(...)` | 停止监控时不做早熟 success；否则分别刷新 Package 与 Software，只有双方完整且原 identity 都消失才 VERIFIED_REMOVED。 |
| `WingetWorkerSignals` | Qt worker 的 completed/failed 线程安全信号。 |
| `PreparedWingetUninstallWithServices` | 保持同一次 service graph 与 prepared state，避免换 repository。 |
| `WingetUninstallPrepareWorker.__init__/run/cancel` | UI 线程外准备证据；cancel 只取消尚未执行的只读工作。 |
| `WingetRuntimePrepareWorker.__init__/run/cancel` | UI 线程外重验并创建第二 gate。 |
| `WingetUninstallExecuteWorker.__init__/run/cancel` | UI 线程外执行；启动后 cancel 只停止监控。 |
| `require_prepared_winget_uninstall()` / `require_runtime_winget_confirmation()` / `require_winget_uninstall_report()` | Qt `object` signal 的运行时类型收窄；错误 payload fail closed。 |
| `WingetUninstallDialog.__init__()` | 创建 modeless 双确认窗口，取消按钮为默认。 |
| `_build_ui()` | 构建风险、详情、进度、确认/取消控件；普通通知不能替代确认。 |
| `_start_prepare()` / `_prepared()` | 启动后台 preparation 并接收 typed result。 |
| `_primary_clicked()` | 只按有限 UI 状态推进，不直接调用系统工具。 |
| `_approve_plan()` / `_runtime_prepared()` / `_approve_runtime()` | 依次完成第一次确认、fresh 重验、第二次确认和 worker dispatch。 |
| `_completed()` / `_show_preview()` / `_report_html()` | 展示 exact ID/版本/源/范围/风险/rollback，以及 process 与 dual verification 的不同。 |
| `_failed()` | 友好显示 fail-closed 原因并声明不重试。 |
| `_cancel_clicked()` / `closeEvent()` | 启动前取消；启动后只请求停止监控，从不强杀。 |
| `_set_busy()` / `_required_state()` | 管理忙碌状态并安全取得当前 service/plan/Preview/confirmation。 |

### 本阶段修改的既有 API

| 方法 | 变化 |
|---|---|
| `SoftwareUninstallRouter.route()` | 新增 `WINGET` 结果，但只在 fresh current-user、structured package manager/Package ID 与 capability 全匹配时返回；不产生执行授权。 |
| `MsiUninstallRepository.create()` / `VendorUninstallRepository.create()` | 现在同时检查 winget active table，三种卸载机制全局互斥。 |
| `VendorUninstallRepository.has_active_uninstall()` | 现在也报告 active winget transaction。 |
| `ApplicationRuntime.create_winget_uninstall_services()` | 组合专用 Package/Software resolver、alias/inventory/adapter、policy、confirmation、repository、registry、verifier、audit。 |
| `ApplicationRuntime.close()` | 关闭 winget repository。 |
| `SystemDiagnosticsTab.open_winget_uninstall()` / `_route_completed()` | 路由到新的非技术双确认窗口。 |

## Stage 4D2B 受控 Vendor Uninstaller API

本节逐一说明 Stage 4D2B 新增的生产函数、方法、协议和公开数据对象。最重要的不变量是：
`UninstallString` 只是不可信本地元数据，任何 API 都不能把它直接当命令执行；真正的写适配器
只接受经过所有确定性门验证的 `ValidatedVendorUninstallAction`。

### 领域模型 `domain.vendor_uninstall`

| 对象 / 函数 | 作用、输入输出和安全约束 |
|---|---|
| `VendorMetadataSourceKind` | 标识 Stage 4D2B 唯一可分析的来源种类 `INTERACTIVE_UNINSTALL_STRING`；Quiet 元数据没有可执行枚举值。 |
| `VendorParseConfidence` | 表示 Windows argv 解析置信度；当前执行链只生成/接受 `HIGH`，不把低置信度猜测升级成授权。 |
| `VendorArgumentDecision` | exact argv 的 `ALLOW`/`BLOCK` 结果；BLOCK 不能被确认界面覆盖。 |
| `VendorAuthenticodeStatus` | 离线 WinVerifyTrust 的 valid/invalid/unknown 事实；它只是信任证据之一。 |
| `VendorPublisherMatch` | installed-software Publisher 与签名组织名的保守 matched/mismatched/unknown 关系。 |
| `VendorInstallLocationRelation` | executable 位于 exact install location 内、外或未知；只有“内”可执行。 |
| `VendorTrustDecision` | executable trust 的 trusted/insufficient/blocked 决定；只有 `TRUSTED_FOR_EXECUTION` 能继续。 |
| `VendorExecutionDecision` | 软件分类与 executable trust 合并后的 ALLOW/BLOCK。 |
| `VendorPreflightState` | read-only process/service preflight 的 READY/BLOCKED/UNKNOWN；只有 READY 可执行。 |
| `VendorProcessResultCategory` | 描述直接进程退出 0/非 0、启动失败、vendor 请求提权、启动前取消、停止监控或长时脱离；不等于软件卸载结论。 |
| `VendorVerificationState` | fresh inventory 的最终观察：verified removed、unverified、异常进程结果但已移除、目标实例变化、failed 或 interrupted。 |
| `VendorUninstallTransactionState` | durable Preview→两级确认→dispatch→monitor→verify 状态；没有从 `INTERRUPTED` 返回执行的自动重试边。 |
| `ParsedVendorUninstallMetadata` | parse-only 输出：source digest、一个 executable token、exact argv tuple、来源、置信度和警告；参数在 repr 中隐藏。 |
| `ParsedVendorUninstallMetadata.argument_fingerprint()` | 对 argv 的值、顺序和边界生成 SHA-256 摘要，不输出参数正文。 |
| `VendorArgumentAssessment` | 保存 argv 的决定、fingerprint、数量与确定性理由。 |
| `VendorArgumentAssessment.canonical_digest()` | 将完整参数策略结果绑定到计划、Preview 和确认。 |
| `VendorExecutableFileIdentity` | 保存 absolute path、volume serial、File ID、大小、创建/修改时间、attributes 与 SHA-256，用于发现同路径替换。 |
| `VendorExecutableFileIdentity.canonical_digest()` | 哈希完整文件身份；path、metadata 或内容任一改变都会产生不同摘要。 |
| `VendorAuthenticodeEvidence` | 保存离线验证状态、证书 subject/organization 与安全错误码；不保存证书或 binary。 |
| `VendorAuthenticodeEvidence.canonical_digest()` | 对签名证据安全投影生成稳定摘要。 |
| `VendorExecutableObservation` | 汇总 file identity、本地 fixed volume、reparse、blocked location、install relation、signature、Publisher match 和观察时间。 |
| `VendorExecutableObservation.evidence_digest()` | 排除观察时间后哈希稳定信任证据，允许 runtime 刷新时间但不允许事实变化。 |
| `VendorExecutableTrustAssessment` | 保存 trust 决定、未通过理由和已建立证据。 |
| `VendorExecutableTrustAssessment.canonical_digest()` | 绑定 exact trust 结论，避免后续层只看一个布尔值。 |
| `VendorUninstallerIdentity` | 将 software/source/command digests、executable observation、exact argv、argument assessment 与 trust 组成不可变执行身份。 |
| `VendorUninstallerIdentity.validate_argument_binding()` | Pydantic 后置校验：fingerprint 和 argument count 必须与 exact tuple 一致，阻止策略评估后替换参数。 |
| `VendorUninstallerIdentity.canonical_digest()` | 包含当前观察时间的完整身份摘要，适合追踪某次构建。 |
| `VendorUninstallerIdentity.invariant_digest()` | 排除可刷新时间、保留全部执行事实的稳定摘要，用于 runtime 重现与确认绑定。 |
| `VendorExecutionAssessment` | 保存最终 execution decision、software class、R2/R2_HIGH/R3 风险、理由和证据。 |
| `VendorExecutionAssessment.validate_allowed_risk()` | ALLOW 只允许 R2/R2_HIGH_IMPACT；任何 R4 或 ALLOW+R3 矛盾对象构造失败。 |
| `VendorExecutionAssessment.canonical_digest()` | 绑定 class、decision、risk、reason 和 evidence。 |
| `VendorRelatedProcess` | 只读相关进程安全投影：PID、名称和 executable path digest；没有 terminate 能力。 |
| `VendorRelatedService` | 只读相关服务投影：ServiceName、显示名、状态和 binary path digest；没有 stop 能力。 |
| `VendorExecutionPreflightResult` | 汇总 process/service evidence、probe 完整性、active uninstall、blocker 与 warning。 |
| `VendorExecutionPreflightResult.canonical_digest()` | 将所有 preflight 事实和 unknown 绑定到确认。 |
| `VendorUninstallPlan` | 单目标、单工具、双确认、rollback NONE 的不可变计划；持有 evidence digests 而非 raw command。 |
| `VendorUninstallPlan.validate_execution_contract()` | 强制唯一 tool name、R2/R2_HIGH、两级确认和 NONE rollback。 |
| `VendorUninstallPlan.canonical_digest()` | 哈希全部授权相关字段，计划变化使确认失效。 |
| `VendorUninstallPreview` | 本地、会过期的对象级 Preview，包含 safe target、capability、Vendor identity、policy、preflight、executable flag 和人工恢复说明。 |
| `VendorUninstallPreview.bind_all_execution_evidence()` | 校验 expiry、software/executable binding、ALLOW/TRUSTED/READY/executable 一致性和 rollback NONE。 |
| `VendorUninstallPreview.invariant_digest()` | 哈希 runtime 必须精确重现的 plan/transaction/operation 与全部 evidence。 |
| `VendorUninstallPreview.canonical_digest()` | 哈希该次具体 Preview，包括 ID 和有效期，供 confirmation capability 使用。 |
| `ValidatedVendorUninstallAction` | 唯一允许传给 Windows 写适配器的强类型对象；包含 software digest、Vendor identity、transaction 和验证时间。 |
| `ValidatedVendorUninstallAction.require_execution_ready_identity()` | 构造时再次要求 trust=trusted、arguments=allow、software digest 一致，拒绝伪造 validated 名称。 |
| `ValidatedVendorUninstallAction.canonical_digest()` | 对完整 typed action 生成摘要。 |
| `VendorUninstallRequest` | ToolRegistry 的唯一输入：transaction/operation/plan/Preview ID 与 validated action；没有 raw command 字段。 |
| `VendorProcessExecutionResult` | 保存进程类别、exit code/PID、是否启动、取消/长时/监控状态、子进程数、时间和安全错误类别。 |
| `VendorUninstallResult` | 工具输出：关联 IDs、software/vendor digests 与 process evidence；不声称卸载成功。 |
| `VendorResidualReport` | exact known install location 的 `lstat` 结果；字段明确 `deletion_performed=false`。 |
| `VendorUninstallVerification` | fresh inventory 结果：原 identity 是否存在、replacement 数、刷新完整性、evidence 与 warning。 |
| `VendorUninstallExecutionReport` | GUI/audit 报告；并列 process、verification、residual、risk、rollback NONE 与 recovery guidance。 |

### Metadata parser 与 executable trust

| 函数 / 方法 | 作用、输入输出和失败语义 |
|---|---|
| `VendorMetadataError` | 表示 raw registry metadata 无法安全进入 Vendor pipeline；不携带 raw command。 |
| `VendorUninstallMetadataParser.__init__(argv_parser)` | 默认注入 Windows `CommandLineToArgvW` wrapper；测试可注入纯解析 fake，类本身没有执行依赖。 |
| `VendorUninstallMetadataParser.parse(raw)` | 只接受 HKCU/current-user registry/vendor source 和存在的 interactive UninstallString；校验长度/NUL/quote/argv 数量与 token 后返回结构化 parse。Quiet 值不进入输出。 |
| `windows_command_line_to_argv(command_line)` | 调用 Shell32 `CommandLineToArgvW` 并用 `LocalFree` 释放缓冲区；只分词、不启动 shell 或进程；非 Windows/Win32 失败抛 `OSError`。 |
| `_quotes_are_structurally_balanced(command_line)` | 在调用 Windows parser 前检测明显未闭合引号，同时考虑反斜杠转义奇偶；false 会 fail closed。 |
| `VendorExecutableTrustError` | 表示 path/type/trust 前置条件超出支持边界。 |
| `VendorExecutableTrustValidator.__init__(platform)` | 注入只读 executable observation 协议，使信任策略可用 fake 测试。 |
| `VendorExecutableTrustValidator.build_identity(software, raw, parsed, arguments)` | 解析 literal path，阻止 interpreter/non-EXE，要求 install location/Publisher，获取平台证据并累积 fixed/reparse/location/signature/publisher/argument gate；全部通过才返回 trusted identity。 |
| `resolve_vendor_executable(token)` | 只接受不含 NUL/变量/home/traversal、带 drive 的 absolute local Windows path；拒绝 relative、UNC/device，且永不查 PATH。 |
| `conservative_publisher_match(publisher, signer)` | 去除有限公司法律形式噪声后要求显著 token tuple 精确相等；缺失/全噪声为 UNKNOWN，不做模糊放行。 |
| `_publisher_words(value)` | 大小写折叠并提取 ASCII 字母数字 token，再移除有限法律后缀；仅供保守匹配。 |
| `VendorArgumentPolicy.assess(arguments)` | 对 exact tuple 做 finite allow-list：允许零参数或一个已知交互卸载 verb；quiet/restart/data/path/response/script/nested/shell-like/unknown/多参数全部 BLOCK，且不修改原参数。 |

### 软件策略、Preflight、Preview 与独立审查

| 函数 / 方法 | 作用、输入输出和安全约束 |
|---|---|
| `VendorUninstallExecutionPolicy.assess(scope, analysis, identity)` | 依次要求 current-user、trusted executable 和 Stage 4D1 非保护决定；普通应用返回 R2 ALLOW，developer tool/runtime 返回 R2_HIGH_IMPACT，其他全部 R3 BLOCK。 |
| `_blocked(safety_class, reason)` | 构造统一 R3/BLOCK assessment，并明确 Vendor capability 不代表软件可安全删除。 |
| `VendorExecutionPreflight.__init__(platform, max_items)` | 注入 Stage 3 read-only diagnostics 并设置有限枚举上限；不持有 process/service 写适配器。 |
| `VendorExecutionPreflight.inspect(software, identity, cancellation, active_uninstall_present)` | 要求 known install location；只关联 executable/binary path 位于该 root 内的对象。相关进程生成 warning、不自动 kill；running service、active uninstall、取消、warning/truncation/异常或 probe 不完整产生 blocker/unknown。 |
| `_canonical(path)` | 用 `abspath`/`normcase` 规范 path 文本，不跟随 reparse target。 |
| `_is_within(path, root)` | 用 `relative_to` 做组件级 containment，避免字符串前缀绕过；越界返回 false。 |
| `VendorUninstallPreviewEngine.__init__(ttl_seconds, now)` | 配置正数 Preview TTL 与可注入时钟；非正数立即拒绝。 |
| `VendorUninstallPreviewEngine.build(plan, target, capability, identity, assessment, preflight)` | 逐一比较 plan 中的 identity/capability/vendor/policy/preflight/risk 摘要；只在 trusted+arguments allow+policy allow+ready 时设置 executable，并生成 expiring Preview。 |
| `VendorUninstallSafetyReview` | 独立审查的 immutable `approved` 与非敏感 `issues`。 |
| `VendorUninstallSafetyValidator.__init__(registry)` | 注入 Stage 4D2B 专用 registry，使 manifest/plan 审查独立于 orchestrator。 |
| `VendorUninstallSafetyValidator.review(plan, preview)` | 要求 registry 精确只有 Vendor tool，manifest 为 irreversible R2/NONE/batch1/preview/runtime-confirmation；校验所有 ID/digest、trust、policy、preflight、risk 与 executable。任一 issue 都返回不批准。 |

### 两级确认 `confirmation.vendor_uninstall`

| 对象 / 函数 | 作用、输入输出和失败语义 |
|---|---|
| `VendorUninstallConfirmationTier` | 区分 PLAN 与 RUNTIME 两个不可替代的 gate。 |
| `VendorUninstallConfirmationState` | pending/approved/rejected/expired/consumed 状态；terminal/consumed 不能重放。 |
| `VendorUninstallConfirmation` | 绑定 confirmation parent、transaction/operation/plan/Preview、plan/preview/invariant/software/vendor/file/argument/capability/policy/preflight digests、risk、对象摘要、时间与状态。 |
| `VendorConfirmationStore.save_plan_confirmation(confirmation)` | Protocol：durable 保存第一 gate 并推进 transaction。 |
| `VendorConfirmationStore.save_runtime_confirmation(confirmation, preview)` | Protocol：保存 fresh Preview 绑定的短时 gate 和 exact tool reservation。 |
| `VendorConfirmationStore.get_confirmation(confirmation_id)` | Protocol：读取 durable capability；未知 ID 必须失败。 |
| `VendorConfirmationStore.resolve_confirmation(confirmation)` | Protocol：持久化 approve/reject/expire 和 transaction 对应状态。 |
| `VendorConfirmationStore.consume_confirmation_pair(plan, runtime)` | Protocol：原子验证/消费 parent-child pair，防 double-click/replay。 |
| `VendorUninstallConfirmationError` | 确认过期、状态错误、binding 变化或 replay 的 fail-closed 错误。 |
| `VendorUninstallConfirmationService.__init__(store, plan_ttl_seconds, runtime_ttl_seconds, now)` | 注入 durable store/clock 并校验两个正 TTL；runtime 默认更短。 |
| `VendorUninstallConfirmationService.request_plan(plan, preview)` | 要求 current executable Preview，创建 PLAN capability，绑定全证据并 durable 保存。 |
| `VendorUninstallConfirmationService.resolve_plan(id, approved, plan, preview)` | 读取 PLAN gate，核对类型、当前状态、有效期与全部 binding，再持久化用户决定。 |
| `VendorUninstallConfirmationService.request_runtime(parent_id, plan, preview)` | 要求 parent 已批准、fresh Preview current/executable，创建短时 RUNTIME child capability。 |
| `VendorUninstallConfirmationService.resolve_runtime(id, approved, plan, preview)` | 核对 runtime type/parent/current evidence/expiry 后持久化即时决定。 |
| `VendorUninstallConfirmationService.consume_runtime(id, plan, preview)` | 要求 runtime 已批准且未过期，重新验证全部 binding，读取已批准 parent，并通过 store 原子消费 pair；成功后返回 authorization facts。 |
| `VendorUninstallConfirmationService._resolve(...)` | PLAN/RUNTIME 共用状态解析：只允许 pending、检查 tier/expiry/current binding，并创建 approved/rejected copy。 |
| `VendorUninstallConfirmationService._create(...)` | 从 plan/Preview 生成 privacy-minimized exact confirmation；不复制 raw command、full args 或 path。 |
| `VendorUninstallConfirmationService._require_not_expired(confirmation)` | 将 naive SQLite 时间恢复为 UTC 语义并拒绝过期能力。 |
| `VendorUninstallConfirmationService._require_executable(plan, preview)` | 要求 Preview executable、风险合法、rollback NONE 和 safety binding 仍一致。 |
| `VendorUninstallConfirmationService._require_current(confirmation, plan, preview)` | 逐项比较 IDs、digests、risk、对象摘要和有效 Preview；任何变更都使旧确认无效。 |

### 持久化、原子授权与跨机制互斥

| 对象 / 函数 | 作用、输入输出和失败语义 |
|---|---|
| `VendorUninstallStoreError` | SQLite state 无法被信任、transition/binding/uniqueness 失败时的错误；写操作随后停止。 |
| `VendorUninstallBase` | Stage 4D2B 独立 SQLAlchemy metadata base，避免隐式耦合旧表定义。 |
| `VendorUninstallTransactionRow` | durable transaction/reservation row；只保存 IDs、digests、risk、state、最小 process/verification facts 和安全错误。 |
| `VendorUninstallConfirmationRow` | durable confirmation row；payload 是脱敏模型，不含 raw command/path/argv。 |
| `VendorUninstallRepository.__init__(database_path)` | 建立 SQLite engine/session factory，但在 `initialize()` 成功前保持不可用。 |
| `VendorUninstallRepository.initialize()` | 创建 additive tables；terminal 保持不变，dispatch/executing/waiting/monitor/process-exited/verifying 标 `INTERRUPTED`，其他 pending work 标 `CANCELLED`，pending/approved confirmations 过期；返回 interrupted IDs，绝不 redispatch。 |
| `VendorUninstallRepository.create(plan, preview)` | 在确认前 reserve transaction；先同时检查 Vendor 与 MSI active rows，只允许全局一个 uninstall，并保存 exact request digest。 |
| `VendorUninstallRepository.has_active_uninstall(exclude_vendor_transaction)` | 查询任一非 terminal Vendor 或 MSI transaction；runtime revalidation 可排除自己的 Vendor ID。 |
| `VendorUninstallRepository.transition(transaction_id, state, ...)` | 按固定 state graph 原子推进，保存脱敏 error/process/verification；非法跳转失败。 |
| `VendorUninstallRepository.state(transaction_id)` | 返回当前 durable Vendor state；未知 transaction 失败。 |
| `VendorUninstallRepository.save_plan_confirmation(confirmation)` | 实现 confirmation store：要求 PREVIEWED，保存 gate 并推进 AWAITING_PLAN_CONFIRMATION。 |
| `VendorUninstallRepository.save_runtime_confirmation(confirmation, preview)` | 刷新 reservation digests，保存 runtime gate 并推进 AWAITING_RUNTIME_CONFIRMATION。 |
| `VendorUninstallRepository.get_confirmation(confirmation_id)` | 从 row state 覆盖 payload state 后 Pydantic 验证，防陈旧 JSON 状态。 |
| `VendorUninstallRepository.resolve_confirmation(confirmation)` | 只解析 pending/approved gate；PLAN 决定推进 PLAN_CONFIRMED/CANCELLED，runtime reject/expire 取消。 |
| `VendorUninstallRepository.consume_confirmation_pair(plan, runtime)` | 同一 transaction 内要求两个 approved、正确 parent、state/Preview/invariant/vendor/argument/file binding，随后把两者标 CONSUMED 并推进 DISPATCHING。 |
| `VendorUninstallRepository.close()` | 释放 SQLite engine 并把 repository 标为未初始化；之后调用会 fail closed。 |
| `VendorUninstallRepository._save_confirmation(...)` | PLAN/RUNTIME 共用 durable 保存，检查 expected state；runtime 时重写所有 fresh reservation digests。 |
| `VendorUninstallRepository._transaction(session, transaction_id)` | 精确按 UUID 取 row；不存在抛 store error。 |
| `VendorUninstallRepository._require_initialized()` | 阻止在 table/init 不可信时进行任何 workflow 操作。 |
| `VendorUninstallExecutionGuard.__init__(repository)` | 注入唯一 durable authorization source。 |
| `VendorUninstallExecutionGuard.require(authorization, tool_name, arguments)` | 在 adapter 前核对 state、operation/plan/Preview/tool、reserved/actual args digest、runtime confirmation ID 与 consumed 状态；成功时同一 session 将 DISPATCHING 改 EXECUTING。 |
| `_request_for_preview(preview)` | 用 Preview evidence 和相同 `validated_at` 构建唯一 typed request，用于 reservation digest；没有 raw command。 |
| `_active_vendor_transaction(session, exclude_transaction)` | 判断 Vendor row 是否非 terminal，可排除当前 transaction。 |
| `_active_msi_transaction(session)` | additive 地检查 MSI table 是否存在并判断非 terminal state，实现 Vendor→MSI 互斥。 |
| `persistence.software_uninstall_execution._active_vendor_transaction(session)` | 反向供 MSI repository 检查 Vendor table，建立 MSI→Vendor 互斥；table 不存在时安全返回 false。 |

### 平台协议、Authenticode 与 Windows 适配器

| 函数 / 方法 | 作用、输入输出和安全约束 |
|---|---|
| `VendorExecutablePlatform.inspect(executable, install_location, publisher)` | 只读协议：返回完整 executable observation，不提供 execute。 |
| `VendorUninstallPlatform.uninstall(action, cancellation)` | 窄写协议：输入只能是 `ValidatedVendorUninstallAction`，返回 process facts；无 raw string/argv setter。 |
| `WindowsAuthenticodeVerifier.verify(path)` | 对 exact file 调用离线 `WinVerifyTrust` cache-only policy，再读取 embedded PKCS#7 signer subject/organization；错误转 invalid/unknown evidence 而非放行。 |
| `_verify_trust(path)` | 构造 `WINTRUST_FILE_INFO/DATA`，使用 `WTD_CACHE_ONLY_URL_RETRIEVAL`、无 UI、generic verify action，完成后调用 state close；返回 Win32 trust status。 |
| `_read_signer(path)` | 用 `CryptQueryObject`/`CryptMsgGetParam` 读取 embedded signed-message signer info，在 certificate store 按 issuer/serial 查找证书，并返回安全名称；所有 Win32 handles 在 finally 释放。 |
| `_certificate_name(cert_get_name, certificate, name_type)` | 两次调用 `CertGetNameStringW` 获取 subject display/simple name；失败或空值返回 None。 |
| `_Guid` / `_WinTrustFileInfo` / `_WinTrustData` / `_Blob` / `_AlgorithmIdentifier` / `_CryptAttributes` / `_SignerInfo` / `_CertInfo` | ctypes layout，仅封装调用所需 Windows ABI 字段；不承载业务授权。 |
| `_PolledProcess.poll()` | adapter 的最小进程协议，只暴露 PID 和 poll；故意没有 terminate/kill。 |
| `WindowsVendorExecutablePlatform.__init__(authenticode, file_platform, environ, max_hash_bytes)` | 注入 signature/File ID adapters 和环境证据；限制正数 hash 大小。非 Windows 且无 fake 时拒绝。 |
| `WindowsVendorExecutablePlatform.inspect(executable, install_location, publisher)` | strict resolve 两个已存在路径，要求 regular `.exe`；检查全组件 reparse、本地 fixed volume、blocked location、containment，读取 before File ID、bounded SHA-256、signature、after File ID，before/after 不同即拒绝。 |
| `WindowsVendorUninstallPlatform.__init__(popen_factory, environ, poll_interval_seconds, long_running_seconds, monotonic, sleeper)` | 注入可测试 Popen/clock；校验正数间隔，并在构造时建立最小 child environment。 |
| `WindowsVendorUninstallPlatform.uninstall(action, cancellation)` | 启动前取消则不 spawn；再次检查 absolute local `.exe` 与 file metadata/hash；调用 `[absolute_exe, *exact_args]`、explicit executable/cwd、sanitized env、DEVNULL、close_fds、`shell=False`。轮询 direct/children；取消或 long-running 只停止监控，不 kill；WinError 740 记录 vendor-requested-elevation、不 runas。 |
| `sanitized_vendor_environment(environment)` | 只保留有限 Windows runtime/profile/temp/program dirs，丢弃 PATH、所有非 allow-list 和 token/secret/password/cookie/API-key/credential 名；NUL key/value 也丢弃。 |
| `_matches_identity(path, expected)` | adapter 边界重新比较 size/ctime/mtime 与 SHA-256；文件消失/读取失败返回 false。 |
| `_sha256(path)` | 以 1 MiB block 流式计算 exact executable SHA-256，不把 binary 读入日志或模型。 |
| `_child_processes(pid)` | 使用 psutil 只读收集 recursive child PID；异常返回空集合，从不发信号。 |
| `_is_local_fixed_volume(path)` | 调用 `GetDriveTypeW`，只有 `DRIVE_FIXED` 为 true；非 Windows/无 anchor false。 |
| `_reparse_free(path)` | 从 drive root 到文件逐组件调用 `GetFileAttributesW`；unknown 或任一 reparse bit 均 false。 |
| `_is_blocked_location(path, environment)` | 构造 TEMP/TMP/Downloads/INetCache roots，并用组件级比较阻止常见高风险可写位置。 |
| `_is_within(path, root)` | normcase 后用 `relative_to` 判断 Windows containment，防 `C:\App2` 冒充 `C:\App`。 |

### 编排、监控、验证与残留

| 对象 / 函数 | 作用、输入输出和失败语义 |
|---|---|
| `VendorUninstallExecutionErrorCode` | capability/policy/target/safety/elevation/execution 等确定性错误码；便于 UI 和 audit 不泄漏 raw 输入。 |
| `VendorUninstallExecutionError.__init__(code, message)` | 保存枚举 code 和友好安全 message；不包装 raw command。 |
| `PreparedVendorUninstall` | prepare 输出：resolution、可选 plan/Preview/review/plan confirmation；歧义/阻止时不会伪造后续对象。 |
| `PreparedVendorRuntimeConfirmation` | fresh runtime Preview 与短时 confirmation 的组合。 |
| `VendorUninstallService.__init__(...)` | 显式注入 resolver、capability/parser/argument/trust/two policies/preflight/Preview/reviewer/confirmation/repository/registry/verifier/residual/audit/elevation probe；避免全局写能力。 |
| `VendorUninstallService.prepare(user_goal, query, cancellation)` | 拒绝 elevated Agent，fresh resolve exact target/raw，构建全部 evidence、plan/Preview/review/audit；只在批准后 reserve transaction 并 durable 请求 PLAN gate。确认存储失败会标 BLOCKED。 |
| `VendorUninstallService.resolve_plan_confirmation(id, approved, plan, preview)` | 委托 confirmation service 核对/持久化第一决定，并写脱敏 audit。 |
| `VendorUninstallService.prepare_runtime_confirmation(parent_id, plan, cancellation)` | 先推进 VALIDATING，再重读 software/raw/capability/executable/hash/signature/args/policy/preflight，重建 Preview/review；任何不一致标 BLOCKED 并拒绝，否则签发 runtime gate。 |
| `VendorUninstallService.resolve_runtime_confirmation(id, approved, plan, preview)` | 核对并持久化即时决定，同时审计。 |
| `VendorUninstallService.execute(runtime_id, plan, preview, cancellation)` | 原子消费 pair；mandatory pre-start audit；构造 typed request/authorization 并经 registry 调用一次。普通退出后推进 PROCESS_EXITED→VERIFYING→terminal；adapter/guard 失败标 FAILED 且不重试。 |
| `VendorUninstallService._build_evidence(...)` | 清除 Quiet 值后要求 Stage 4D1 Vendor metadata support；parse exact interactive raw、评估 args、构建 trust identity、执行 class policy 和 read-only preflight。 |
| `VendorUninstallService._fresh_target(identity_digest, cancellation)` | 调用 resolver.inspect，要求 inventory complete、exact target/raw 存在且 interactive metadata 仍在；partial/disappear/missing raw 全部失败。 |
| `VendorUninstallService._monitoring_stopped_report(plan, preview, result)` | 对 STOPPED/MONITORING_DETACHED 持久化 `MONITORING` process facts，生成 `INTERRUPTED`/未验证报告并延迟 residual；明确不 kill、不早验。 |
| `VendorUninstallService._report(...)` | 将 IDs、safe target summary、risk、process、verification、residual 与 recovery guidance 合成用户报告。 |
| `_terminal_state(verification_state)` | VERIFIED_REMOVED→terminal verified；unverified/unexpected/replacement→COMPLETED_UNVERIFIED；其余→FAILED，不提供 retry edge。 |
| `VendorUninstallVerifier.__init__(resolver)` | 注入 fresh normalized inventory resolver。 |
| `VendorUninstallVerifier.verify(target, process, max_items, cancellation)` | 刷新 exact identity，记录 inventory complete/partial/failure 与 same-name replacement；结合而不覆盖 process fact，返回 verified/failed/replacement/unverified。 |
| `_replacement_count(snapshot, target)` | 统计同规范 name/publisher/scope 但不同 source-qualified identity 的候选；不自动选或声明升级成功。 |
| `_same(left, right)` | 非空文本做 whitespace normalization 与 casefold 精确比较；缺失返回 false。 |
| `VendorResidualAnalyzer.analyze(install_location)` | 对 original known location 仅调用 `os.lstat`，报告 missing/existing/link/error；不 enumerate/follow/delete。 |

### 隐私最小化 Audit

| 函数 / 方法 | 作用、输入输出和安全约束 |
|---|---|
| `VendorUninstallAuditLogger.__init__(repository, app_version, git_commit)` | 注入结构化 audit store 和版本元数据；git commit 可未知。 |
| `VendorUninstallAuditLogger.previewed(plan, preview)` | 记录 request/plan/transaction、software/capability/vendor/file/argument/policy/preflight digests、risk、counts 与签名类别；不记录 executable path/raw argv。 |
| `VendorUninstallAuditLogger.confirmation_resolved(plan, confirmation)` | 记录 confirmation tier/state/expiry 和关联 IDs/digests，不记录对象正文。 |
| `VendorUninstallAuditLogger.started(plan, preview, runtime_confirmation_id)` | mandatory write-ahead event，记录 exact reservation digests 和 `EXECUTING` 意图；失败会阻止 platform launch。 |
| `VendorUninstallAuditLogger.completed(plan, report)` | 记录 process category/exit/PID/children、fresh verification、residual flags、terminal interpretation、rollback NONE 与耗时。 |
| `VendorUninstallAuditLogger.failed(plan, phase, error_code, mutation_may_have_started)` | 记录失败阶段与是否可能已启动；错误 payload 不含 exception raw command/path。 |

### 只读 MSI/Vendor 路由

| 对象 / 函数 | 作用、输入输出和失败语义 |
|---|---|
| `SoftwareUninstallMechanism` | `MSI`、`VENDOR`、`AMBIGUOUS`、`UNSUPPORTED` 四种 GUI 路由结果。 |
| `SoftwareUninstallRoute` | 保存 read-only resolution、mechanism、capability 与用户可读 reason；本身无 authorization。 |
| `SoftwareUninstallRouter.__init__(resolver, capability, max_items)` | 注入 fresh target resolver 与 Stage 4D1 capability resolver。 |
| `SoftwareUninstallRouter.route(query, cancellation)` | fresh resolve；非精确/歧义保持 AMBIGUOUS/UNSUPPORTED；exact target 按 current capability 只选 MSI 或 Vendor，其他机制不 fallback。 |

### Tool、Runtime 与配置

| 对象 / 函数 | 作用、输入输出和安全约束 |
|---|---|
| `VendorUninstallTool.__init__(platform)` | 创建固定 manifest：`software.uninstall.vendor`、R2、write、non-idempotent、runtime confirmation、Preview、batch 1、rollback NONE、irreversible。 |
| `VendorUninstallTool.manifest` | 返回固定 manifest；调用方不能注册通用 executable/argument 字段。 |
| `VendorUninstallTool.execute(request, cancellation)` | 只接受 `VendorUninstallRequest`，否则 TypeError；把 validated action 交给窄 platform，并返回带 identity digests 的 result。 |
| `VendorUninstallServices` | Runtime dependency bundle：专用 ToolRegistry、fresh resolver 与 Vendor service。 |
| `ApplicationRuntime.create_software_uninstall_router()` | 构建 fresh Windows inventory/resolver/capability 的 read-only router；无 write guard 或 adapter。 |
| `ApplicationRuntime.create_vendor_uninstall_services()` | 组装专用 registry/guard/tool、Windows trust/execute adapters、policies、preflight、Preview、confirmations、repository、verifier、residual 与 audit；所有配置通过依赖注入。 |
| `ApplicationRuntime.close()`（Stage 4D2B 增量） | 在其他 store 之前关闭 Vendor repository，阻止 shutdown 后继续写。 |
| `AppSettings.vendor_runtime_confirmation_ttl_seconds` | `PC_MANAGER_VENDOR_RUNTIME_CONFIRMATION_TTL_SECONDS`，15–300 秒，默认 60。 |
| `AppSettings.vendor_monitor_poll_seconds` | `PC_MANAGER_VENDOR_MONITOR_POLL_SECONDS`，0.05–5 秒，默认 0.25。 |
| `AppSettings.vendor_long_running_seconds` | `PC_MANAGER_VENDOR_LONG_RUNNING_SECONDS`，30–7200 秒，默认 900；达到后只停止同步监控，不 kill。 |

### PySide6 worker 与 GUI

| 函数 / 方法 | 作用、输入输出和安全约束 |
|---|---|
| `SoftwareUninstallRouteSignals` | route worker 的 completed/failed Qt signals；只传 safe route 或友好错误。 |
| `SoftwareUninstallRouteWorker.__init__(runtime, query)` | 保存 runtime/query 和 cancellation token，不在 UI thread 做 inventory。 |
| `SoftwareUninstallRouteWorker.run()` | 后台创建 read-only router 并 emit route；异常只发类型化安全消息。 |
| `SoftwareUninstallRouteWorker.cancel()` | 设置 cancellation token；不取消或控制任何 uninstaller，因为 route 阶段尚未执行。 |
| `require_software_uninstall_route(value)` | UI 边界 runtime type check；非 `SoftwareUninstallRoute` 抛 TypeError。 |
| `VendorUninstallWorkerSignals` | Vendor prepare/runtime/execute worker 的 completed/failed signal 集合。 |
| `PreparedVendorUninstallWithServices` | 把 prepare 结果与其专用 services 绑定，确保后续步骤复用同一 graph/repository。 |
| `VendorUninstallPrepareWorker.__init__(runtime, user_goal, query)` | 创建 prepare worker 所需参数和 cancellation token。 |
| `VendorUninstallPrepareWorker.run()` | 后台调用 `create_vendor_uninstall_services().service.prepare()`；不直接调用 tool。 |
| `VendorUninstallPrepareWorker.cancel()` | 只请求取消当前 read-only prepare。 |
| `VendorRuntimePrepareWorker.__init__(services, plan_confirmation_id, plan)` | 保存已批准 parent 与 immutable plan，准备 fresh runtime revalidation。 |
| `VendorRuntimePrepareWorker.run()` | 后台调用 `prepare_runtime_confirmation`，只产生 fresh Preview/gate；尚不执行。 |
| `VendorRuntimePrepareWorker.cancel()` | 请求取消 runtime validation；不会把取消变成卸载授权。 |
| `VendorUninstallExecuteWorker.__init__(services, runtime_confirmation_id, plan, preview)` | 保存 exact runtime capability 与其绑定对象。 |
| `VendorUninstallExecuteWorker.run()` | 后台 resolve runtime approval 并调用 orchestrator execute；UI 仍不碰 registry/platform。 |
| `VendorUninstallExecuteWorker.cancel()` | 请求停止观察；adapter 启动后不 terminate/kill，语义由 structured result 说明。 |
| `require_prepared_vendor_uninstall(value)` | 强制 worker result 为 `PreparedVendorUninstallWithServices`。 |
| `require_runtime_vendor_confirmation(value)` | 强制 runtime worker result 类型，避免 QObject payload 混淆。 |
| `require_vendor_uninstall_report(value)` | 强制 execute worker result 为 final report。 |
| `VendorUninstallDialog.__init__(runtime, user_goal, query, parent)` | 创建 modeless 两级确认对话框；默认动作是取消，并立即从后台 prepare 开始。 |
| `VendorUninstallDialog._build_ui()` | 建立 safe summary、候选列表、状态、明确取消/确认按钮；不显示 raw command/full path+args。 |
| `VendorUninstallDialog._start_prepare(query)` | 启动 prepare worker、追踪 worker 并禁用重复提交。 |
| `VendorUninstallDialog._prepared(value)` | type-check 结果；处理 ambiguity、blocked review 或显示 PLAN Preview。 |
| `VendorUninstallDialog._show_candidates(candidates)` | 显示安全软件候选投影，要求用户选择；不自动选相似名称。 |
| `VendorUninstallDialog._primary_clicked()` | 按当前明确 UI 阶段分派 select/plan approve/runtime approve；未知状态不执行。 |
| `VendorUninstallDialog._select_candidate()` | 用所选 exact identity digest 重新 prepare，旧候选/Preview 不授权。 |
| `VendorUninstallDialog._approve_plan()` | 只解析第一确认并启动 fresh runtime prepare；不会执行 tool。 |
| `VendorUninstallDialog._runtime_prepared(value)` | 显示第二次即时确认及 fresh Preview。 |
| `VendorUninstallDialog._approve_runtime_and_execute()` | 明确 approve runtime 后创建 execute worker；每个 confirmation 只使用一次。 |
| `VendorUninstallDialog._completed(value)` | 显示 process/verification/residual 分离报告；不把 exit 0 文案写成成功。 |
| `VendorUninstallDialog._show_preview(preview, immediate)` | 生成 plan 或 runtime 的安全 HTML，显示 trust/risk/preflight/rollback/不自动行为。 |
| `VendorUninstallDialog._cancel_clicked()` | 未 dispatch 时 reject 当前 confirmation；worker active 时请求取消；dispatch 后只停止监控。 |
| `VendorUninstallDialog._reject_plan_and_close()` | durable 拒绝 PLAN gate 后关闭。 |
| `VendorUninstallDialog._reject_runtime_and_close()` | durable 拒绝 runtime gate 后关闭。 |
| `VendorUninstallDialog._failed(message)` | 恢复 UI 可操作状态并显示脱敏错误；不猜测执行结果。 |
| `VendorUninstallDialog._set_busy(text)` | 统一更新忙碌状态和按钮禁用，防 double-click。 |
| `VendorUninstallDialog.shutdown()` | 请求所有 active workers 取消/停止监控并断开 UI 所有权；不杀子进程。 |
| `VendorUninstallDialog.closeEvent(event)` | 关闭时调用 `shutdown()`，避免遗留未管理 Qt worker。 |
| `_preview_html(preview, immediate)` | 把 safe target、trust categories、risk、process/service counts、manual UI 与 rollback NONE 转义成 HTML；不渲染 raw UninstallString。 |
| `_report_html(report)` | 把 process fact、fresh verification、residual、recovery guidance 转义成最终 HTML。 |
| `SystemDiagnosticsTab._open_selected_vendor_uninstall()` | 从表格要求恰好一个目标并进入 Vendor dialog；表格选择本身不授权。 |
| `SystemDiagnosticsTab.open_vendor_uninstall(user_goal, query)` | 创建/追踪 modeless Vendor dialog，并明确状态仍在身份验证、尚未授权。 |
| `SystemDiagnosticsTab.open_routed_uninstall(user_goal, query)` | 启动只读 mechanism worker，不在 UI thread 刷 inventory。 |
| `SystemDiagnosticsTab._route_completed(worker, user_goal, value)` | type-check route；exact MSI/Vendor 分别进入独立 dialog，ambiguous/unsupported fail closed。 |
| `SystemDiagnosticsTab._route_failed(worker, message)` | 移除 finished worker 并显示安全错误。 |
| `SystemDiagnosticsTab.shutdown()`（Stage 4D2B 增量） | 关闭所有 Vendor dialogs 并 cancel route workers，避免窗口退出后 UI 继续调度。 |
| `SystemDiagnosticsTab._software_selection_changed()`（Stage 4D2B 增量） | 仅根据是否 exact 单选启用 Vendor 按钮；不会预先确认。 |
| `MainWindow` Stage 4D2B chat route | “卸载软件 X”先创建 `SoftwareTargetQuery`，交给只读 router；模型/聊天文本不能选择 executable 或 argv。 |

### 测试替身 API（仅测试代码）

| 函数 / 方法 | 作用 |
|---|---|
| `tests.fixtures.vendor_uninstall.vendor_entry(...)` | 构建合成 HKCU/current-user Vendor registry record；绝不使用本机真实 UninstallString。 |
| `FakeVendorExecutablePlatform.inspect(...)` | 从临时 fixture 文件生成完整可信 observation，用于 deterministic trust 测试。 |
| `FakeVendorUninstallPlatform.uninstall(...)` | 只记录 typed action，并可改变 fake inventory；不启动真实进程。 |
| `SyntheticVendorEnvironment.close()` | 释放测试 repository/audit SQLite engines。 |
| `build_vendor_environment(...)` | 组装与生产相同边界、但完全由 synthetic inventory/executable/fake adapter 驱动的测试 graph。 |
| `_synthetic_argv(command, executable)` | 仅解析受控 fixture tail；生产代码仍使用 Windows parser。 |

## Stage 4D2A 受控 MSI 卸载 API

本节逐一说明 Stage 4D2A 新增的生产函数、方法和公开数据对象。所有 ProductCode 示例都必须是
合成值；API 不接受原始 UninstallString、任意 executable 或任意 installer arguments。

### 领域模型 `domain.software_uninstall_execution`

| 对象 / 函数 | 作用、输入输出和安全约束 |
|---|---|
| `MsiInstallContext` | 表示 Windows Installer 注册上下文：user-managed、user-unmanaged、machine、unknown。执行策略只接受 current-user 的 user-unmanaged。 |
| `MsiExecutionDecision` | Stage 4D2A 最终确定性决定，只含 `ALLOW`/`BLOCK`；用户确认不能把 BLOCK 改成 ALLOW。 |
| `MsiPreflightState` | preflight 的 READY/BLOCKED/UNKNOWN；只有 READY 能生成 executable Preview。 |
| `MsiInstallerResultCategory` | 将原始 exit code 分类为成功、需重启、用户取消、安装器忙、产品不存在、权限/策略拒绝、失败、意外重启、监控脱离、启动失败或未知；它不是最终卸载结论。 |
| `MsiVerificationState` | fresh inventory 的最终观察：verified removed、unverified、异常返回但已移除、被升级/替换、failed、interrupted。 |
| `MsiUninstallTransactionState` | 描述 Preview、两级确认、验证、dispatch、执行、等待和终态。`WAITING`/`INTERRUPTED` 不会自动恢复或重试。 |
| `MsiProductRegistration` | msi.dll 返回的一个 ProductCode/context 安装实例以及安全的 name/version/publisher/location/state 投影；不含卸载命令。 |
| `MsiProductRegistration.canonical_digest()` | 对注册上下文和安全属性生成稳定 SHA-256 摘要，供身份和确认绑定。 |
| `ValidatedMsiProduct` | 唯一可进入适配器的强类型对象；绑定严格 ProductCode 及摘要、Stage 4D1 identity/metadata/capability、MSI registration、scope、architecture、source anchor 和验证时间。 |
| `ValidatedMsiProduct.require_matching_product_code_digest()` | Pydantic 后置校验：重新计算 ProductCode 摘要，拒绝构造后替换 ProductCode。 |
| `ValidatedMsiProduct.canonical_digest()` | 包含验证时间的完整对象摘要，用于追踪某次观察。 |
| `ValidatedMsiProduct.evidence_digest()` | 排除 `validated_at` 的稳定执行证据摘要；允许运行时重新观察时间变化，但不允许身份事实变化。 |
| `MsiExecutionAssessment` | execution-only policy 结果，包含 class、ALLOW/BLOCK、R2/R2_HIGH/R3、理由和证据。 |
| `MsiExecutionAssessment.validate_risk_decision()` | 只允许 ALLOW 与 R2/R2_HIGH 配对，拒绝 R4 和矛盾 risk/decision。 |
| `MsiExecutionAssessment.canonical_digest()` | 绑定执行分类、风险、理由和证据。 |
| `RelatedProcessEvidence` | 只保存强路径相关进程的 PID、名称和 executable path digest；不保存命令行或控制权限。 |
| `RelatedServiceEvidence` | 只保存强路径相关服务的名称、显示名、状态和 binary path digest；不提供 stop 权限。 |
| `SoftwareExecutionPreflightResult` | 保存进程/服务证据完整性、相关对象、installer busy/reboot pending 的已知或 unknown、预期权限、blocker/warning。 |
| `SoftwareExecutionPreflightResult.canonical_digest()` | 将全部 preflight 观察绑定到计划和确认。 |
| `MsiUninstallPlan` | 单对象、单工具、双确认、rollback NONE 的不可变计划；持有所有证据摘要而不是 raw metadata。 |
| `MsiUninstallPlan.validate_execution_contract()` | 强制唯一工具名、R2/R2_HIGH、两级确认和 NONE rollback，防止模型或 UI 扩大执行面。 |
| `MsiUninstallPlan.canonical_digest()` | 对全部授权相关字段生成计划摘要。 |
| `MsiUninstallPreview` | 本地对象级、会过期的执行 Preview；包含安全投影、强 MSI 身份、风险、preflight、恢复说明和 executable 标记。 |
| `MsiUninstallPreview.bind_all_execution_evidence()` | 校验 target/product/identity、ALLOW/READY/executable 和 rollback NONE 的内部一致性。 |
| `MsiUninstallPreview.invariant_digest()` | 对运行时必须重现的稳定证据生成摘要；不绑定新 Preview ID/生成时间。 |
| `MsiUninstallPreview.canonical_digest()` | 对该次具体 Preview 的所有字段生成摘要。 |
| `MsiUninstallRequest` | 内部工具请求，只含 transaction/operation/plan/Preview ID 和 `ValidatedMsiProduct`；故意没有 command/args 字段。 |
| `MsiInstallerExecutionResult` | 固定 MSI client 的启动、exit/category、取消时机、长时运行、开始/结束/耗时和安全错误类型证据。 |
| `MsiUninstallResult` | 工具输出，关联 transaction/operation/identity/ProductCode digest 和 installer result；不声称最终成功。 |
| `MsiResidualReport` | 对原已知 install location 的单路径 `lstat` 结果，显式记录是否检查、是否存在、是否链接/重解析点及 `deletion_performed=false`。 |
| `MsiUninstallVerification` | fresh registry/MSI 双清单后的最终状态、原 identity/ProductCode 是否仍在、替代候选数、刷新完整性、证据和警告。 |
| `MsiUninstallExecutionReport` | GUI/审计最终报告，保留 installer 和 verification 两种事实、残留报告、风险、rollback NONE 与恢复指导。 |

### MSI 身份 `orchestration.software_msi_validation`

| 函数 / 方法 | 作用、输入输出和失败语义 |
|---|---|
| `MsiProductValidationError.__init__(code, message)` | 创建带确定性枚举码的安全错误；message 不包含 raw uninstall command。 |
| `normalize_product_code(value)` | 只接受完整带花括号的 GUID，使用 `UUID` 解析并返回大写规范形式；缺括号、参数、shell 字符、Unicode 仿冒或垃圾全部抛 `MsiProductValidationError`。 |
| `MsiProductValidator.__init__(platform)` | 注入只读 Windows Installer registration 协议，避免业务层直接依赖 msi.dll。 |
| `MsiProductValidator.validate(software, capability)` | 要求 MSI/high、identity/capability ProductCode 相同、恰好一个 installed registration、user-unmanaged/current-user、name/version/publisher 一致；返回 `ValidatedMsiProduct`，否则按具体 code fail closed。 |
| `_same_optional(left, right)` | 规范空白并大小写无关比较可选 metadata；一边缺失时不猜测相等。 |

### 执行策略、Preview 与审查

| 函数 / 方法 | 作用、输入输出和安全约束 |
|---|---|
| `SoftwareUninstallExecutionPolicy.assess(product, analysis)` | 把 Stage 4D1 分类收窄为执行许可：user application 为 R2，developer tool/runtime 为 R2_HIGH，其他/保护/非 current-user 为 R3 BLOCK。 |
| `_blocked(safety_class, risk_level, reason)` | 构造统一的 BLOCK assessment，并明确“MSI capability 不等于安全”。 |
| `MsiUninstallPreviewEngine.__init__(ttl_seconds, now)` | 配置正数 Preview TTL 和可注入时钟，便于过期测试。 |
| `MsiUninstallPreviewEngine.build(plan, target, product, capability, assessment, preflight)` | 逐项比较计划证据，只在 ALLOW+READY 时生成 executable、到期的 `MsiUninstallPreview`。 |
| `MsiUninstallSafetyValidator.__init__(registry)` | 注入专用 registry 作为独立安全审查边界。 |
| `MsiUninstallSafetyValidator.review(plan, preview)` | 要求 registry 只有一个正确 manifest，核对全部 digest、风险、transaction、ALLOW、READY、executable；返回 `MsiUninstallSafetyReview`，任何问题都不批准。 |
| `MsiUninstallSafetyReview` | `approved` 与非敏感 `issues` 的不可变审查结果。 |

### Process / Service preflight `orchestration.software_execution_preflight`

| 函数 / 方法 | 作用、输入输出和安全约束 |
|---|---|
| `SoftwareExecutionPreflight.__init__(platform, max_items)` | 注入 Stage 3 只读诊断协议并限定枚举数量；没有进程或服务写适配器。 |
| `SoftwareExecutionPreflight.inspect(software, product, cancellation)` | 要求 identity 一致及已知 install location；只把 executable/binary path 位于该位置内的对象当强相关。相关进程、running service、取消、warning/truncation/异常都阻止；从不 kill/stop。 |
| `_canonical(path)` | 用 `abspath/normcase` 规范化路径文本，不 `resolve()` 链接/联接目标，避免扩大范围。 |
| `_is_within(path, root)` | 用 `relative_to` 判断强路径包含关系；越界返回 false。 |

### 平台协议和 Windows 实现

| 函数 / 方法 | 作用、输入输出和安全约束 |
|---|---|
| `MsiProductInventoryPlatform.registrations(product_code)` | 只读协议：为严格 ProductCode 返回所有 MSI context registration。 |
| `MsiUninstallPlatform.uninstall(product, cancellation)` | 狭窄执行协议：输入只能是 `ValidatedMsiProduct`，输出结构化 installer result。 |
| `_PolledProcess.poll()` | Windows adapter 的最小子进程监控协议；不暴露 terminate/kill。 |
| `WindowsMsiProductInventory.__init__(msi_dll)` | 生产时加载 msi.dll；测试可注入 fake。非 Windows 且无 fake 时拒绝构造。 |
| `WindowsMsiProductInventory.registrations(product_code)` | 调用 `MsiEnumProductsExW` 和 `MsiGetProductInfoExW`，枚举 current-user/machine context 并返回安全属性；不调用 `Win32_Product` 或 mutation API。 |
| `WindowsMsiProductInventory._get_info(product_code, sid, context, property_name)` | 两次缓冲区查询一个 allow-listed MSI property；unknown product 返回 None，其他 API 错误抛 `OSError`。 |
| `WindowsMsiProductInventory._configure_signatures()` | 为使用的两个 msi.dll Unicode 函数配置 ctypes arg/restype，避免隐式参数转换。 |
| `WindowsMsiUninstallPlatform.__init__(...)` | 注入系统目录、Popen 工厂、轮询/长时阈值、时钟和 sleeper；正数校验支持无真实进程测试。 |
| `WindowsMsiUninstallPlatform.uninstall(product, cancellation)` | 启动前取消则不 spawn；否则严格解析系统 `msiexec.exe`，以 `[exe, /x, ProductCode, /norestart]`、`shell=False`、固定 cwd 和 DEVNULL stdio启动。轮询 exit；达到阈值则不 kill、返回 MONITORING_DETACHED。 |
| `_context_from_raw(value)` | 把 MSI context flag 映射到领域枚举；未知值保守为 UNKNOWN。 |
| `_optional_path(value)` | 非空 MSI property 转为 Path，空值保持 None。 |
| `_get_system_directory()` | 调用 `GetSystemDirectoryW` 获取可信系统目录；失败抛 Windows error，不从 PATH 查找 executable。 |
| `current_process_is_elevated()` | 只读查询当前 token elevation；不请求权限。非 Windows 返回 false。Stage 4D2A 对 true fail closed。 |

### Exit code、验证与残留

| 函数 / 方法 | 作用、输入输出和安全约束 |
|---|---|
| `map_msi_exit_code(exit_code)` | 映射 0/5/1602/1605/1614/1618/1625/1641/3010 等 documented code；1641 单独标记意外已发起重启。 |
| `_fallback(exit_code)` | 1601–1654 未专门映射的 code 归为 installer failure，其他归为 unknown。 |
| `MsiUninstallVerifier.__init__(resolver, msi_inventory)` | 注入 fresh normalized registry resolver 和 Windows Installer API inventory。 |
| `MsiUninstallVerifier.verify(product, installer, max_items, cancellation)` | 独立刷新两种证据；仅在完整、无 warning/truncation 的 registry inventory 中原 identity 消失、ProductCode 也消失且 installer 合理成功时 VERIFIED_REMOVED。partial inventory、失败返回但消失、仍存在、替换升级或 probe 失败均保留独立状态。 |
| `_replacement_count(snapshot, product)` | 在 fresh inventory 中按规范 name/publisher 搜索不同 identity 的替代版本候选，不把它自动选为目标。 |
| `_same(left, right)` | 对必需文本做空白规范化/大小写无关比较。 |
| `SoftwareResidualAnalyzer.analyze(install_location)` | 只对已知 exact path 调用 `os.lstat`；报告缺失/存在/链接/错误，不枚举、不跟随、不删除。 |

### 双重确认 `confirmation.software_uninstall_execution`

| 函数 / 方法 | 作用、输入输出和失败语义 |
|---|---|
| `MsiUninstallConfirmationTier` | 区分 PLAN 与 RUNTIME 两个不可互换的 gate。 |
| `MsiUninstallConfirmationState` | pending/approved/rejected/expired/consumed；terminal 或 consumed 不能重放。 |
| `MsiUninstallConfirmation` | 绑定两级 parent、transaction/operation/plan/Preview、全部 evidence digest、风险、对象摘要、时间和状态。 |
| `MsiConfirmationStore.save_plan_confirmation(...)` | 持久化第一 gate 并原子推进 transaction。 |
| `MsiConfirmationStore.save_runtime_confirmation(...)` | 保存 fresh Preview/tool arguments 与第二 gate。 |
| `MsiConfirmationStore.get_confirmation(...)` | 按 UUID 读取 durable capability；不存在必须失败。 |
| `MsiConfirmationStore.resolve_confirmation(...)` | 持久化批准/拒绝/过期及对应 transaction 状态。 |
| `MsiConfirmationStore.consume_confirmation_pair(...)` | 原子验证和消费 parent/child 两个批准，防止 replay/double-click。 |
| `MsiUninstallConfirmationService.__init__(store, plan_ttl_seconds, runtime_ttl_seconds, now)` | 校验 TTL、注入 store/时钟；runtime 默认更短。 |
| `request_plan(plan, preview)` | 要求 executable 当前 Preview，创建并持久化第一确认。 |
| `resolve_plan(id, approved, plan, preview)` | 重新核对绑定/expiry/current state，持久化用户决定。 |
| `request_runtime(parent_id, plan, preview)` | 要求已批准 parent 和相同 invariant 的 fresh Preview，创建短时第二确认。 |
| `resolve_runtime(id, approved, plan, preview)` | 重新核对 parent、证据、risk、Preview 和 expiry 后保存即时决定。 |
| `consume_runtime(id, plan, preview)` | 要求 runtime approved 和 parent plan approved，随后由 store 原子标记二者 consumed/transaction dispatching。 |
| `_resolve(...)` | 两级 resolve 的共享校验/状态转换实现。 |
| `_create(tier, plan, preview, ...)` | 从 plan/Preview 创建完整 digest-bound capability 和明确对象摘要。 |
| `_require_not_expired(confirmation)` | 发现过期会先 durable 保存 EXPIRED，再抛确认错误。 |
| `_require_executable(plan, preview)` | 校验 plan/Preview IDs/digests/invariant、风险和 executable，阻止旧/改写对象。 |
| `_require_current(confirmation, plan, preview, tier)` | 比较存储 capability 与当前全部字段，阻止 risk/capability/ProductCode/identity 替换。 |

### 持久化和执行 guard `persistence.software_uninstall_execution`

| 函数 / 方法 | 作用、输入输出和安全约束 |
|---|---|
| `MsiUninstallRepository.__init__(database_path)` | 创建独立 SQLite engine/session factory；尚未初始化时所有操作 fail closed。 |
| `initialize()` | 建表和探测数据库；把 dispatching/executing/waiting等 active 状态标为 INTERRUPTED，把未消费确认标为 EXPIRED/CANCELLED；返回中断 transaction IDs，绝不重试。 |
| `create(plan, preview)` | 在任何 confirmation 前持久化 exact transaction/tool argument digest；全局或同 identity 已有 active transaction 时拒绝。 |
| `transition(transaction_id, state, ...)` | 只允许显式状态图中的单步转换，并写入最小 installer/verification/error 数据。 |
| `state(transaction_id)` | 读取当前 durable 枚举状态；未知 ID 或数据库错误抛 store error。 |
| `save_plan_confirmation(confirmation)` | 原子插入 PLAN row 并从 PREVIEWED 进入 AWAITING_PLAN_CONFIRMATION。 |
| `save_runtime_confirmation(confirmation, preview)` | 原子更新 fresh Preview/argument digests，插入 RUNTIME row并进入 AWAITING_RUNTIME_CONFIRMATION。 |
| `get_confirmation(id)` | 从 row 恢复 Pydantic capability；SQLite naive timestamp 会恢复为 UTC。 |
| `resolve_confirmation(confirmation)` | 只允许 pending/approved 的合法下一状态，并同步 transaction 的 confirmed/cancelled 状态。 |
| `consume_confirmation_pair(plan_confirmation, runtime_confirmation)` | 在单事务中检查 parent、approved、所有 row/transaction binding 后同时 consumed 并进入 DISPATCHING。 |
| `close()` | dispose SQLite engine 并清除 initialized 标志。 |
| `_save_confirmation(...)` | plan/runtime 共用的原子插入和 transaction exact-state比较实现。 |
| `_transaction(session, id)` | 获取 exact transaction row；不存在抛 store error。 |
| `_require_initialized()` | 防止数据库未验证时执行任何高风险状态操作。 |
| `MsiUninstallExecutionGuard.__init__(repository)` | 注入唯一 durable authorization source。 |
| `MsiUninstallExecutionGuard.require(authorization, tool_name, arguments)` | 紧邻工具调用核对 consumed IDs、tool、argument digest、ProductCode/identity/operation/plan/Preview，然后 durable 进入 EXECUTING；失败转为 `WriteAuthorizationError`。 |
| `_request_for_preview(preview)` | 只从 validated Preview 构造唯一允许的 typed tool request。 |
| `_confirmation_to_row(value)` | Pydantic capability 映射为 privacy-minimized SQL row。 |
| `_confirmation_from_row(row)` | SQL row 恢复强类型 capability 与枚举/UUID/UTC 时间。 |
| `_as_utc(value)` | 给 SQLite 丢失 timezone 的 timestamp 附加 UTC，已有 timezone 则转换 UTC。 |

### 工具、审计和编排

| 函数 / 方法 | 作用、输入输出和安全约束 |
|---|---|
| `MsiUninstallTool.__init__(platform)` | 创建固定 manifest：唯一工具名、R2、write、irreversible、NONE rollback、runtime confirmation、Preview、batch 1。 |
| `MsiUninstallTool.manifest` | 返回不可变 manifest 供 registry/independent reviewer 检查。 |
| `MsiUninstallTool.execute(request, cancellation)` | 要求 `MsiUninstallRequest`，把其中 typed product交给狭窄平台，返回结构化结果；错误类型或 raw 字符串不能通过。 |
| `MsiUninstallAuditLogger.__init__(repository, app_version, git_commit)` | 注入 append-only audit 和版本追踪，不持有 raw installer metadata。 |
| `previewed(plan, preview)` | 记录 plan/Preview/identity/ProductCode/capability/safety/preflight digest、counts、风险和 NONE rollback。 |
| `confirmation_resolved(plan, confirmation)` | 记录 tier、状态、ID 和 digest，不记录 raw ProductCode/command。 |
| `started(plan, preview, runtime_confirmation_id)` | mandatory write-ahead event；失败会阻止 adapter launch。 |
| `completed(plan, report)` | 分别记录 installer category/exit code、verification、residual flags 和 recovery level。 |
| `failed(plan, phase, error_code, mutation_may_have_started)` | 记录安全错误类别和 mutation 可能性，不写异常中的本地 raw 内容。 |
| `MsiUninstallService.__init__(...)` | 依赖注入完整 identity/policy/preflight/confirmation/repository/registry/verifier/audit 边界及 elevation probe。 |
| `prepare(user_goal, query, cancellation)` | fresh resolve；若唯一目标则完成 capability/ProductCode/policy/preflight/Preview/review/audit/transaction 并请求 PLAN confirmation；歧义只返回 candidates。 |
| `resolve_plan_confirmation(id, approved, plan, preview)` | 委托 confirmation service 并记录第一 gate 用户决定。 |
| `prepare_runtime_confirmation(parent_id, plan, cancellation)` | 进入 VALIDATING，重新运行 elevation、identity、capability、ProductCode、policy、preflight、Preview 和 review；只有完全重现 invariant 才请求 runtime gate。 |
| `resolve_runtime_confirmation(id, approved, plan, preview)` | 保存和审计第二 gate 决定。 |
| `execute(runtime_id, plan, preview, cancellation)` | 原子消费确认、写前审计、构造 exact authorization、经 registry/guard dispatch 一次；long-running 保持 WAITING，否则刷新验证、残留报告、终态和审计。任何 post-launch错误不触发 retry。 |
| `_fresh_target(identity_digest, cancellation)` | fresh inspect exact identity 并取只在内存存在的 raw entry；warning/truncation、identity/raw 缺失即 TARGET_CHANGED/RAW_EVIDENCE_MISSING。 |
| `_terminal_state(installer, verification)` | 将 installer/verification 二维结果映射为 truthful transaction 终态，优先保留 reboot/user-cancel/privilege 和 unverified。 |

### 运行时和 GUI

| 函数 / 方法 | 作用、输入输出和线程/安全约束 |
|---|---|
| `MsiUninstallServices` | 将专用 registry、resolver 和 orchestration service 打包给 GUI/测试。 |
| `ApplicationRuntime.__init__(settings)` 的 Stage 4D2A 部分 | 初始化 `MsiUninstallRepository`，立即执行 crash recovery，并公开只读 `interrupted_msi_uninstall_ids`；数据库不可验证时应用初始化失败。 |
| `ApplicationRuntime.create_msi_uninstall_services()` | 组装生产 Windows inventory/MSI adapter、专用 store/guard/registry、policies、preflight、confirmations、verifier、residual 和 audit；不复用通用命令执行器。 |
| `ApplicationRuntime.close()` 的 Stage 4D2A 部分 | 在共享 audit 关闭前 dispose MSI transaction store，避免 GUI 退出后遗留连接。 |
| `AppSettings.from_env()` 的 Stage 4D2A 部分 | 读取 runtime confirmation TTL、monitor poll 和 long-running threshold；Pydantic 限制安全范围，未提供任意 executable/args 配置。 |
| `PreparedMsiUninstallWithServices` | Qt worker payload，确保后续确认/执行继续使用产生该 Preview 的同一服务图。 |
| `MsiUninstallPrepareWorker.__init__(runtime, user_goal, query)` | 保存输入并创建 cancellation token，不在 GUI 线程访问 Windows inventory。 |
| `MsiUninstallPrepareWorker.run()` | 后台创建服务并调用 `prepare`；异常转为可见 failed signal，成功发 candidates/Preview。 |
| `MsiUninstallPrepareWorker.cancel()` | 协作取消剩余只读准备步骤。 |
| `MsiRuntimePrepareWorker.__init__(services, plan_confirmation_id, plan)` | 保存第一 confirmation 和 immutable plan 供后台 fresh revalidation。 |
| `MsiRuntimePrepareWorker.run()` | 校验 UUID并后台请求 runtime Preview/confirmation；不执行 adapter。 |
| `MsiRuntimePrepareWorker.cancel()` | 只取消未完成的运行时重新验证。 |
| `MsiUninstallExecuteWorker.__init__(services, runtime_confirmation_id, plan, preview)` | 保存 single-use capability 和 exact对象，创建 cancellation token。 |
| `MsiUninstallExecuteWorker.run()` | 后台消费确认、执行和监控/验证；异常转 failed signal，避免冻结 Qt。 |
| `MsiUninstallExecuteWorker.cancel()` | 启动前可阻止 spawn；启动后只记录请求，不 kill MSI。 |
| `require_prepared_msi_uninstall(value)` | Qt object signal 的 runtime type narrowing；错误 payload 抛 TypeError。 |
| `require_runtime_msi_confirmation(value)` | 确保 payload 是 fresh runtime Preview/confirmation。 |
| `require_msi_uninstall_report(value)` | 确保 payload 是 verified final execution report。 |
| `SoftwareUninstallDialog.__init__(runtime, user_goal, query, parent)` | 创建 modeless 两级确认窗口，默认焦点/按钮为取消，并立即启动后台准备。 |
| `_build_ui()` | 创建风险说明、详情、单选 candidates、indeterminate progress 和取消优先按钮；没有通用参数输入。 |
| `_start_prepare(query)` | 启动 prepare worker 并把 UI 置为尚未授权状态。 |
| `_prepared(value)` | 接收 candidates 或 reviewed Preview；缺 plan/confirmation/review fail closed。 |
| `_show_candidates(candidates, reason)` | 显示明确单选表，identity digest 存在本地 item data；不会按第一候选自动执行。 |
| `_primary_clicked()` | 只按明确 state machine 分派选择/计划确认/runtime确认/关闭，其他状态无动作。 |
| `_select_candidate()` | 从已选择行取 identity digest，废弃旧状态并生成全新计划。 |
| `_approve_plan()` | 保存第一次批准，然后在后台完整重新验证；不直接调用工具。 |
| `_runtime_prepared(value)` | 显示 fresh 即时确认；R2_HIGH 按钮加强警告但默认仍是取消。 |
| `_approve_runtime_and_execute()` | 保存第二次批准并启动 execute worker；UI 自身不调用 registry/platform。 |
| `_completed(value)` | 显示 installer、verification、residual 和 recovery 的分离结果。 |
| `_show_preview(preview, immediate)` | 生成 plan 或 runtime 阶段对象级说明并设置取消优先。 |
| `_cancel_clicked()` | pending gate 时持久化拒绝；worker 时发协作取消；已启动 MSI 不强杀。 |
| `_reject_plan_and_close()` | durable 拒绝 PLAN confirmation 后关闭，finally 确保窗口结束。 |
| `_reject_runtime_and_close()` | durable 拒绝 RUNTIME confirmation 后关闭。 |
| `_failed(message)` | 显示 fail-closed 状态并明确无自动 retry/elevation/process/service/residual action。 |
| `_set_busy(text)` | 设置不确定进度并禁用批准按钮。 |
| `shutdown()` | 受控退出时请求协作取消；不终止已启动 installer。 |
| `closeEvent(event)` | worker 活跃时忽略直接关闭并先取消；pending confirmation 视为拒绝。 |
| `_preview_html(preview, immediate)` | HTML 转义所有本地字符串，显示精确对象、ProductCode、风险、preflight、NONE rollback 和不会自动做的动作。 |
| `_report_html(report)` | HTML 转义并分开显示 installer/verification/residual/recovery；不把 exit 0 单独写成成功。 |
| `SystemDiagnosticsTab.open_msi_uninstall(user_goal, query)` | 从安全软件表或聊天打开 modeless dialog，登记生命周期并提示“尚未授权”。 |
| `SystemDiagnosticsTab._open_selected_msi_uninstall()` | 将单选表的 name/version/publisher/scope/architecture 转为 query，未选目标时拒绝。 |
| `SystemDiagnosticsTab._software_selection_changed()` 的 Stage 4D2A 部分 | 只在恰好选择一个可投影软件行时启用 MSI 审查按钮；选择本身不授权。 |
| `SystemDiagnosticsTab.shutdown()` 的 Stage 4D2A 部分 | 对所有存活卸载 dialog 调用安全 shutdown，防止主窗口退出时丢失 worker 所有权。 |
| `MainWindow._handle_chat()` 的 Stage 4D2A 分支 | 本地提取明确显示名称并创建 `SoftwareTargetQuery`；不解析 ProductCode/命令/参数。 |

补充错误/记录类型：`MsiProductValidationError`、`MsiUninstallExecutionError` 和
`MsiUninstallStoreError` 分别标识身份、编排和持久化 fail-closed 边界；其构造函数只接收安全
code/message。`PreparedMsiUninstall` 与 `PreparedMsiRuntimeConfirmation` 是编排阶段返回值，
不会自行执行。`MsiUninstallTransactionRow`/`MsiUninstallConfirmationRow` 是内部 SQL 映射，
禁止进入模型 payload。`SoftwareUninstallSafetyPolicy.assess()` 现在优先把 Visual C++、
redistributable 和 shared-runtime 关键词分类为 `SHARED_RUNTIME`，避免落入 developer-runtime
可执行规则。

## Stage 4D1 软件身份与卸载 Preview API（零执行）

本节覆盖 Stage 4D1 新增或修改的每个生产函数和方法。这里的“卸载能力”仅表示本地元数据足以
识别一种机制；它不表示安全、可执行或已授权。全部五个工具都是 R0，结果固定
`execution_performed=false`，target acknowledgement 后工作流必须停止。

### 领域枚举、原始数据和安全投影 `domain.software_uninstall_analysis`

| 类/函数/方法 | 详细作用、输入、返回与安全约束 |
|---|---|
| `canonical_digest(payload)` | 将可 JSON 序列化对象按键排序、紧凑编码为 UTF-8 后计算 SHA-256；用于身份、计划、Preview 和确认绑定。它不读取系统、不授权操作。 |
| `SoftwareSource` | 有限来源枚举：MSI、Registry、Vendor、Package Manager、MSIX、Portable、Windows Feature、Driver 与 Unknown；未知来源不能被自动提升为已支持机制。 |
| `RegistryHive` / `RegistryView` | 仅表达 HKCU/HKLM 与 x86/x64 读取来源，使同名条目保持来源可区分；不是通用注册表地址或写权限。 |
| `SoftwareSafetyClass` / `SoftwareSafetyDecision` | 分别表达保护/高影响/普通/开发者/未知分类和 blocked/high-impact/allowed Preview 决策；没有 execute 决策值。 |
| `UninstallCapabilityType` / `CapabilitySupport` | 表达观察到的机制类型及元数据是否足够；`METADATA_SUPPORTED` 只允许生成说明，不允许调用卸载器。 |
| `EvidenceKind` / `ImpactSeverity` | 区分已知路径相关、名称启发式和未知证据，以及影响严重度；避免把弱相关说成确定依赖。 |
| `TargetAcknowledgementState` | `PENDING`、`ACKNOWLEDGED`、`REJECTED`、`EXPIRED` 状态；任何终态都不携带执行授权。 |
| `RawInstalledSoftwareEntry` | Windows/包来源的本地原始记录。卸载/静默命令字段被排除于序列化和 repr，只能在短生命周期内部快照使用。 |
| `RawInstalledSoftwareEntry.source_anchor_digest()` | 摘要来源、scope、architecture、注册表或包的精确锚点；不包含可变显示文本，用于发现来源替换。 |
| `RawInstalledSoftwareEntry.command_metadata_digest()` | 只对原始命令元数据生成摘要；调用方可比较变化但不能取得或记录原文。 |
| `SoftwareIdentity` | 归一化稳定身份：版本化 identity schema、来源、scope、architecture 与 source anchor；同名不等于同一身份。 |
| `SoftwareIdentity.canonical_digest()` | 生成 source-qualified 身份摘要；用于精确 resolve/inspect 和确认失效。 |
| `NormalizedInstalledSoftware` | 可安全进入 UI/审计判定的投影，包含名称、版本、发布者、大小、位置、来源和 warnings，但不含卸载命令。 |
| `NormalizedInstalledSoftware.metadata_digest()` | 摘要规范化元数据；版本、发布者、位置等变化会使旧 Preview 失效。 |
| `SoftwareInventory` | 一次有界采集的不可变 entries、时间、warnings 和 truncated 标记；不会缓存为执行依据。 |
| `SoftwareTargetQuery` | 精确 identity digest，或 display name 加可选 publisher/version/scope/architecture 过滤器。 |
| `SoftwareTargetQuery.require_selector()` | Pydantic 后校验；identity 与 name 都缺失时抛 `ValidationError`，防止无目标分析。 |
| `ResolvedSoftwareTarget` | 精确 selected 或显式 ambiguous candidates；即使候选为零也保持未解析状态。 |
| `ResolvedSoftwareTarget.validate_resolution()` | 拒绝 selected+ambiguous、无 selected 却未标 ambiguous 等矛盾状态。 |
| `ParsedUninstallMetadata` | 命令解析的安全结构：摘要、exe 路径/存在性/绝对或 UNC、wrapper、已知 switches、参数数量和 warnings；不含完整参数。 |
| `UninstallCapability` | 类型、support、证据、限制、可选安全解析投影及 identity/metadata bindings。 |
| `UninstallCapability.canonical_digest()` | 摘要能力结论；解析、证据或限制变化使旧 Preview 无效。 |
| `SoftwareSafetyAssessment` | 确定性安全分类、Preview 决策、证据与原因；不是执行权限。 |
| `SoftwareSafetyAssessment.canonical_digest()` | 摘要完整策略结论，供 Preview/audit 比对。 |
| `SoftwareImpactFinding` | 一条进程/启动项/服务相关线索，标记证据种类、严重度、说明和是否已知。 |
| `SoftwareImpactAssessment` | 有界 findings、明确 unknowns、warnings 和采集时间；不声称完整依赖图。 |
| `SoftwareImpactAssessment.canonical_digest()` | 摘要影响证据，使关联项改变可被发现。 |
| `SoftwareUninstallAnalysisPlan` | 固定五工具 R0 计划，绑定目标、数量上限、零预计修改、NONE rollback、plan confirmation 和无 runtime confirmation。 |
| `SoftwareUninstallAnalysisPlan.validate_zero_execution_contract()` | Pydantic 后校验；拒绝非 R0、可写、运行时确认、非 NONE、预计修改大于零或重复工具。 |
| `SoftwareUninstallAnalysisPlan.canonical_digest()` | 摘要除生成时间外的全部授权字段；目标或工具链变化使确认失效。 |
| `SoftwareUninstallPreview` | 最终安全投影：target、capability、policy、impact、所有 digests、TTL、`executable_in_current_stage=false`、`execution_performed=false` 和 stop reason。 |
| `SoftwareUninstallPreview.enforce_zero_execution()` | Pydantic 后校验；拒绝任一执行标记、非 R0/NONE、过期顺序或内部 digest 不一致。 |
| `SoftwareUninstallPreview.canonical_digest()` | 摘要完整 Preview，用于 acknowledgement 精确绑定。 |
| `SoftwareTargetAcknowledgement` | 记录用户是否理解当前 target/Preview、绑定摘要和有效期；没有 tool arguments 或 capability token。 |
| `SoftwareInventoryRequest/Result` | `max_items` 有界输入与安全 inventory 输出；结果固定 zero execution。 |
| `SoftwareResolveRequest/Result` | 目标 query+上限输入，返回 resolution 和本次采集时间；不能自动选择 fuzzy 候选。 |
| `SoftwareInspectRequest/Result` | 精确 identity digest 输入，返回当前 normalized 项和 found；缺失不伪造对象。 |
| `SoftwareCapabilityRequest/Result` | identity+上限输入，返回与该身份绑定的 capability；不返回 raw command。 |
| `SoftwarePreviewRequest/Result` | 不可变 plan+identity 输入，返回最终 Preview；没有 execute 选项。 |
| `SoftwareAnalysisOutcome` | 分析终态：ambiguous resolution 或 exact resolution+Preview 二选一。 |
| `SoftwareAnalysisOutcome.bind_preview_to_resolution()` | 后校验 exact/ambiguous 与 Preview 的组合，并核对 Preview target identity；矛盾时抛 `ValidationError`。 |

### 异常 `domain.software_errors`

| 类/方法 | 作用 |
|---|---|
| `SoftwareAnalysisErrorCode` | 稳定错误码，覆盖确认、审计、目标变化、解析、策略、能力、零执行和取消等安全停止原因。 |
| `SoftwareAnalysisError.__init__(code, message)` | 创建带稳定 code 的 `RuntimeError`；message 可展示但不会触发 Shell、提权或执行退路。 |

### 平台采集与命令元数据解析

| 类/函数/方法 | 详细作用 |
|---|---|
| `PackageInventoryProvider.collect(max_items, cancellation)` | 可插拔结构化包来源协议；返回原始记录、warnings、truncated。实现不得通过通用 shell 枚举。 |
| `SoftwareInventoryPlatform.collect_raw(max_items, cancellation)` | 平台采集协议；只读返回 bounded raw records，不做 normalization 或 execution。 |
| `UnavailablePackageInventoryProvider.collect(...)` | 默认诚实适配器；返回空记录和“结构化包来源不可用”说明，不调用 winget/PowerShell 猜测。 |
| `WindowsSoftwareInventoryPlatform.__init__(package_provider=None)` | 注入可选结构化包来源；缺省使用 unavailable provider，构造时不读注册表。 |
| `WindowsSoftwareInventoryPlatform.collect_raw(max_items, cancellation)` | 顺序读取固定 HKCU/HKLM x64/x86 Uninstall 视图并合并包来源；检查取消/上限，单源失败变 warning，绝不读取 `Win32_Product` 或执行 uninstall string。 |
| `WindowsSoftwareInventoryPlatform._collect_registry_view(...)` | 打开一个固定 hive/view 的卸载根键并逐子键查询 values；访问拒绝/格式错误安全跳过，原始来源 ID 绑定 hive/view/key。 |
| `_registry_sources()` | 返回固定四个 hive/view/Win32 flag 组合；没有 caller-supplied registry path。 |
| `_read_values(key)` | 查询当前卸载子键的允许值集合；只读，缺失值不报成执行错误。 |
| `_text(value)` | 将 registry value 保守转换/trim 为非空字符串，否则 `None`。 |
| `_optional_bool(value)` | 仅将明确 0/1 等证据转换为 bool，未知返回 `None`。 |
| `_estimated_size(value)` | 将注册表 KiB 估算值转换为非负 bytes；非法/负值返回 `None`，避免伪精确。 |
| `parse_windows_uninstall_metadata(command_line)` | 对不可信字符串进行最大 32,768 字符限制和结构解析；检查绝对/UNC、wrapper、`.exe`、存在性及有限 switches，返回 sanitized model，永不启动程序。 |
| `_command_line_to_argv(command_line)` | Windows 使用 `CommandLineToArgvW` 获得 argv 并 `LocalFree`；非 Windows 仅为测试使用保守 `shlex` fallback；解析失败抛 `OSError` 供上层转 warning。 |

### 归一化、解析、能力与影响编排

| 类/函数/方法 | 详细作用 |
|---|---|
| `SoftwareInventorySnapshot` | 内部 dataclass，将 safe inventory 与 ephemeral `raw_by_identity` 配对；不得越过编排边界。 |
| `SoftwareInventoryService.__init__(platform)` | 注入只读平台采集器，不进行 I/O。 |
| `SoftwareInventoryService.collect(max_items, cancellation)` | 采集、Unicode/空白规范化、稳定 identity、保守去重并返回 snapshot；同 identity 不同 metadata 产生 conflict warning，不静默合并命令。 |
| `SoftwareInventoryService.project_legacy(max_items, cancellation)` | 将新 inventory 映射为 Stage 3 `InstalledSoftware` 显示模型，保留 warning/truncated；不把 raw command 带回旧 API。 |
| `normalize_raw_entry(raw)` | 将一条 raw record 规范化；缺失 display name 或必要锚点时返回 `None`，MSI ProductCode 仅接受严格 GUID。 |
| `_clean(value)` | Unicode NFKC、trim 并把空字符串转换为 `None`；不解释内容为指令。 |
| `SoftwareTargetResolver.__init__(inventory, max_candidates=100)` | 注入 fresh inventory service 并限制候选数量。 |
| `SoftwareTargetResolver.refresh(max_items, cancellation)` | 每次决策重新采集 authoritative snapshot，不复用旧 UI 清单。 |
| `SoftwareTargetResolver.resolve(query, max_items, cancellation)` | identity 精确匹配，或 exact name+filters 匹配；不唯一/无精确结果返回 bounded ambiguous candidates，substring 永不自动 selected。 |
| `SoftwareTargetResolver.inspect(identity_digest, max_items, cancellation)` | fresh re-read 后仅在 digest 恰好唯一时返回 target，否则 `None`。 |
| `SoftwareTargetResolver._filters(entries, query)` | 仅应用 caller 已给的 publisher/version/scope/architecture 精确过滤，不推断缺失字段。 |
| `SoftwareTargetResolver._ambiguous(query, candidates, reason)` | 构造显式 ambiguous 结果并截断候选；不把唯一 substring 偷换成 exact。 |
| `UninstallCapabilityResolver.resolve(software, raw)` | 先核对 raw source anchor，再分类 MSI/vendor/package/MSIX/portable/feature/driver/unknown；MSI 与结构化包必须有一致精确 ID，vendor 只调用安全 parser。 |
| `UninstallCapabilityResolver._identity_digest_for(software)` | 返回 normalized identity digest，统一绑定 capability。 |
| `_unsupported(reason)` | 构造 UNKNOWN/UNSUPPORTED capability 和固定原因，不猜测执行方式。 |
| `SoftwareImpactAnalyzer.__init__(platform, max_items=5000)` | 注入 Stage 3 只读诊断平台和有界采集上限。 |
| `SoftwareImpactAnalyzer.analyze(software, cancellation)` | 读取进程/启动项/服务并按安装路径做 known correlation、按规范名称做 heuristic correlation；失败变 warning/unknown，不修改对象。 |
| `software_impact_analyzer._canonical(path)` | resolve(strict=False)+normcase，用于本地比较；不访问目标内容。 |
| `software_impact_analyzer._is_within(path, root)` | 用 `relative_to` 判断路径包含关系，异常返回 false。 |

### 策略、Preview、独立校验与零执行守卫

| 类/函数/方法 | 详细作用 |
|---|---|
| `SoftwareUninstallSafetyPolicy.__init__(agent_root, windows_directory=None)` | 规范化 Agent/Windows 根用于保护判断；缺省 Windows 根来自环境的系统目录。 |
| `SoftwareUninstallSafetyPolicy.assess(software)` | 固定顺序判定 Windows/driver/Agent/system/security/unknown、high-impact、developer 或 user app；只返回 Preview decision，永不授权卸载。 |
| `_assessment(class, decision, evidence, reasons)` | 构造不可变策略结论，避免各分支遗漏解释。 |
| `_contains(value, terms)` | 大小写规范化文本的有限关键词包含判断；仅作为显式策略证据。 |
| `software_uninstall_policy._canonical(path)` | 对策略根/安装位置规范化用于边界比较。 |
| `software_uninstall_policy._is_within(path, root)` | 安装位置是否位于保护根；ValueError 时 false。 |
| `SoftwareUninstallPreviewEngine.__init__(policy, capability, impact, ttl_seconds)` | 注入三类确定性分析器和短时 TTL；不持有执行器。 |
| `SoftwareUninstallPreviewEngine.build(plan, software, raw, cancellation)` | fresh 计算 capability/policy/impact，绑定所有摘要和 expiry，生成强制 stop 的 zero-execution Preview。 |
| `SoftwareSafetyIssue` / `SoftwareSafetyReview` | 独立校验问题与 approved 聚合结果；不自动修复计划。 |
| `SoftwareUninstallSafetyValidator.__init__(registry, guard)` | 注入专用 registry 与零执行 guard。 |
| `SoftwareUninstallSafetyValidator.review_plan(plan)` | 验证 exact tool set、R0/read-only/NONE/zero changes，并用各 manifest schema 验证结构化参数；异常转为 issue，fail closed。 |
| `SoftwareUninstallSafetyValidator.review_preview(plan, preview)` | 核对 plan、identity、capability 摘要、R0、zero-execution 与 stop reason；任一问题使 approved=false。 |
| `SoftwareZeroExecutionGuard.validate_registry(registry)` | 要求 registry names 精确等于五工具集合，且每个 manifest 为 R0/read-only/NONE/no runtime confirmation；多一个或少一个都抛 `SoftwareAnalysisError`。 |
| `SoftwareZeroExecutionGuard.validate_result(result)` | 要求任何工具结果显式 `execution_performed is False`；缺失或 true 时抛零执行违规。 |

### 五个注册工具 `tools.system_tools.software_analysis`

| 函数/类方法 | 详细作用 |
|---|---|
| `_manifest(name, description, input_model, output_model)` | 创建统一 R0/read-only/cancellable/NONE/no-runtime-confirmation manifest，固定 Windows 平台、上限、前后置条件和审计字段。 |
| `SoftwareInventoryTool.__init__(inventory)` | 注入 inventory service 并创建 `software.inventory` manifest。 |
| `SoftwareInventoryTool.manifest` | 返回不可变 manifest，无 I/O。 |
| `SoftwareInventoryTool.execute(request, cancellation)` | 强类型检查 `SoftwareInventoryRequest`，执行 bounded collect 并只返回 safe inventory。 |
| `SoftwareResolveTool.__init__(resolver)` / `.manifest` | 注入 resolver并暴露 `software.resolve` 的只读 manifest。 |
| `SoftwareResolveTool.execute(request, cancellation)` | fresh resolve validated query，返回显式 resolution 与采集时间。 |
| `SoftwareInspectTool.__init__(resolver)` / `.manifest` | 构建 `software.inspect` exact re-read 工具。 |
| `SoftwareInspectTool.execute(request, cancellation)` | fresh inspect identity；返回 found/None，不把消失目标当成功。 |
| `SoftwareUninstallCapabilityTool.__init__(resolver, capability)` / `.manifest` | 注入 fresh resolver 与本地 capability analyzer，构建第四个工具。 |
| `SoftwareUninstallCapabilityTool.execute(request, cancellation)` | fresh inspect，要求 matching raw evidence，随后只分析 metadata；目标/raw 消失抛 `TARGET_CHANGED`。 |
| `SoftwareUninstallPreviewTool.__init__(resolver, preview_engine)` / `.manifest` | 注入 fresh resolver 与 non-executable Preview engine，构建第五个工具。 |
| `SoftwareUninstallPreviewTool.execute(request, cancellation)` | 再次读取 exact target/raw，随后生成 Preview；证据变化 fail closed，没有卸载分支。 |

### 计划编排、确认与审计

| 类/函数/方法 | 详细作用 |
|---|---|
| `is_software_uninstall_analysis_request(user_goal)` | 本地有限关键词识别“卸载/移除软件影响分析”意图；只负责 UI 路由，不调用模型或工具。 |
| `extract_software_target_name(user_goal)` | 从受支持的自然语言前缀中提取候选显示名；提取失败返回 `None`，不扩大范围。 |
| `SoftwareUninstallAnalysisPlanCompiler.__init__(max_items=5000)` | 保存有界 inventory 上限。 |
| `SoftwareUninstallAnalysisPlanCompiler.compile(user_goal, query)` | 创建固定 exact five-tool R0 plan；不接受 caller 提供任意工具名。 |
| `SoftwareUninstallAnalysisService.__init__(...)` | 注入 compiler、registry、validator、guard、confirmation 和 audit；构造时不采集软件。 |
| `SoftwareUninstallAnalysisService.prepare(user_goal, query=None)` | 本地提取或验证 query、编译计划、独立 review 并审计；不 approved 时抛安全错误。 |
| `.request_plan_confirmation(plan)` | 创建绑定 plan ID/digest/工具参数摘要/expiry 的计划确认并审计。 |
| `.resolve_plan_confirmation(id, approved, plan)` | 消费一次确认；拒绝、过期、重放或 digest 变化 fail closed 并审计。 |
| `.analyze(plan, cancellation=None)` | 要求计划已批准，按五工具顺序执行并逐结果过 zero guard；ambiguous 返回候选，exact 时比较 fresh metadata/capability、校验 Preview 并停止。 |
| `.request_target_acknowledgement(plan, preview)` | 为当前 validated Preview 创建短时 acknowledgement；不创建 `ExecutionAuthorization`。 |
| `.resolve_target_acknowledgement(id, acknowledged, plan, preview)` | 核对全部绑定并记录 acknowledged/rejected/expired；无论结果都结束流程。 |
| `._execute_inventory/._execute_resolve/._execute_inspect/._execute_capability/._execute_preview` | 五个内部 typed wrapper：只通过 registry 执行、收窄返回类型并立即验证 zero execution；类型异常 fail closed。 |
| `SoftwareAnalysisConfirmationService.__init__(ttl_seconds=...)` | 创建进程内的一次性确认/ack 存储；无持久执行权限。 |
| `.request_plan(plan)` | 生成 plan-bound `ConfirmationRequest`，绑定规范摘要、工具/对象说明与 expiry。 |
| `.resolve_plan(id, approved, plan)` | 验证 ID、未消费、未过期和 plan digest；返回 resolved confirmation。 |
| `.require_plan_approved(plan)` | 分析前要求当前 plan 已有匹配 approved 状态，否则抛确认错误。 |
| `.request_acknowledgement(plan, preview)` | 创建 Preview-bound target acknowledgement，明确用途仅为理解目标。 |
| `.resolve_acknowledgement(id, acknowledged, plan, preview)` | 检查 plan/Preview/digests/expiry 并一次性终结；不转换为工具授权。 |
| `SoftwareUninstallAnalysisAuditLogger.__init__(repository, app_version, git_commit)` | 注入 SQLite repository 和版本信息；不保存 raw source。 |
| `.plan_reviewed(...)` | 记录 goal/plan 摘要、validator decision 和 zero changes。 |
| `.plan_confirmation(...)` | 记录 confirmation ID、绑定摘要和批准/拒绝，不存用户输入正文以外的敏感 metadata。 |
| `.inventory_completed(...)` | 仅记录数量、warnings/truncated 和 zero execution。 |
| `.target_resolved(...)` | 记录 query 摘要、selected identity digest 或候选数量，不记录 registry key/command。 |
| `.previewed(...)` | 记录 identity/metadata/capability/Preview digests、安全分类、影响计数和 stop。 |
| `.acknowledgement_resolved(...)` | 记录 acknowledgement 终态及绑定摘要，明确 authority=false。 |
| `.failed(...)` | 记录稳定 error code、阶段和脱敏消息；仍声明 execution=false。 |

### 运行时与 Qt UI

| 类/函数/方法 | 详细作用 |
|---|---|
| `SoftwareAnalysisServices` | runtime dependency bundle：专用 registry、inventory、resolver 和 orchestration service；不包含 uninstaller。 |
| `ApplicationRuntime.create_software_analysis_services()` | 组装 Windows只读采集、五工具专用 registry、策略/影响/Preview、validator、confirmation 和 audit；立即运行 exact allow-list guard。 |
| `SoftwareWorkerSignals` | Qt `completed/failed` 信号容器；跨线程只传 object/string。 |
| `PreparedSoftwareAnalysis` | worker 返回的强类型 plan+review，不表示 confirmed。 |
| `SoftwarePrepareWorker.__init__(services, goal, query)` | 保存依赖与输入，不在 UI 线程采集。 |
| `SoftwarePrepareWorker.run()` | 后台调用 `prepare`，成功发 typed prepared，异常只发失败消息。 |
| `SoftwareAnalyzeWorker.__init__(services, plan)` | 保存 immutable plan 和 cooperative token。 |
| `SoftwareAnalyzeWorker.run()` | 后台调用 `analyze`；不会创建 execution worker。 |
| `SoftwareAnalyzeWorker.cancel()` | 设置 cooperative cancellation，阻止后续只读步骤。 |
| `require_prepared_software_analysis(value)` | 收窄 Qt object signal；错误类型抛 `TypeError`。 |
| `require_software_analysis_outcome(value)` | 只接受 `SoftwareAnalysisOutcome`，避免 UI 把任意对象当成功。 |
| `SoftwareAnalysisDialog.__init__(runtime, user_goal, query=None, parent=None)` | 创建非模态分析对话框、状态和 controls，然后开始只读 prepare；没有 execute 控件。 |
| `._build_ui()` | 构造计划、候选、Preview、确认/取消控件；默认按钮不授权卸载。 |
| `._start_prepare(query)` | 使旧 plan/ack 失效并在线程池启动 prepare worker。 |
| `._prepared(value)` / `._show_plan(prepared)` | 强类型接收并显示目标、工具、R0、零修改和 NONE rollback。 |
| `._primary_clicked()` | 按当前有限 UI state 分派计划确认或 target acknowledgement；无 execute state。 |
| `._approve_plan_and_analyze()` | 解析一次计划确认后启动 read-only analyze worker。 |
| `._analyzed(value)` | 显示 ambiguous candidates 或 final Preview；不会自动选择候选。 |
| `._show_candidates(resolution)` | 将 safe candidate 字段写表格和 UserRole identity；提示需重新计划。 |
| `._select_candidate()` | 读取单个显式选择的 identity digest 并调用 `_start_prepare`，旧确认失效。 |
| `._show_preview(preview)` / `_preview_html(preview)` | 显示安全字段、能力/影响/unknowns 和 stop reason；HTML escape 不可信文本，不显示 raw command。 |
| `._acknowledge_and_stop()` | 解析 target acknowledgement，显示“未执行”，随后关闭或停留终态。 |
| `._cancel_clicked()` | 取消 worker 或根据 state 记录拒绝；不强杀线程。 |
| `._reject_plan_and_close()` / `._reject_acknowledgement_and_close()` | 以 rejected 终态消费当前请求并关闭，不留下权限。 |
| `._failed(message)` | 清理 busy 状态并显示安全停止，不把错误当 Preview。 |
| `._set_busy(text)` | 仅调整 UI enabled/progress 文本。 |
| `.shutdown()` / `.closeEvent(event)` | 请求 cooperative cancellation 并从 runtime 集合清理；关闭不启动后台操作。 |
| `SystemDiagnosticsTab._software_selection_changed()` | 仅在恰好一个可解析软件行被选中时启用分析按钮。 |
| `SystemDiagnosticsTab._open_selected_software_analysis()` | 从 safe table projection 构建目标并打开 Stage 4D1 dialog，不直接调用工具。 |
| `SystemDiagnosticsTab.open_software_analysis(user_goal, query=None)` | 创建非模态 dialog、保留生命周期引用并在 finished 后移除。 |
| `SystemDiagnosticsTab._selected_software_query()` | 从名称/版本/发布者/scope/architecture 五列构造 exact-field query；列缺失或枚举非法返回 `None`。 |
| `MainWindow._handle_chat()` 的 Stage 4D1 分支 | 在进程/服务/通用诊断路由前识别卸载分析意图并打开 dialog；不会将自然语言变成命令。 |

## Stage 4C2 Windows 服务启动类型安全管理 API

本节覆盖 Stage 4C2 新增或因稳定身份拆分而修改的每个生产函数和方法。所有写入均为单个
ServiceName 的 R2 操作；“Automatic”在本节始终表示非延迟 Automatic。Delayed Automatic、
Disabled 以及任意其他服务配置字段没有写入口。

### 领域模型 `domain.service_startup_actions`

| 类/函数 | 作用、输入、返回值、异常与安全约束 |
|---|---|
| `ServiceStartupActionType` | 有限动作枚举：`SET_AUTOMATIC`、`SET_MANUAL`、`RESTORE`；工具和编排不接受任意配置动作。 |
| `ServiceStartupManagementMode` | UI/策略结论：可变更、可恢复、只读或阻止；只负责表达确定性结论，不授权执行。 |
| `ServiceStartupErrorCode` | 隐私安全的稳定错误码，覆盖身份/配置/状态/依赖漂移、权限、备份、确认、审计、事务、冲突和验证失败；消息不携带命令或秘密。 |
| `ServiceStartupTransactionState` | 写前持久化状态机，从 `BACKUP_CREATED`、Preview、两级确认、验证/执行/复验直到终态；`INTERRUPTED` 不可自动恢复执行。 |
| `ServiceStartupPermissionEvidence` | 一次精确服务句柄权限探测，记录查询配置、修改配置和当前进程是否提权。 |
| `ServiceStartupPermissionEvidence.allows_change` | 无参数属性；仅在 query 与 `SERVICE_CHANGE_CONFIG` 都可用且进程未提权时返回 `True`，不修改 DACL、不请求 UAC。 |
| `ServiceStartupPermissionEvidence.canonical_digest()` | 对三个稳定权限结论生成 SHA-256，排除采集时间，使运行时权限漂移能使确认失效。 |
| `ServiceStartupImpact` | 展示排序后的依赖/被依赖名称、当前运行状态、“不应改变运行状态”和固定摘要。 |
| `ServiceStartupImpact.canonical_digest()` | 摘要完整影响模型；关系或运行状态变化会改变两级确认绑定。 |
| `ServiceStartupSafetyAssessment` | 策略输出：身份/源配置摘要、安全分类、管理模式、允许结论、原因码和固定解释。 |
| `ServiceStartupBackupPayload` | 仅在加密 vault 内使用的原始材料：稳定身份、显示名、原启动配置、原运行状态和时间；不进入工具参数或审计。 |
| `ServiceStartupBackupPayload.canonical_digest()` | 对解密后的完整备份内容生成摘要，供存入后立即验证和恢复时再次验证。 |
| `ServiceStartupBackupReference` | 对外只暴露 backup UUID、身份摘要、payload 摘要、时间和 verified 标记，不暴露密文或配置序列化字节。 |
| `ServiceStartupActionPlan` | 单对象不可变计划，绑定事务/操作、动作、稳定身份、源/目标配置、状态/影响/权限摘要、备份、R2、条件 FULL 和两级确认。 |
| `ServiceStartupActionPlan.validate_contract()` | Pydantic 后校验；拒绝非 Automatic/Manual、延迟、no-op、动作与目标不符、RESTORE 未绑定原记录、非 R2、非 FULL 或缺少任一确认层。失败抛 `ValidationError`。 |
| `ServiceStartupActionPlan.canonical_digest()` | 摘要所有授权字段；计划任一变化都会使已有 Preview 和确认无效。 |
| `ServiceStartupActionPreview` | 将计划、当前观察、目标、策略、影响、权限和已验证备份组合成只读 Preview。 |
| `ServiceStartupActionPreview.executable` | 仅当策略允许、普通权限齐全、备份已验证、状态非 pending、状态摘要自洽且明确不改变运行状态时为真。 |
| `ServiceStartupActionPreview.canonical_digest()` | 摘要整个 Preview，作为计划/即时确认和 SQLite 执行授权的一部分。 |
| `ServiceStartupActionRequest` | 注册工具唯一输入；含有限动作、稳定身份、精确源/目标配置、预期运行状态、影响摘要和已验证备份引用，没有通用字段字典。 |
| `ServiceStartupMutationResult` | 返回写前/写后配置和运行状态、是否派发、是否复验通过、运行状态是否未变、固定消息和时刻。 |
| `ServiceStartupActionTransaction` | 不含密文的持久化事务投影；包含全部绑定摘要、确认 ID、状态、隐私安全错误和结果。 |
| `ServiceStartupChangeRecord` | 一次成功 Agent-owned 变更；保存原/写入配置和备份引用，用于冲突检查后的新 RESTORE 事务。 |
| `canonical_service_startup_digest(payload)` | 对 JSON 可序列化对象进行排序、紧凑 UTF-8 编码并返回 SHA-256；供 Stage 4C2 的规范绑定，不授予任何权限。 |

### 领域异常 `domain.service_startup_errors`

| 类/方法 | 作用 |
|---|---|
| `ServiceStartupActionError.__init__(code, message)` | 构造带 `ServiceStartupErrorCode` 的工作流异常；编排用 code 决定终态，UI 使用 message，异常本身不触发退路或提权。 |

### 策略、影响、Preview 与独立复核

| 类/函数 | 作用、输入、返回值与拒绝条件 |
|---|---|
| `ServiceStartupSafetyPolicy.__init__(base_policy)` | 组合既有 Stage 4C1 保护服务策略；Stage 4C2 不复制或弱化其身份、账户、签名与路径检查。 |
| `ServiceStartupSafetyPolicy.assess(observation, action, target)` | 检查基础策略、源/目标类型、延迟标记、no-op 和依赖关系；只有无依赖的 Automatic/Manual 精确互转可允许，返回可审计 assessment。 |
| `build_service_startup_impact(observation)` | 排序依赖与被依赖 ServiceName，绑定当前状态并固定 `runtime_change_expected=False`；只读，无 SCM 副作用。 |
| `_blocked_mode(reasons)` | 仅延迟/不支持的转换显示 `READ_ONLY`，保护、Disabled、驱动或依赖风险显示 `BLOCKED`。 |
| `_explain(code)` | 将策略错误码映射为固定用户说明；只接受策略能够产生的码，避免模型解释改变风险。 |
| `ServiceStartupPreviewEngine.__init__(policy)` | 注入确定性策略。 |
| `ServiceStartupPreviewEngine.build(plan, observation, target, permissions, backup)` | 从新鲜本地证据构造 Preview，并绑定计划、状态、影响、权限和备份摘要；不调用写 API。 |
| `ServiceStartupSafetyReview` | 独立复核结果，含 approved 与去重后的问题列表。 |
| `ServiceStartupSafetyValidator.review(plan, preview)` | 逐项复核 plan/transaction/digest、稳定身份、源/目标、状态、影响、权限、备份和 executable；返回问题而非擅自修正计划。 |

### 两级确认 `confirmation.service_startup_actions`

| 类/方法 | 作用、输入、返回值与状态规则 |
|---|---|
| `ServiceStartupConfirmationTier` | 区分 `PLAN` 与更短时的 `RUNTIME` 确认。 |
| `ServiceStartupConfirmationState` | pending、approved、rejected、consumed、expired 的有限状态。 |
| `ServiceStartupActionConfirmation` | 一次不可变确认，绑定动作、事务/操作/计划/Preview、稳定身份、源/目标配置、状态/影响/权限、备份、对象摘要、父确认和到期时间。 |
| `ServiceStartupConfirmationError` | 未知、过期、重放、跨层或摘要漂移时抛出；没有“尽量继续”分支。 |
| `ServiceStartupActionConfirmationService.__init__(plan_ttl_seconds, runtime_ttl_seconds, now=...)` | 创建内存确认状态机并注入可测试时钟；TTL 非正或 runtime 大于 plan 时拒绝。持久化由 repository 单独负责。 |
| `request_plan(plan, preview)` | 要求 executable 且所有绑定一致，创建第一层 pending 确认。 |
| `resolve_plan(id, approved, plan, preview)` | 校验 pending、未过期和当前绑定后批准/拒绝，只能解析一次。 |
| `request_runtime(plan_confirmation_id, plan, preview)` | 要求父 PLAN 已批准且仍匹配，使用重新读取后的 Preview 创建子确认。 |
| `resolve_runtime(id, approved, plan, preview)` | 在短 TTL 内校验并批准/拒绝即时确认；任何对象证据变化都拒绝。 |
| `consume_runtime(id, plan, preview)` | 工具执行前一次性消费已批准 RUNTIME 确认，同时再核对父子与全部摘要；重放失败。 |
| `_create(tier, plan, preview, parent_confirmation_id, ttl)` | 生成 UUID、对象摘要、绑定摘要和起止时间的内部构造器。 |
| `_require_pending(id, tier)` | 读取指定层级确认并要求状态为 pending；未知 ID、错误层级或已处理状态抛确认异常。 |
| `_require_unexpired(request)` | 比较注入时钟；到期后拒绝，防止旧授权长期存活。 |
| `_require_executable(plan, preview)` | 要求 Preview executable，且 plan/transaction/动作/备份关系一致。 |
| `_require_same_binding(request, plan, preview)` | 比较计划、Preview、身份、源/目标、状态、影响、权限、备份和对象摘要；变化即拒绝。 |
| `_resolve(id, approved, tier, plan, preview)` | 两层共享的解析实现；先做状态/过期/绑定校验，再以不可变副本保存结论。 |

### 备份、事务与执行守卫 `persistence.service_startup_actions`

| 类/方法 | 作用、输入、返回值与副作用 |
|---|---|
| `ServiceStartupStoreError` | vault/repository 未初始化、记录缺失、非法状态、确认/参数不匹配和备份问题的失败关闭异常。 |
| `ServiceStartupBackupProtector.protect(plaintext)` | 协议：把 exact backup bytes 交给当前用户范围保护器；生产实现使用 Windows DPAPI。 |
| `ServiceStartupBackupProtector.unprotect(ciphertext)` | 协议：仅由同一用户上下文解密；失败必须向上抛出，不能使用明文退路。 |
| `ServiceStartupBase` | Stage 4C2 独立 SQLAlchemy 声明基类。 |
| `ServiceStartupBackupRow` | 加密备份表；保存 UUID、身份/payload 摘要、ciphertext 与时间，不与审计表混用。 |
| `ServiceStartupTransactionRow` | 事务表；保存计划/Preview/备份/确认/状态和脱敏结果。 |
| `ServiceStartupConfirmationRow` | 两级确认表；保存父子、摘要、状态、到期和消费证据。 |
| `ServiceStartupChangeRow` | 成功变更历史；保存原/写入配置、恢复来源和 restored 时间，不保存解密备份。 |
| `ServiceStartupBackupVault.__init__(database_path, protector)` | 创建专用 engine/session 与注入式保护器；尚未建表。 |
| `ServiceStartupBackupVault.initialize()` | 建立 backup 表并将 vault 标为可用。数据库失败向上抛出并阻止 Preview。 |
| `ServiceStartupBackupVault.store(payload)` | 序列化、计算摘要、加密并持久化；随后立即 `_load` 解密比较，只有完全相同才返回 verified reference。 |
| `ServiceStartupBackupVault.load(backup_id, expected_digest=...)` | 解密指定备份并要求 payload 摘要等于调用方绑定；损坏、替换或缺失均拒绝恢复。 |
| `ServiceStartupBackupVault._load(backup_id, expected_digest)` | 内部读取/解密/JSON/Pydantic 验证路径；把保护器或格式异常转换为安全 store error。 |
| `ServiceStartupBackupVault.close()` | 释放 vault engine；之后必须新建/初始化才能使用。 |
| `ServiceStartupBackupVault._require_initialized()` | 所有操作的内部前置条件；数据库损坏/未初始化不会被当作空备份。 |
| `ServiceStartupActionRepository.__init__(database_path)` | 创建事务数据库 engine/session factory；不执行迁移或恢复写操作。 |
| `ServiceStartupActionRepository.initialize()` | 建表；上次活动写标为 `INTERRUPTED`，未派发 pending 流程取消；返回中断 ID，绝不自动继续。 |
| `create(plan, preview)` | 在备份已存在后写入事务及全部摘要；重复 UUID 或不可执行 Preview 拒绝。 |
| `transition(transaction_id, state, error_code=None, error_message=None, result=None)` | 按白名单状态图原子前进；保存隐私安全错误/结果，非法跳转不修改数据库。 |
| `bind_runtime_preview(transaction_id, preview)` | 将新鲜 Preview 摘要写入同一事务；plan/identity/source/target/backup 不匹配时拒绝。 |
| `record_confirmation(value)` | 严格收窄为 Stage 4C2 确认并新增/更新绑定、结论和到期信息。 |
| `bind_confirmation(transaction_id, confirmation_id, runtime)` | 把正确层级 ID 绑定到事务；不能用另一计划的确认替换。 |
| `consume_confirmation_pair(runtime_confirmation_id)` | 在单一数据库事务中验证父 PLAN 与子 RUNTIME 均批准、未过期/未消费且摘要一致，然后一次性消费。 |
| `record_change(plan, result, restored_source_backup_id=None)` | 仅接受 verified、已派发且 runtime unchanged 的结果；原子新增 change history，并在 RESTORE 成功时标记来源记录已恢复。 |
| `mark_restored(backup_id)` | 将指定成功变更标记已恢复；重复或未知记录拒绝。正常恢复路径优先由 `record_change` 原子完成。 |
| `get_change(backup_id)` | 返回一条领域 change record；用于 prepare_restore，缺失则失败。 |
| `list_changes(limit=500)` | 按时间返回有界 Agent-owned 历史；非法上限拒绝，永不扫描系统服务配置。 |
| `get(transaction_id)` | 把 ORM 行严格转换为公开事务；缺失/损坏数据拒绝。 |
| `require_execution_authorization(manifest, arguments, authorization)` | 校验工具名、R2/确认要求、事务状态、计划/Preview/参数摘要、两个已消费确认和备份绑定；这是 registry 写守卫。 |
| `close()` | 释放 repository engine。 |
| `_require_initialized()` | 内部初始化断言，数据库不可用时禁止任何配置写。 |
| `ServiceStartupExecutionGuard.__init__(repository)` | 将 Stage 4C2 repository 注入 `ToolRegistry`。 |
| `ServiceStartupExecutionGuard.require(manifest, arguments, authorization)` | 将通用 write-guard 调用转发到 repository 的完整执行授权检查。 |
| `_transaction_from_row(row)` | 严格解析枚举、UUID、JSON 和 UTC 时间，构造不含密文的事务领域对象。 |
| `_change_from_row(row)` | 将 history ORM 行转换为稳定身份及原/写入配置的领域记录。 |
| `_as_utc(value)` | SQLite 返回 naive 时间时补 UTC；已有时区则规范化到 UTC。 |

### 平台协议与 Windows SCM 适配器

| 类/函数 | 作用、输入、返回值与平台副作用 |
|---|---|
| `ServiceStartupPlatform.evaluate_permissions(service_name)` | 协议：对精确 ServiceName 形成 query/change-config/提权证据，不改变配置。 |
| `ServiceStartupPlatform.set_automatic(request, cancellation, on_dispatched=None)` | 协议：只接受 SET_AUTOMATIC 严格请求，必须复验并返回 read-back 结果。 |
| `ServiceStartupPlatform.set_manual(...)` | 协议：只接受 SET_MANUAL 严格请求。 |
| `ServiceStartupPlatform.restore(...)` | 协议：只接受由已验证备份构造的 RESTORE 请求，且目标仍局限于 Automatic/Manual。 |
| `WindowsServiceStartupPlatform.evaluate_permissions(service_name)` | 分别尝试 query 与 `SERVICE_CHANGE_CONFIG` 句柄并检测当前进程 elevation；句柄立即关闭，不改 DACL。 |
| `WindowsServiceStartupPlatform.set_automatic(request, cancellation, on_dispatched=None)` | 校验动作/目标后调用 `_change`，raw start type 固定 `SERVICE_AUTO_START`。 |
| `WindowsServiceStartupPlatform.set_manual(...)` | 校验动作/目标后调用 `_change`，raw start type 固定 `SERVICE_DEMAND_START`。 |
| `WindowsServiceStartupPlatform.restore(...)` | 只把已验证的 Automatic/Manual 目标映射为上述两个常量；延迟或其他目标抛 `ValueError`。 |
| `WindowsServiceStartupPlatform._change(request, raw_target, cancellation, on_dispatched)` | 打开精确 handle，复验稳定身份、源配置、运行状态和影响；调用一次 `ChangeServiceConfig`，其余字段全部 no-change/null；随后回读配置与运行状态并返回 verified 结果。Access denied 转稳定错误且不提权。 |
| `_can_open_service(service_name, desired_access)` | 以给定最小权限尝试打开精确服务；access denied/不存在返回 `False`，其他 Windows 错误上抛，所有句柄 finally 关闭。 |
| `_cancelled_result(request, started, before=None)` | 取消发生在派发前时构造 change_dispatched=False 的真实 no-write 结果；源/目标都保持观测或预期源值。 |

### 命令对象和注册工具

| 类/函数 | 作用、输入、返回值与执行边界 |
|---|---|
| `ServiceStartupUndoRecord` | 条件 FULL 的回滚描述：原动作、备份、稳定身份、原/写入配置和有效条件；不是自动执行凭据。 |
| `ServiceStartupConfigurationCommand.__init__(platform, request, cancellation, on_dispatched=None)` | 将一个严格请求及窄平台封装成命令对象。 |
| `ServiceStartupConfigurationCommand.execute()` | 按 action 只分派到 platform 的 automatic/manual/restore 三方法之一，并缓存结果。 |
| `ServiceStartupConfigurationCommand.verify()` | 仅当已有结果 verified、runtime unchanged 且 after 精确等于目标时为真；未执行返回假。 |
| `ServiceStartupConfigurationCommand.build_undo_record()` | 只有 `verify()` 为真才构造条件回滚记录，否则抛异常，避免为失败写入虚构 FULL。 |
| `ServiceStartupConfigurationCommand.rollback(authorized_restore_request, cancellation)` | 只接受 independently authorized 的 RESTORE 严格请求；校验稳定身份及精确反向源/目标后调用 platform.restore，不复用原确认。 |
| `SetServiceAutomaticTool.__init__(platform, backups)` | 注入窄平台和备份 vault。 |
| `SetServiceAutomaticTool.manifest` | 返回 `system.service.startup.set_automatic` 的 R2、FULL conditional、两级确认、单对象 Windows-only 清单。 |
| `SetServiceAutomaticTool.execute(request, cancellation)` | 收窄 Pydantic 请求、验证动作和备份绑定，再运行命令；没有 fallback。 |
| `SetServiceManualTool.__init__(platform, backups)` / `manifest` / `execute(...)` | 与 Automatic 工具相同，但仅允许 `SET_MANUAL` 和固定工具名。 |
| `RestoreServiceStartupTool.__init__(platform, backups)` / `manifest` / `execute(...)` | 仅允许 RESTORE；同样重新验证 backup ID/digest/identity/source，不能接受外部任意“原值”。 |
| `_require_backup(vault, request)` | 解密并验证备份，要求身份与 expected source configuration 精确匹配；失败时工具不调用平台。 |
| `_manifest(name, description)` | 为三个工具生成共同窄清单：R2、非只读、非幂等、可取消派发前、FULL、两确认、timeout、batch=1 和 Windows 平台。 |

### 编排 `orchestration.service_startup_actions`

| 方法/函数 | 作用、输入、返回值与状态变化 |
|---|---|
| `ServiceStartupActionService.__init__(...)` | 注入 Stage 4C1 只读平台/解析器、Stage 4C2 平台、策略、Preview、复核、确认、vault、repository、registry 和 audit；不创建隐藏全局状态。 |
| `list_changes()` | 返回本 Agent 的有界成功变更历史，供恢复 UI；不枚举或修改系统。 |
| `prepare_change(user_goal, service_name, action)` | 仅接受 SET_AUTOMATIC/SET_MANUAL；精确重读、先做资格和普通权限检查、创建并验证备份，再构造计划/Preview/复核/事务/审计。没有写 SCM。 |
| `prepare_restore(user_goal, backup_id)` | 读取 Agent-owned history 与备份，要求未恢复、稳定身份未变、当前配置仍等于 Agent-written 值、运行/依赖/权限可用；创建新的反向备份和完整 RESTORE Preview。 |
| `request_plan_confirmation(plan, preview)` | 创建 PLAN 确认并持久化，将事务推进到等待确认。 |
| `resolve_plan_confirmation(id, approved, plan, preview)` | 解析并持久化第一层结论；拒绝进入 BLOCKED/取消终态，批准后绑定事务并等待 runtime 确认。 |
| `request_runtime_confirmation(plan_confirmation_id, plan)` | 全量重读和 `_revalidate`，保存新 Preview，再创建/持久化子确认。 |
| `resolve_runtime_confirmation(id, approved, plan, preview)` | 解析并持久化即时结论；批准后把事务推进 CONFIRMED，拒绝不执行。 |
| `execute(plan_confirmation_id, runtime_confirmation_id, plan, preview, cancellation=None)` | 消费内存和 SQLite 确认，审计开始，最后一次复验，进入写前 EXECUTING，调用唯一注册工具，进入 VERIFYING；仅 verified+runtime unchanged 才记 change/completed。错误按“是否可能已派发”如实记录且不重试。 |
| `_prepare(user_goal, action, observation, target, restore_source_backup_id=None)` | change/restore 共用只读准备路径；在 eligibility/permission 后创建备份、影响和计划，再审查、建事务和审计。 |
| `_build_plan(...)` | 构造绑定 identity/source/target/state/impact/permission/backup 的 immutable R2 计划。 |
| `_create_backup(observation)` | 构造 exact payload，交 vault 加密存储，并要求返回 verified reference。 |
| `_revalidate(plan)` | 重新解析精确服务并比较稳定身份、配置、运行状态、影响、权限和备份；构建新 Preview 并独立复核。 |
| `_resolve_exact_identity(service_name)` | 通过 Stage 4C1 resolver 按 exact ServiceName 新鲜读取；不存在或名称变化失败关闭。 |
| `_require_eligible(observation, action, target)` | 执行 Stage 4C2 policy 并将首个 reason code 转成工作流异常；在资格不明时不创建备份。 |
| `_require_permissions(permissions)` | 阻止提权进程和缺少 query/change-config 的普通进程；权限不足发生在备份与确认之前。 |
| `_block(plan, message)` | 尽力将已有事务转 BLOCKED 后抛确认/配置异常；状态记录失败不会变成允许。 |
| `_arguments(plan)` | 从计划生成唯一 `ServiceStartupActionRequest` 的 JSON 参数，供 registry 校验和摘要。 |
| `_target_for_action(action)` | SET_AUTOMATIC 映射非延迟 Automatic、SET_MANUAL 映射 Manual；RESTORE 必须由备份显式提供目标，直接调用会拒绝。 |
| `_tool_name(action)` | 把三个有限动作映射到三个已注册工具名；无动态工具拼接。 |

### 审计 `audit.service_startup_actions`

| 方法/函数 | 作用与隐私边界 |
|---|---|
| `ServiceStartupActionAuditLogger.__init__(repository, app_version, git_commit)` | 注入共享结构化审计仓库和版本证据。 |
| `previewed(plan, preview, review)` | 记录动作、ServiceName、风险、ALLOW/BLOCK、摘要、备份验证和影响计数；不记录 binary path、命令、密码或密文。 |
| `confirmation_resolved(plan, confirmation)` | 记录确认层级、结果、父子/过期/绑定摘要。 |
| `started(plan, preview)` | 在真实写入前记录开始证据；审计不可用时编排拒绝继续。 |
| `completed(plan, result)` | 记录派发、verified、runtime unchanged 和 before/after 枚举，不保存服务内容。 |
| `failed(plan, phase, code, message, mutation_may_have_started)` | 记录固定阶段、错误码、脱敏消息及是否可能已派发；用于区分安全拒绝和需要人工检查的不确定结果。 |
| `_tool_name(plan)` | 从有限 action 返回准确工具名供审计；无任意字符串输入。 |

### 运行时、GUI 和 Qt workers

| 类/方法/函数 | 作用、线程和副作用 |
|---|---|
| `ServiceStartupActionServices` | runtime bundle，包含 registry、窄平台、精确 resolver 和应用服务。 |
| `ApplicationRuntime.create_service_startup_action_services()` | 组合 Stage 4C1 base policy、Stage 4C2 policy/validator/vault/repository/audit/guard，注册恰好三个工具并返回 bundle。 |
| `ApplicationRuntime.close()`（Stage 4C2 增量） | 在 worker 停止后先关闭 service-startup vault/repository，再关闭既有资源。 |
| `ServiceStartupWorkerSignals` | worker 的 `completed(object)` 与 `failed(str)` 终态信号。 |
| `PreparedServiceStartupAction` | prepare worker 输出：services、plan、Preview、review。 |
| `RuntimeServiceStartupPreview` | runtime worker 输出：新鲜 Preview 和 pending 即时确认。 |
| `ServiceStartupPrepareWorker.__init__(runtime, action, service_name=None, backup_id=None, user_goal=...)` | 保存互斥的变更或恢复请求；GUI 线程不查询/写 SCM。 |
| `ServiceStartupPrepareWorker.run()` | worker 线程初始化 COM，构造 services，调用 prepare_change 或 prepare_restore，发终态信号并释放 COM。 |
| `ServiceStartupRuntimePreviewWorker.__init__(service, plan_confirmation_id, plan)` | 保存同一确认服务实例、父确认和计划。 |
| `ServiceStartupRuntimePreviewWorker.run()` | worker 中全量 revalidation 并请求即时确认；不写 SCM。 |
| `ServiceStartupExecutionWorker.__init__(service, plan_confirmation_id, runtime_confirmation_id, plan, preview)` | 保存一次执行所需绑定并创建 cancellation token。 |
| `ServiceStartupExecutionWorker.cancel()` | 仅请求在 `ChangeServiceConfig` 派发前取消；已派发后不能撤销系统调用。 |
| `ServiceStartupExecutionWorker.run()` | worker 中消费确认并执行/验证，安全发出结果或错误。 |
| `ServiceStartupHistoryWorker.__init__(runtime)` | 保存 runtime，不在 GUI 线程访问 SQLite。 |
| `ServiceStartupHistoryWorker.run()` | worker 中读取 Agent-owned change history 并发出 tuple。 |
| `require_prepared_service_startup(value)` | 收窄 Qt object 为 `PreparedServiceStartupAction`，类型不符抛 `TypeError`。 |
| `require_runtime_service_startup(value)` | 收窄为 `RuntimeServiceStartupPreview`。 |
| `require_service_startup_result(value)` | 收窄为 `ServiceStartupMutationResult`。 |
| `require_service_startup_history(value)` | 要求 tuple 且每项为 `ServiceStartupChangeRecord`。 |
| `_emit_completed(signals, value)` | 发成功信号；只忽略应用安全关闭后 QObject 已删除造成的 Qt `RuntimeError`。 |
| `_emit_failed(signals, exc)` | 格式化异常类型/消息并发失败信号；同样只处理 QObject 删除竞态。 |
| `ServiceStartupActionDialog.__init__(runtime, action, service_name=None, backup_id=None, display_name=None, parent=None)` | 创建单对象分阶段对话框并立即启动只读 prepare；变更用 service_name，恢复用 backup_id。 |
| `ServiceStartupActionDialog._build_ui()` | 创建详情、状态、PLAN/RUNTIME/关闭按钮；没有直接工具调用。 |
| `_prepare()` | 禁用推进按钮并启动后台 prepare worker。 |
| `_prepared(value)` | 类型收窄并展示身份、源/目标、运行状态、权限、备份、风险和 review；不可执行时不开放确认。 |
| `_advance()` | 仅按当前阶段调用计划或即时确认，防止跳级。 |
| `_approve_plan()` | 显示对象具体确认框，批准第一层并启动 runtime worker。 |
| `_runtime_ready(value)` | 展示新鲜证据与短时确认；不自动接受。 |
| `_approve_runtime()` | 再次显示具体 source->target 和条件 FULL，批准后启动 execution worker。 |
| `_executed(value)` | 显示 read-back before/after、runtime unchanged、verified 和恢复提示；不把失败说成成功。 |
| `_failed(message)` | 显示友好错误并禁用写入口。 |
| `shutdown()` | 请求尚未派发的 execution 取消。 |
| `_replace_cancel_callback(callback)` | 断开旧连接并绑定当前阶段唯一取消/关闭行为，避免重复触发。 |
| `closeEvent(event)` | 关闭前调用 shutdown，再交 Qt 处理。 |
| `ServiceStartupHistoryDialog.__init__(runtime, parent=None)` | 创建 Agent-owned 可恢复历史窗口并后台加载。 |
| `ServiceStartupHistoryDialog._load()` | 启动 history worker。 |
| `_loaded(value)` | 类型收窄并填充 display/config/time 表；已恢复项不再提供相同 restore 入口。 |
| `_failed(message)` | 显示历史加载错误。 |
| `_selected()` | 返回所选领域记录而非从表格文本重建 backup ID。 |
| `_request_restore()` | 发出选中 backup UUID/display name 给管理页，仍不直接恢复。 |
| `_preview_html(prepared)` | HTML 转义显示第一份 Preview 与 review。 |
| `_runtime_html(value)` | HTML 转义显示新鲜 runtime Preview 和确认到期信息。 |
| `_result_html(result)` | HTML 转义显示变更派发、验证、配置与运行状态证据。 |
| `ServiceManagementTab._set_startup_actions(automatic, manual)` | 集中启用/禁用两种配置 Preview 按钮。 |
| `ServiceManagementTab._open_startup_action(action)` | 仅从选中 `ServiceInventoryItem` 取 exact ServiceName；先检查本地能力，再打开相同工作流对话框。 |
| `ServiceManagementTab._open_restore_history()` | 打开 Agent-owned history，连接 restore 请求。 |
| `ServiceManagementTab._open_restore_action(backup_id, display_name)` | 严格要求 UUID 后创建 RESTORE 对话框；不接受任意配置值。 |
| `_startup_capabilities(item)` | 纯 UI 预筛选：仅 Stage 4C1-safe、无依赖、非 delayed 且当前 Automatic/Manual 时返回相反方向能力；后端仍会全量重验。 |
| `_startup_management_text(item)` | 把 capability/Delayed/Disabled/其他阻止状态转成表格说明；不授权执行。 |

### Stage 4C1 稳定身份兼容修订

| 类/方法 | Stage 4C2 后的精确定义 |
|---|---|
| `ServiceStartupType` | 规范化 SCM 启动类型：Automatic、Automatic Delayed、Manual、Disabled、Boot、System、Unknown。 |
| `ServiceStartupConfiguration` | 只含可变 `startup_type` 与 `delayed_auto_start`，与稳定身份分离。 |
| `ServiceStartupConfiguration.canonical_digest()` | 对两个启动配置证据生成独立摘要。 |
| `ServiceStableIdentity` | 只含 ServiceName、service type、原始 binary 配置指纹与 service account；display/startup/runtime 不属于稳定身份。 |
| `ServiceStableIdentity.canonical_digest()` | 规范化 name/account 并摘要稳定字段。`ServiceIdentity` 是保留给 Stage 4C1 import 的兼容别名。 |
| `ServiceObservation.state_digest()` | 现在同时绑定稳定身份摘要、启动配置摘要、状态和 accepted controls。 |
| `ServiceObservation.configuration_digest()` | 单独返回启动配置摘要，供 Stage 4C1/4C2 TOCTOU 复验。 |
| `ServiceControlPlatform.start/stop(...)` | 新增 `expected_startup_configuration_digest`，状态控制前同时复验稳定身份与启动配置，防止 Stage 4C2 或外部修改后复用旧确认。 |

## Stage 4C1 Windows 服务安全启停 API

本节覆盖 Stage 4C1 新增或修改的每个生产函数/方法。`ServiceName` 是执行身份；
`DisplayName` 只用于展示和唯一精确匹配。除特别注明外，所有模型都是不可变 Pydantic
模型，校验失败抛出 `ValidationError`，所有真实写入均要求普通用户权限、写前事务、
双重确认和注册工具授权。

### 领域模型 `domain.service_actions`

| 类/函数 | 作用、输入、返回值、异常与安全约束 |
|---|---|
| `ServiceActionType` | 有限动作枚举：`START`、`STOP`、`RESTART`；不允许模型构造其他动作。 |
| `ServiceStepType` | 唯一平台写步骤 `START`/`STOP`。Restart 只能由这两个步骤编排。 |
| `ServiceState` | SCM 状态枚举，包含稳定、pending、paused 与 unknown 状态。 |
| `ServiceState.is_pending` | 无参数属性；pending 四种过渡态返回 `True`，用于禁止冲突控制。无副作用。 |
| `ServiceSafetyClass` | 服务安全分类：当前用户第三方、系统、驱动、安全、网络、登录、存储、更新、Agent、企业和未知等。 |
| `ServiceSafetyDecision` | 确定性最终结论 `ALLOW`/`BLOCK`；LLM 不能覆盖。 |
| `ServiceErrorCode` | UI/审计可稳定匹配的隐私安全错误码；不嵌入二进制路径或凭据。 |
| `ServiceTransactionState` | 写前事务状态机；区分计划、两次确认、每个控制/等待步骤、完成、部分完成、失败、取消和中断。 |
| `ServiceStableIdentity` / `ServiceIdentity` | 绑定 ServiceName、服务类型、二进制路径指纹和运行账户；`ServiceIdentity` 是兼容别名。显示名和可变启动配置不属于稳定身份。 |
| `ServiceStableIdentity.canonical_digest()` | 规范化名称/账户并对稳定字段做 SHA-256；返回十六进制摘要，用于 TOCTOU 复验。无系统读取。 |
| `ServiceStartupType` / `ServiceStartupConfiguration` | 规范化启动类型及 delayed-auto 证据；配置与稳定身份分离，以便状态控制检测配置漂移、配置工具表达有意变更。 |
| `ServiceStartupConfiguration.canonical_digest()` | 对启动类型和 delayed 标记生成独立 SHA-256。 |
| `ServiceRelation` | 一条依赖或被依赖关系，含精确 ServiceName、显示名和当前状态。 |
| `ServicePermissionEvidence` | 记录 query/start/stop/enumerate-dependents 句柄探测结论、是否提权和采集时间。 |
| `ServicePermissionEvidence.allows(action)` | 按动作判断最小权限是否齐全；提权进程或缺少 query 立即返回 `False`，Restart 必须同时具备三类控制/枚举权限。 |
| `ServicePermissionEvidence.canonical_digest()` | 对稳定权限布尔值做摘要，排除采集时间；用于确认后的权限漂移检测。 |
| `ServiceObservation` | 一次新鲜 SCM 观察：稳定身份、显示名、独立启动配置、状态、可接受控制、PID、可选二进制/发布者/描述及关系图。 |
| `ServiceObservation.state_digest()` | 将稳定身份摘要、启动配置摘要、状态和可接受控制绑定为 SHA-256，避免旧状态确认被复用。 |
| `ServiceObservation.configuration_digest()` | 返回独立启动配置摘要，供执行前复验。 |
| `ServiceObservation.dependency_digest()` | 先按 ServiceName 排序依赖/被依赖项，再摘要名称与状态；用于阻止关系图漂移。 |
| `ServiceDependencyAssessment` | 依赖分析结果，含阻止的依赖/被依赖项、图摘要和解释。 |
| `ServiceSafetyAssessment` | 确定性分类结果，含身份摘要、分类、ALLOW/BLOCK、错误码和用户解释。 |
| `ServiceActionPlan` | 单对象不可变计划，绑定三类预期摘要、精确步骤、风险、确认要求和 MANUAL 回滚。 |
| `ServiceActionPlan.validate_contract()` | Pydantic 后校验：START/STOP 只能一个对应步骤且为 R2；RESTART 必须 STOP→START 且为 R2_HIGH_IMPACT；两次确认必须开启；回滚必须 MANUAL。违反即拒绝建模。 |
| `ServiceActionPlan.canonical_digest()` | 对全部执行相关计划字段做 SHA-256，计划任何变化都会改变确认绑定。 |
| `ServiceActionPreview` | 组合观察、策略、依赖、权限、风险和回滚的只读 Preview。 |
| `ServiceActionPreview.executable` | 仅当策略允许、依赖允许、权限齐全、非 pending 且状态摘要自洽时返回 `True`。 |
| `ServiceActionPreview.canonical_digest()` | 摘要完整 Preview，绑定两级确认。 |
| `ServiceStepRequest` | 注册工具的严格输入：事务、步骤、配置身份/摘要、预期状态、5–120 秒超时。 |
| `ServiceStepResult` | 单步结果：前后状态、是否已派发、是否验证、消息和起止时间。 |
| `ServiceActionResult` | 完整动作结果；明确完成/部分完成/no-op、最终状态和每步证据。 |
| `ServiceActionTransaction` | SQLite 行的领域投影，含绑定摘要、确认 ID、顺序步骤、当前索引、结果和错误。 |
| `ServiceInventoryItem` | GUI 清单项：新鲜观察、分类、三个动作是否可用以及解释。 |
| `canonical_binary_fingerprint(raw_binary_path)` | 接受并逐字绑定 SCM 原始二进制配置字符串，使用 UTF-8/surrogatepass 后 SHA-256。返回不可逆指纹，不展开、不执行路径。 |
| `canonical_path(path)` | `Path.resolve(strict=False)` 后按 Windows 大小写规则规范化；仅供摘要/比较，不授予访问权限。 |
| `_digest(payload)` | 将对象以排序、紧凑 JSON 编码后 SHA-256；内部统一生成稳定绑定摘要。 |

### 领域异常 `domain.service_errors`

| 类/方法 | 作用 |
|---|---|
| `ServiceActionError.__init__(code, message)` | 构造带稳定 `ServiceErrorCode` 的工作流异常；消息供 UI，错误码供状态机/审计。 |
| `ServiceConfigurationChangedError.__init__(message=...)` | 固定为 `SERVICE_CONFIGURATION_CHANGED`，用于身份字段与批准 Preview 不一致。 |
| `ServicePermissionError.__init__(message=...)` | 固定为 `PRIVILEGE_REQUIRED`，权限不足时失败关闭且不触发提权。 |

### 目标解析、计划和依赖

| 类/函数 | 作用、输入、返回值与拒绝条件 |
|---|---|
| `ServiceTargetResolver.__init__(platform, max_items=5000)` | 注入平台和有界清单上限；上限小于 1 抛 `ValueError`。 |
| `ServiceTargetResolver.list_current()` | 每次调用平台重新枚举，返回不超过上限的观察；不使用缓存身份执行。 |
| `ServiceTargetResolver.resolve_query(query)` | 先精确大小写无关匹配 ServiceName，再允许唯一精确 DisplayName；空值、无匹配、多 DisplayName 分别拒绝；不做模糊/部分匹配。 |
| `ServiceTargetResolver.resolve_name(service_name)` | 执行前通过精确 ServiceName 重读；不存在或平台返回不同身份即抛 `ServiceActionError`。 |
| `ServiceActionPlanCompiler.compile(user_goal, target_query, action, observation, permissions)` | 从本地可信观察生成单对象计划；绑定状态/关系/权限摘要，Restart 固定 STOP→START，风险固定；不接受模型提供的身份。 |
| `service_action_intent(text)` | 从有限中英文动词识别 START/STOP/RESTART，无法识别返回 `None`；不执行。 |
| `service_target_query(text)` | 删除有限动作/礼貌词并提取一个目标提示；批量词、代词或空目标抛 `ValueError`，阻止“全部服务”。 |
| `ServiceDependencyAnalyzer.assess(observation, action)` | Start/Restart 要求全部依赖 RUNNING；Stop/Restart 要求全部被依赖项 STOPPED；返回图摘要与阻止项，绝不级联控制。 |

### 安全策略、Preview 与独立复核

| 类/函数 | 作用、输入、返回值与安全约束 |
|---|---|
| `ServiceSafetyPolicy.__init__(current_username, agent_root, windows_directory)` | 固化当前用户和保护根目录；随后所有判断默认拒绝。 |
| `ServiceSafetyPolicy.assess(observation, action)` | 依次检查驱动/共享/交互类型，系统/安全/网络/登录/存储/更新/企业/Agent 标记，路径、运行账户、签名发布者、pending、可接受控制和禁用启动类型；仅窄当前用户第三方服务返回 ALLOW。 |
| `_normalize_account(value)` | 去除空白和 `.\\` 前缀并 casefold，供账户比较；不解析凭据。 |
| `_same_account(candidate, current)` | 接受限定名或当前用户名短名；系统账户不会等于当前普通用户。 |
| `_is_within(path, root)` | 用 `Path.relative_to` 判断路径是否在保护根内；不访问文件内容。 |
| `_explain(reason)` | 将策略错误码映射为初级用户可理解的固定解释；无模型生成。 |
| `ServicePreviewEngine.__init__(policy, dependency_analyzer)` | 注入两个相互独立的确定性检查器。 |
| `ServicePreviewEngine.build(plan, observation, permissions)` | 验证计划绑定后组合策略/依赖/权限为 Preview；不执行 SCM 控制。 |
| `ServiceSafetyReview` | 独立复核输出：是否批准、问题列表和复核时间。 |
| `ServiceActionSafetyValidator.__init__(registry)` | 注入注册表，避免审查不存在的工具。 |
| `ServiceActionSafetyValidator.review(plan, preview)` | 检查计划/Preview 摘要、风险、回滚、步骤对应的已注册工具清单、权限、策略和依赖；任一问题返回不批准。 |

### 两级确认 `confirmation.service_actions`

| 类/方法 | 作用、输入、返回值与状态规则 |
|---|---|
| `ServiceConfirmationTier` | 区分 `PLAN` 与短时 `RUNTIME`。 |
| `ServiceConfirmationState` | 区分 pending、approved、rejected、consumed、expired。 |
| `ServiceActionConfirmation` | 不可变确认凭据，绑定 plan/preview/identity/state/dependency/permission/对象摘要、父确认和过期时间。 |
| `ServiceConfirmationError` | 过期、重放、绑定漂移或非法状态统一抛出的拒绝异常。 |
| `ServiceActionConfirmationService.__init__(plan_ttl_seconds, runtime_ttl_seconds, now=...)` | 设置两个有限 TTL 和可测试时钟；非法 TTL 拒绝。确认存于实例内存，持久化由 repository 负责。 |
| `request_plan(plan, preview)` | 要求 Preview 可执行且与计划一致，创建第一层 pending 凭据。 |
| `resolve_plan(id, approved, plan, preview)` | 复核全部摘要和有效期后批准/拒绝；只能解析一次。 |
| `request_runtime(plan_confirmation_id, plan, preview)` | 要求父计划确认已批准且仍匹配，创建更短期第二层凭据。 |
| `resolve_runtime(id, approved, plan, preview)` | 解析即时确认；任何 Preview 漂移均拒绝。 |
| `consume_runtime(id, plan, preview)` | 执行入口一次性消费已批准即时确认；重放抛 `ServiceConfirmationError`。 |
| `_create(tier, plan, preview, parent_confirmation_id, ttl)` | 生成绑定摘要、对象摘要、时间和 UUID 的内部构造器。 |
| `_resolve(id, approved, tier, plan, preview)` | 内部公共解析路径；校验 tier、pending 状态、过期和所有绑定后替换状态。 |
| `_get(id)` | 从内存取凭据；空/未知 ID 失败关闭。 |
| `_require_not_expired(request)` | 超时则标记/拒绝，确保旧确认不再使用。 |
| `_require_executable(plan, preview)` | 要求 Preview 可执行、计划 ID/摘要/事务/动作一致。 |
| `_require_current(request, plan, preview)` | 对比当前所有摘要和对象摘要；任一差异视为计划变化。 |

### 事务持久化与执行授权 `persistence.service_actions`

| 类/方法 | 作用、输入、返回值与副作用 |
|---|---|
| `ServiceActionStoreError` | 数据库未初始化、事务非法、步骤越序或确认不匹配时抛出。 |
| `ServiceActionBase` | Stage 4C1 SQLAlchemy 声明基类。 |
| `ServiceActionTransactionRow` | 内部事务表映射；保存摘要、顺序步骤、状态和脱敏结果 JSON。 |
| `ServiceActionConfirmationRow` | 内部确认表映射；保存两个确认层的绑定和消费状态。 |
| `ServiceActionRepository.__init__(database_path)` | 创建 SQLite engine/session factory，不执行迁移。路径来自应用数据配置。 |
| `initialize()` | 建表；将上次活动事务标记 `INTERRUPTED`、未执行 pending 标记取消；返回中断事务 ID，不自动继续。 |
| `create(plan, preview, steps)` | 写入 Preview 后的事务及每个工具/参数摘要；重复 ID 或非 PREVIEW 状态拒绝。 |
| `transition(transaction_id, state, error_code=None, error_message=None, result=None)` | 按白名单状态图原子更新；非法跳转拒绝，错误和结果以脱敏 JSON 保存。 |
| `bind_runtime_preview(transaction_id, preview)` | 保存即时重验 Preview 摘要，且必须对应同一计划/事务。 |
| `record_confirmation(value)` | 新增或更新确认行，持久化绑定、结论和消费时间；不存服务二进制路径。 |
| `bind_confirmation(transaction_id, confirmation_id, runtime)` | 将正确层级确认 ID 绑定事务，禁止跨事务替换。 |
| `consume_confirmation_pair(plan_confirmation_id, runtime_confirmation)` | 单事务内原子验证父子确认均批准、未消费、未过期且摘要匹配，再标记消费。 |
| `begin_step(transaction_id, index, executing_state)` | 写前声明当前有且只有一个顺序步骤正在执行；越序或已有步骤进行中拒绝。 |
| `mark_dispatched(transaction_id, wait_state)` | SCM 控制返回前立刻记录已派发边界和 WAITING 状态；仅活动步骤可调用。 |
| `complete_step(transaction_id, index, state)` | 验证索引后递增下一步，清除 in-progress，并转入 STOP_COMPLETED/COMPLETED/失败状态。 |
| `get(transaction_id)` | 读取并转换一个完整领域事务；不存在抛 store error。 |
| `record_terminal_result(transaction_id, result)` | 只更新终态结果 JSON；用于 verified=False 等无异常终止。 |
| `close()` | 释放 SQLAlchemy engine；之后不得继续执行服务事务。 |
| `_require_initialized()` | 内部前置检查，未初始化时失败关闭。 |
| `ServiceExecutionGuard.__init__(repository)` | 注入持久化证据，作为 ToolRegistry 写守卫。 |
| `ServiceExecutionGuard.require(manifest, arguments, authorization)` | 校验事务/操作/计划/Preview/工具/参数摘要、确认消费、当前步骤与风险；不匹配时工具不会获得调用。 |
| `_from_row(row)` | 将 ORM 行和 JSON 字段严格还原成 `ServiceActionTransaction`。 |

### 平台协议与 Windows SCM 实现

| 类/函数 | 作用、输入、返回值与平台副作用 |
|---|---|
| `ServiceControlPlatform.list_services(max_items=5000)` | 协议：有界枚举观察。实现必须只读。 |
| `ServiceControlPlatform.inspect(service_name)` | 协议：按精确 ServiceName 读取；不存在返回 `None`。 |
| `ServiceControlPlatform.evaluate_permissions(service_name, action)` | 协议：只通过最小权限句柄打开来形成证据，不执行控制。 |
| `ServiceControlPlatform.start(identity, expected_startup_configuration_digest, expected_state, timeout_seconds, cancellation, on_dispatched=None)` | 协议：精确稳定身份和独立启动配置复验后开始并等待 RUNNING；回调记录派发边界。 |
| `ServiceControlPlatform.stop(...)` | 协议：同样复验稳定身份和启动配置后发送 STOP 并等待 STOPPED；不终止 PID、不级联。 |
| `WindowsServiceControlPlatform.list_services(max_items=5000)` | 用 query-only SCM handle 枚举并逐项观察；权限/删除竞态造成的单项失败被安全跳过，上限受控。 |
| `WindowsServiceControlPlatform.inspect(service_name)` | 以 query 权限打开精确服务并读取配置、状态、依赖、发布者证据；无修改。 |
| `WindowsServiceControlPlatform.evaluate_permissions(service_name, action)` | 分别尝试 query/start/stop/enumerate-dependent 权限句柄并检测 token 是否提权；返回布尔证据，句柄立即关闭。 |
| `WindowsServiceControlPlatform.start(...)` | 调用共同 `_control`，仅允许目标 RUNNING，使用 `StartService`。 |
| `WindowsServiceControlPlatform.stop(...)` | 调用共同 `_control`，仅允许目标 STOPPED，使用 `ControlService(SERVICE_CONTROL_STOP)`。 |
| `WindowsServiceControlPlatform._inspect_with_scm(scm, service_name)` | 复用已打开 SCM 的精确读取；服务消失返回 `None`，其他错误上抛。 |
| `WindowsServiceControlPlatform._control(step, identity, expected_state, target_state, timeout, cancellation, on_dispatched)` | 用所需单一控制权限重新打开，比较完整配置摘要和预期状态；目标已达到时返回 verified no-op；否则派发、回调、有限轮询并返回证据。身份/状态变化、取消和 API 失败均停止。 |
| `current_windows_username()` | 读取 `GetUserNameEx(NameSamCompatible)` 风格当前账户，供策略固定当前用户；不读取密码/token 内容。 |
| `_observation_from_handle(scm, handle, service_name)` | 从 query handle 汇总 `QueryServiceConfig`、`QueryServiceStatusEx`、描述、依赖/被依赖、可执行路径、公司名和 Authenticode 结果。 |
| `_query_relation(scm, service_name)` | 只读打开一个关系节点并返回名称/显示名/状态；关系消失则让上层重新 Preview。 |
| `_wait_for_state(handle, target_state, timeout_seconds, cancellation)` | 使用 monotonic 截止时间、checkpoint 与 wait hint 有界轮询；取消只停止等待/未来步骤，不撤销已派发控制；超时返回未验证状态或抛稳定错误。 |
| `_can_open_service(service_name, desired_access)` | 尝试最小 desired access 并立即关闭；access denied 返回 `False`，非权限类异常上抛。 |
| `_process_is_elevated()` | 读取当前进程 token elevation；任何检测失败按安全错误处理，策略不借机提权。 |
| `_state(value)` | 将 Win32 SERVICE_* 数值映射为 `ServiceState`，未知值映射 UNKNOWN。 |
| `_startup_type(value, delayed_auto_start)` | 将 SCM start type 与只读 delayed 标记组合为规范 `ServiceStartupType`；Automatic+delayed 明确映射为 `AUTOMATIC_DELAYED`，未知数值为 `UNKNOWN`。 |
| `_extract_executable_path(raw)` | 从服务二进制字符串安全提取可执行文件部分并展开环境变量；不执行命令或参数。无法可靠解析返回 `None`。 |
| `_publisher(path)` | 读取 Windows 版本资源 CompanyName，仅作显示/辅助分类；不替代 Authenticode 布尔验证。 |
| `_GUID`、`_WinTrustFileInfo`、`_WinTrustUnion`、`_WinTrustData` | `WinVerifyTrust` 所需固定 ctypes 结构；没有通用 native-call 接口。 |
| `_authenticode_signature_valid(path)` | 通过 `WinVerifyTrust` 验证文件签名并关闭状态数据；返回布尔值，不联网下载或执行文件。 |

### 注册工具 `tools.system_tools.service_actions`

| 类/函数 | 作用、输入、返回值与执行边界 |
|---|---|
| `StartServiceTool.__init__(platform, on_dispatched=None)` | 注入唯一平台和可选写前派发回调。 |
| `StartServiceTool.manifest` | 返回 `system.service.start` R2、MANUAL、单对象、需两次确认、Windows-only 清单。 |
| `StartServiceTool.execute(request, cancellation)` | 收窄为 `ServiceStepRequest`，要求 step=START，通知派发边界后调用平台 start；身份/权限验证仍由平台执行。 |
| `StartServiceTool._notify(request)` | 将严格请求传给 repository 回调，不接受任意参数。 |
| `StopServiceTool.__init__(platform, on_dispatched=None)` | 注入 stop 平台和派发回调。 |
| `StopServiceTool.manifest` | 返回 `system.service.stop` R2、MANUAL、单对象、需两次确认清单。 |
| `StopServiceTool.execute(request, cancellation)` | 要求 step=STOP 后调用平台 stop；没有 terminate-process 或 cascade fallback。 |
| `StopServiceTool._notify(request)` | 将严格请求传给事务派发回调。 |
| `_manifest(name, description, risk)` | 构造两个工具共享的窄清单；固定 schema、权限、超时、批量 1、审计字段和 Windows 平台。 |

### 应用服务 `orchestration.service_actions`

| 方法/函数 | 作用、输入、返回值与副作用 |
|---|---|
| `ServiceActionService.__init__(...)` | 注入平台、解析、编译、策略、Preview、复核、确认、仓库、注册表和审计；超时非正数拒绝。 |
| `list_current()` | 新鲜枚举并为每项计算三种权限/策略可用性；只读但可能较慢，应在 worker 运行。 |
| `prepare(user_goal, target_query, action)` | 本地解析、权限探测、编译、Preview、独立复核、持久化写前步骤和审计；返回 plan/preview/review，不执行控制。 |
| `request_plan_confirmation(plan, preview)` | 创建并持久化第一层确认，绑定事务。 |
| `resolve_plan_confirmation(id, approved, plan, preview)` | 解析并审计；批准进入 runtime gate，拒绝进入 CANCELLED。 |
| `request_runtime_confirmation(plan_confirmation_id, plan)` | 重读身份/状态/关系/权限并复核；变化即 BLOCKED，否则保存新 Preview 并签发短时确认。 |
| `resolve_runtime_confirmation(id, approved, plan, preview)` | 解析第二层；批准进入 CONFIRMED，拒绝取消。 |
| `execute(plan_confirmation_id, runtime_confirmation_id, plan, preview, cancellation=None)` | 一次性消费确认、再次全量复验、先写 mandatory audit，再按顺序调用注册工具；返回 verified 最终/部分结果。 |
| `_execute_steps(plan, preview, runtime_confirmation_id, cancellation)` | 严格顺序执行步骤并写入 begin/dispatched/complete 状态；Stop 失败不 Start；Stop 后取消返回部分完成；异常记录真实最终状态。 |
| `_cancelled_result(plan, results, final_state, started)` | 构造取消或 Restart Stop 后部分完成结果，设置相应错误码并审计；不做反向控制。 |
| `_runtime_preview(plan)` | 按 ServiceName 重读并比较配置、状态、依赖、权限摘要，再构建/复核 Preview；任一漂移调用 `_block`。 |
| `_final_state(service_name, fallback)` | 失败后尽力只读查询当前状态；查询异常返回 UNKNOWN，不虚构 fallback 成功。 |
| `_step_arguments(plan, initial_state)` | 预生成有序工具名/严格参数及每步预期状态，供事务持久化摘要。 |
| `_step_argument(plan, step, expected_state)` | 构造一个 `ServiceStepRequest`；START/STOP 只映射到两个注册名。 |
| `_result(...)` | 统一创建带时间、步骤、最终状态、完成/部分/no-op 标志的结果。 |
| `_block(plan, message, code)` | 尽力将事务标记 BLOCKED 后抛 `ServiceActionError`；数据库异常不会把拒绝变成允许。 |
| `_preview_error(preview)` | 按提权、权限、策略、依赖优先级选择稳定阻止码。 |

### 审计 `audit.service_actions`

| 方法 | 作用与隐私边界 |
|---|---|
| `ServiceActionAuditLogger.__init__(repository, app_version, git_commit)` | 注入审计库和版本证据。 |
| `previewed(plan, preview)` | 记录 ServiceName、动作、风险、分类和各摘要；不记录二进制路径/命令。 |
| `confirmation_resolved(plan, confirmation)` | 记录层级、批准/拒绝、绑定和到期/消费状态。 |
| `started(plan, preview)` | 真实控制前强制记录开始事件；失败会阻止执行。 |
| `step_completed(plan, index, result)` | 记录步骤序号、是否派发、前后状态和验证结论。 |
| `completed(plan, result)` | 记录 completed/partial/no-op 和最终状态。 |
| `failed(plan, phase, error_code, message, mutation_may_have_started)` | 记录脱敏错误类型/阶段与是否可能已派发；不保存路径或异常中的敏感参数。 |

### 运行时组合与设置

| 类/方法 | 作用 |
|---|---|
| `ServiceActionServices` | runtime 返回的不可变 bundle：应用服务、registry 和 repository。 |
| `ApplicationRuntime.create_service_action_services()` | 创建 resolver/compiler/policy/dependency/preview/validator/audit/guard，注册恰好 start/stop 两个工具并返回 bundle。 |
| `create_service_action_services.dispatched(request)` | 内部回调：验证 `ServiceStepRequest` 后把 STOP/START 分别标记 WAITING_STOPPED/WAITING_RUNNING；在平台轮询前持久化。 |
| `ApplicationRuntime.close()` | 新增关闭 service repository；应在所有 worker 有界结束后调用。 |
| `AppSettings.service_runtime_confirmation_ttl_seconds` | 即时确认 TTL，环境变量 `PC_MANAGER_SERVICE_RUNTIME_CONFIRMATION_TTL_SECONDS`，范围 15–300，默认 60。 |
| `AppSettings.service_action_timeout_seconds` | 单个 SCM 状态等待上限，环境变量 `PC_MANAGER_SERVICE_ACTION_TIMEOUT_SECONDS`，范围 5–120，默认 30。 |
| `AppSettings.from_environment()`（Stage 4C1 增量） | 读取上述两个字符串并交给 Pydantic 做范围/类型校验；不读取文件或凭据。 |

### Qt worker、对话框和管理页

| 类/方法/函数 | 作用、线程和副作用 |
|---|---|
| `ServiceWorkerSignals` | worker 的 `completed(object)`/`failed(str)` 终态信号。 |
| `PreparedServiceAction` | worker 返回 services + plan + preview + review 的不可变容器。 |
| `RuntimeServicePreview` | worker 返回重新验证 Preview 与即时确认的容器。 |
| `ServiceInventoryWorker.__init__(runtime)` | 保存 runtime；不在 GUI 线程查询 SCM。 |
| `ServiceInventoryWorker.run()` | worker 线程初始化 COM、构造服务并读取清单，发成功/失败信号，最后释放 COM。 |
| `ServicePrepareWorker.__init__(runtime, user_goal, service_name, action)` | 保存精确本地选择和动作。 |
| `ServicePrepareWorker.run()` | worker 内创建服务并只读 prepare；返回 `PreparedServiceAction`。 |
| `ServiceRuntimePreviewWorker.__init__(service, plan_confirmation_id, plan)` | 保存同一应用服务和父确认，避免重建确认内存状态。 |
| `ServiceRuntimePreviewWorker.run()` | worker 内全量重验并请求短时确认。 |
| `ServiceExecutionWorker.__init__(service, plan_confirmation_id, runtime_confirmation_id, plan, preview)` | 保存同一服务/绑定并创建协作取消 token。 |
| `ServiceExecutionWorker.cancel()` | 仅设置 token；阻止未来步骤，不强杀已派发 SCM 请求。 |
| `ServiceExecutionWorker.run()` | worker 内消费确认并执行有界步骤，发 verified 结果/失败。 |
| `require_service_inventory(value)` | Qt `object` 信号的运行时类型收窄；非 tuple/非清单项抛 `TypeError`。 |
| `require_prepared_service(value)` | 要求 `PreparedServiceAction`。 |
| `require_runtime_service(value)` | 要求 `RuntimeServicePreview`。 |
| `require_service_result(value)` | 要求 `ServiceActionResult`。 |
| `_emit_completed(signals, value)` | 发成功信号；仅吞掉 owner 已在安全关闭期间删除导致的 Qt `RuntimeError`。 |
| `_emit_failed(signals, exc)` | 格式化异常类型/消息并发失败信号；仅吞掉相同 Qt 删除竞态。 |
| `ServiceActionDialog.__init__(runtime, service_name, display_name, action, parent=None)` | 创建单对象模态式工作流对话框并立即启动只读 prepare worker。 |
| `ServiceActionDialog._build_ui()` | 构建摘要、风险、详情、进度和分阶段按钮；不执行工具。 |
| `_start_prepare()` | 禁用动作并启动后台 Preview。 |
| `_prepared(value)` | 类型收窄、显示详细 Preview；审查不通过时不启用确认。 |
| `_advance()` | 按当前 UI 阶段路由计划确认或即时确认，不允许跳级。 |
| `_approve_plan()` | 明确对话确认对象/风险后解析第一层，并启动 runtime revalidation worker。 |
| `_runtime_ready(value)` | 显示新鲜状态/依赖/身份摘要和 MANUAL 警告，启用即时确认。 |
| `_approve_runtime()` | 显式确认后解析第二层并启动 execution worker。 |
| `_executed(value)` | 显示每步和最终/部分状态，完成后按钮只关闭。 |
| `_failed(message)` | 显示友好失败，禁用执行并把取消按钮改为安全关闭。 |
| `shutdown()` | 对活动 execution worker 请求取消未来步骤。 |
| `_replace_cancel_callback(callback)` | 断开旧按钮槽并绑定唯一新阶段回调，避免重复点击多次执行。 |
| `closeEvent(event)` | 关闭前调用 shutdown；不等待或强杀当前平台调用。 |
| `_preview_html(prepared)` | HTML 转义显示身份、状态、分类、步骤、关系、权限和审查；不执行。 |
| `_runtime_html(value)` | 显示即时对象与摘要，提醒 MANUAL。 |
| `_result_html(result)` | 显示每步前后状态、验证和反向操作需新计划。 |
| `ServiceManagementTab.__init__(runtime)` | 创建独立服务管理页但不立即枚举 SCM，避免应用启动和其他任务被服务清单占用线程。 |
| `_build_ui()` | 构建筛选、表格、Start/Stop/Restart 检查按钮和说明。 |
| `refresh()` | 禁用写入口并在线程池刷新新鲜清单。 |
| `showEvent(event)` | 用户首次打开本页时惰性调用 `refresh()`；后续显示不自动重复，手动刷新仍可用。 |
| `_inventory_ready(value)` | 类型收窄、保存清单并重绘；不会复用 Stage 3 陈旧对象执行。 |
| `_render()` | 按本地筛选填充展示表；ServiceName 保存为选择提示。 |
| `_selection_changed()` | 按选中 `ServiceInventoryItem` 的动作许可开启对应检查按钮。 |
| `_set_actions(start, stop, restart)` | 集中控制三个按钮的 enabled 状态。 |
| `_selected()` | 返回当前选中领域项或 `None`，不凭表格文本重建身份。 |
| `_open_action(action)` | 对选中精确项打开工作流对话框；结束后刷新清单。 |
| `open_action_request(action, target_hint, display_hint=None)` | 聊天入口；本地查找精确目标/选中引用后打开同一对话框，歧义拒绝。 |
| `_failed(message)` | 显示只读加载错误并保持动作禁用。 |
| `shutdown()` | 关闭管理页时请求活动对话框取消未来步骤。 |
| `_search_text(item)` | 生成仅供本地筛选的 ServiceName/显示名/状态/发布者/分类文本。 |
| `MainWindow._build_service_management_tab()` | 将“服务管理”作为独立页加入主窗口并连接聊天引用。 |
| `MainWindow._remember_service_reference(service_name, display_name)` | 保存最近一次本地明确服务引用；仅作下次代词提示，不是执行授权。 |
| `MainWindow._handle_chat()`（Stage 4C1 增量） | 识别有限服务动作；需要唯一目标或明确最近引用，随后打开同一 Preview 流程。LLM 不能直接调用 SCM。 |
| `MainWindow.shutdown()`（Stage 4C1 增量） | 请求服务/其他 worker 取消，并等待 35 秒覆盖默认 SCM 30 秒 timeout + 清理余量，避免数据库先关闭。 |
| `_references_previous_service(text)` | 仅检测有限中英文代词短语；没有最近明确引用时不会猜目标。 |

## Stage 4B 启动项安全管理 API

下列接口均属于现有项目的 Stage 4B。除 Windows 适配器和 GUI worker 外，核心接口可在无
GUI、无模型、无真实注册表写入的环境中测试。所有写接口只接受不可伪造的身份/备份引用，
不接受任意注册表路径、命令行或 Shell 字符串。

### 领域模型 `domain.startup_actions`

`StartupSource`、`StartupActionType`、`StartupEntryStatus`、`StartupSafetyClass`、
`StartupManagementMode`、`StartupSafetyDecision`、`StartupErrorCode` 和
`StartupTransactionState` 是有限枚举，分别约束来源、唯一两种动作、可证明的配置状态、
安全分类、是否可管理、最终策略决定、稳定错误码和持久化事务状态。它们没有副作用；未知值
在 Pydantic 校验阶段失败，防止模型或 UI 扩大工具能力。

#### `RegistryStartupIdentity.canonical_digest()`

散列 hive、固定键路径、值名、原始类型/数据摘要、命令指纹、解析路径、StartupApproved
摘要和注册表视图。返回 64 位 SHA-256 十六进制字符串；不返回或记录原始命令字节。

#### `FolderStartupIdentity.canonical_digest()`

先规范化快捷方式和目标路径大小写，再散列卷序列号、File ID、`.lnk` 内容摘要、参数/
工作目录指纹和 approval 摘要。用于发现快捷方式被替换、改写或重定向。

#### `StartupIdentity.require_matching_identity()` / `canonical_digest()`

模型校验器确保注册表来源只能携带 registry 身份、Startup Folder 来源只能携带 folder
身份；混合或缺失会抛 `ValidationError`。摘要以来源值为前缀，避免不同来源的同名对象碰撞。

#### `StartupObservation.current_state_digest()`

绑定身份、状态、状态证据、管理模式、发布者和解析后的程序路径，返回确认使用的当前状态
摘要。只读，无 I/O；任一可见安全证据变化都会使旧 Preview/确认失效。

#### `StartupBackupPayload.require_source_material()` / `canonical_digest()`

校验 HKCU Run 备份必须有精确值字节和类型，Startup Folder 备份必须有精确 `.lnk` 字节
和 Agent 存储路径，且两类材料不能混用；只读来源不能生成写备份。摘要覆盖解密后的全部恢复
材料。原始字节字段在 `repr` 中隐藏，并只能持久化到加密 vault。

#### `StartupActionPlan.validate_contract()` / `canonical_digest()`

强制每个启动项写计划都是 R2、FULL、计划确认加即时确认且只有一个精确目标；违反时抛
`ValidationError`。摘要覆盖所有执行相关字段，计划文字或动作改变都会失效。

#### `StartupActionPreview.executable` / `canonical_digest()`

`executable` 仅在确定性策略 ALLOW、备份已验证且 observation 仍匹配状态摘要时为真。
`canonical_digest` 绑定完整 Preview，供确认和事务使用。两者只读。

#### `_canonical_digest(payload)`

内部辅助函数，以排序、紧凑 JSON 编码 `JsonValue` 后计算 SHA-256；为所有 Stage 4B 摘要
提供唯一规范算法，不读取文件或数据库。

`StartupSafetyAssessment`、`StartupBackupReference`、`StartupActionRequest`、
`StartupMutationResult`、`StartupActionTransaction` 和 `DisabledStartupRecord` 是不可变数据
载体。工具 request 只有动作、身份、状态摘要和备份引用，精确命令/快捷方式字节不会通过
公共工具参数传播。

### 应用运行时与配置

#### `ApplicationRuntime.create_startup_action_services()`

创建一个短生命周期 `StartupActionServices` 组合，注入共享确认、事务 repository、加密 vault
和审计，以及新的 Windows adapter、resolver、policy、Preview、validator、registry 和两个窄
工具。它不扫描或修改启动项；实际 I/O 由后续明确调用触发。非 Windows 平台抛运行时错误。

#### `ApplicationRuntime.close()`（Stage 4B 扩展）

在既有资源之外关闭启动项 vault/repository engine。主窗口先等待有限 worker 完成，再调用此
方法，避免后台线程使用已释放数据库；重复关闭由底层 SQLAlchemy 安全处理。

#### `AppSettings.startup_plan_confirmation_ttl_seconds` / `startup_runtime_confirmation_ttl_seconds`

从环境读取计划与即时确认有效期并返回正整数；缺失时使用保守默认值，非法或非正值让设置加载
失败，不会退化为永不过期确认。

#### `MainWindow._build_startup_management_tab()` / `shutdown()` / `closeEvent()`（Stage 4B 扩展）

前者只构建并连接 Stage 4B 页面状态；`shutdown` 通知该页拒绝待确认请求并等待全局线程池；无
托盘的直接关窗也先调用 shutdown。主窗口仍不直接访问 platform 或 ToolRegistry。

### 异常 `domain.startup_errors`

#### `StartupActionError.__init__(code, message)`

保存稳定 `StartupErrorCode` 和面向用户的非敏感消息。`StartupIdentityChangedError.__init__`
固定为身份变化；`StartupConflictError.__init__` 固定为不覆盖冲突；`StartupBackupError` 用于
缺失、损坏或未验证备份。异常不会自动重试或降级权限。

### 策略、Preview 与独立复核

#### `StartupSafetyPolicy.__init__(agent_root, windows_directory=None)`

规范化 Agent 根目录和 Windows 目录。默认 Windows 目录来自 `WINDIR`；构造过程不枚举或
写入启动项。

#### `StartupSafetyPolicy.assess(observation, action)`

按 scope、Agent 路径/名称、程序存在性、Windows 路径、安全/驱动/企业标记、发布者、管理
模式和允许来源顺序分类。仅普通当前用户、已知发布者、受支持来源和正确状态可 ALLOW；其余
返回 BLOCK、稳定原因码和解释。发布者只是组合证据，模型不能覆盖结论。

#### `_canonical(path)` / `_is_within(path, root)` / `_explain(reason)`

分别做非严格解析加 Windows 大小写规范、无异常的祖先关系检查、以及稳定错误码到说明的
映射。均为纯辅助函数，不执行变更。

#### `StartupPreviewEngine.__init__(policy)` / `build(plan, observation, backup)`

注入确定性策略；`build` 先比较身份、备份 ID/摘要和当前状态，再评估策略并创建不可变
Preview。任何不一致抛 `ValueError`，且在异常前无写操作。

#### `StartupActionSafetyValidator.__init__(registry)` / `review(plan, preview)`

独立检查 `startup.disable`/`startup.restore` 是否注册、manifest 风险/回滚/批量是否精确、
plan/Preview/action/identity/state/backup 绑定是否一致及策略是否 ALLOW。返回
`StartupSafetyReview(approved, issues)`；所有问题都收集后拒绝，不执行工具。

### 双重确认 `confirmation.startup_actions`

#### `StartupActionConfirmationService.__init__(plan_ttl_seconds=300, runtime_ttl_seconds=60, now=None)`

设置两个正数有效期和可测试时钟；非正值抛 `ValueError`。确认只保存在当前进程内，重启后
不会恢复成执行权限。

#### `request_plan(plan, preview)` / `resolve_plan(id, approved, plan, preview)`

前者仅为可执行且完全匹配的 Preview 创建 PLAN 请求；后者只能一次性批准/拒绝尚未过期的
同一请求。返回不可变 `StartupActionConfirmation`；失配、未知、重复或过期抛
`StartupConfirmationError`。

#### `request_runtime(plan_confirmation_id, plan, preview)`

要求父 PLAN 已批准、未过期、动作和绑定仍一致；允许绑定经过实时重验的新 Preview，并创建
较短有效期的 RUNTIME 请求。父确认不能是拒绝、消费或其他动作。

#### `resolve_runtime(...)` / `consume_runtime(...)`

即时确认的决议与消费接口。消费发生在工具写边界，同时把父/子确认标为 CONSUMED；重复消费、
Preview 变化、父确认失效或过期都抛异常。无平台副作用。

#### `_create` / `_resolve` / `_get` / `_require_not_expired` / `_require_executable` / `_require_current`

内部函数分别创建摘要绑定请求、执行一次性状态变化、查找并拒绝空/未知 ID、标记过期、检查
Preview 可执行性，以及比较 action/transaction/operation/plan/identity/state/backup/Preview
绑定。它们是所有公共确认方法共享的 fail-closed 实现。

### 目标解析与编排

#### `StartupTargetResolver.__init__(platform)` / `list_current()`

注入有限平台协议；每次 `list_current` 都重新读取当前配置并按确定性顺序返回，不使用缓存。

#### `resolve_name(query)` / `resolve_identity(identity)`

名称解析先精确再有限模糊匹配，零结果和多结果分别抛未找到/歧义错误；身份解析调用平台
`inspect`，严格拒绝身份变化。UI 的旧表格行只是查询线索，不是授权。

#### `StartupActionService.list_current()` / `list_disabled()`

前者实时读取并为每项附加 DISABLE 策略评估；后者只读 Agent 的持久化禁用索引。均不执行
变更。

#### `prepare_disable(user_goal, identity)`

解析一个实时目标、评估策略、捕获精确备份、DPAPI 加密持久化并验证读回，然后建立计划、
Preview、独立审查、事务和审计。任一前置失败即停止；成功仍未禁用启动项。

#### `prepare_restore(user_goal, backup_id)`

只从 Agent 禁用索引和相同 verified vault 备份建立恢复计划；不会接受任意路径或外部备份。
确认原位置无新对象和禁用材料未变后返回 plan/Preview/review，仍无平台写入。

#### `request_plan_confirmation` / `resolve_plan_confirmation`

把编排事务与确认服务衔接，持久记录请求/结果和审计。拒绝时进入阻止状态；审计或数据库不可用
时不授予执行能力。

#### `request_runtime_confirmation` / `resolve_runtime_confirmation`

执行前重新读取身份、发布者/路径/approval、备份和策略，产生新 Preview 后申请短期确认并
持久化。变化、冲突或备份错误会阻止事务，不会沿用旧 Preview。

#### `execute(plan_confirmation_id, runtime_confirmation_id, plan, preview)`

核对父子确认关联，消费确认，写前审计并生成数据库 execution authorization；再通过唯一
`ToolRegistry` 路径调用对应窄工具。结果经过类型/后置条件验证并更新禁用索引、事务和审计。
失败会记录 BLOCKED/FAILED/ROLLBACK 状态并重新抛出，不尝试 shell 或提权。

#### `_prepare` / `_build_plan` / `_revalidate` / `_block` / `_arguments`

内部函数分别完成共享的 Preview/复核/事务持久化，构造不可变 R2 计划，按动作重验 active 或
disabled 状态，安全地记录阻止原因，以及生成不含秘密的严格工具参数。`_restore_observation`
从原 observation 生成仅供恢复评估的状态；`_tool_name` 是动作到两个固定工具名的完整映射。

### 加密备份与事务持久化

#### `BackupProtector.protect(plaintext)` / `unprotect(ciphertext)`

平台无关协议：实现必须返回加密字节或解密字节，失败须抛异常。生产实现是当前用户 DPAPI；
测试可注入确定性 fake。

#### `StartupBackupVault.__init__(database_path, protector)` / `initialize()` / `close()`

创建独立 SQLAlchemy engine 和保护器；`initialize` 建表并启用服务，`close` 释放连接。未初始化
调用任何数据方法抛 `StartupStoreError`。

#### `StartupBackupVault._require_initialized()` / `StartupActionRepository._require_initialized()`

内部前置检查，engine/session factory 尚未建立时统一抛 `StartupStoreError`。数据库不可用时不会
创建确认或运行启动项写操作。

#### `StartupBackupVault.store(payload)`

序列化精确 payload、计算明文摘要、调用保护器、写入密文，再强制读回/解密/摘要校验；只在
全部成功时返回 verified `StartupBackupReference`。数据库或 DPAPI 失败使计划停止。

#### `StartupBackupVault.load(backup_id, expected_digest)` / `_load(...)`

读取密文、解密和 Pydantic 校验，检查已验证标志、存储摘要及调用方期望摘要；任一不一致抛
`StartupStoreError`。公共 `load` 永远不允许未验证记录。

#### `StartupActionRepository.__init__` / `initialize` / `close`

管理事务、确认和禁用索引表。初始化时把不可能安全继续的活动事务标为中断；关闭释放 engine。

#### `create(plan, preview)` / `transition(transaction_id, expected, target, ...)`

`create` 持久化新事务及精确摘要；`transition` 使用允许的前态集合做比较并更新目标状态、错误和
结果摘要。状态竞争、非法跳转或数据库错误抛 `StartupStoreError`。

#### `bind_runtime_preview(...)` / `record_confirmation(...)` / `bind_confirmation(...)`

分别保存实时 Preview 摘要、两级确认记录和事务上的确认 ID。只保存引用、决定和时间，不保存
原始命令或快捷方式字节。

#### `consume_confirmation_pair(transaction_id, plan_confirmation_id, runtime_confirmation_id)`

在一个数据库事务中验证同 action/plan/preview/identity/state/backup 的父子确认并一次性消费；
返回 `ExecutionAuthorization` 所需证明。重复、过期或摘要变化均拒绝。

#### `record_disabled` / `mark_restored` / `get_disabled` / `list_disabled`

维护 Agent 可恢复索引：成功禁用后记录原 observation 和 backup 引用；成功恢复后标记恢复；
查询不存在或已恢复记录会失败/排除。索引不是 backup，本身不含精确恢复字节。

#### `get(transaction_id)` / `require_execution_authorization(authorization, tool_name, arguments)`

读取 typed 事务；授权检查比较事务状态、固定工具、完整参数摘要和已消费确认，确保绕过编排器
直接调用 registry 仍失败。

#### `StartupExecutionGuard.require(...)`

把通用 `WriteExecutionGuard` 协议桥接到 repository 的严格授权检查，无返回值；不匹配抛
`WriteAuthorizationError`/存储错误。

`_transaction_from_row`、`_disabled_from_row` 将 ORM 行恢复为 typed 模型；`_as_utc` 统一
SQLite naive 时间为 UTC。它们只做数据转换。

### 平台协议、Windows 与 DPAPI

#### `StartupManagementPlatform.list_entries(max_items=5000)`

返回有限 `StartupObservation` 元组。生产实现只枚举六个固定来源，按上限截断；单项无法解析
时产生 READ_ONLY/UNKNOWN observation，而不是猜测可执行状态。

#### `inspect(identity)` / `capture_backup(identity, backup_id)`

`inspect` 在原来源重新定位并比较完整身份，未找到返回 `None`，变化抛身份错误；
`capture_backup` 只为可写当前用户来源读取精确原始字节、类型、approval 证据和禁用存储位置。

#### `disable(payload)` / `restore(payload)` / `disabled_material_matches(payload)`

前两者是唯一平台写 API：校验固定来源、当前状态和 approval 后，事务删除/还原 HKCU Run 值，
或同卷无覆盖移动 `.lnk`。后者只读验证禁用材料仍与 backup 精确一致。冲突、权限、身份和平台
错误均抛 typed 异常；没有 fallback。

#### `WindowsStartupManagementPlatform.__init__(disabled_storage_root)`

规范化并创建 Agent 自有禁用存储根，加载固定 Win32/COM 函数签名。它不接受注册表根或任意
来源配置。

#### `_list_registry_source` / `_list_folder_source` / `_registry_observation` / `_folder_observation`

内部只读枚举与 observation 构造函数：读取原始注册表值/`.lnk`，生成隐私最小化命令摘要、
稳定身份、解析程序和发布者证据。不会把完整命令暴露给 UI、audit 或模型。

#### `_inspect_location` / `_approval_for_registry` / `_require_approval_unchanged`

精确定位身份、只读 StartupApproved 字节/摘要、并在写入前比较捕获证据。不存在和改变是不同
结果；approval 未知格式保持只读且从不写入。

#### `_read_optional_raw_value` / `_read_raw_value` / `_query_raw_handle`

通过 `RegQueryValueExW` 两阶段读取保留精确注册表类型和原始字节，处理值增长并设置大小上限。
可选版本把“值不存在”转为 `None`；其他错误不吞掉。

#### `_delete_registry_value_transacted` / `_set_registry_value_transacted`

在固定 HKCU Run 键上通过 transacted handle 删除或按原类型/字节恢复一个值；不存在、已存在、
范围不符均拒绝，commit 前不会报告成功。

#### `_open_transacted_run_key` / `_commit_transaction` / `_rollback_transaction` / `_close_registry_key` / `_close_handle`

创建 Windows transaction、只打开编译期固定 native-view HKCU Run 键、提交/回滚并确定释放
句柄。任一 Win32 返回码转为明确平台错误；rollback/close 用于异常清理。

#### `_decode_registry_command` / `_resolve_command` / `_command_line_to_argv` / `_resolve_executable_path`

按注册表类型解码字符串、用 `CommandLineToArgvW` 解析、展开受控环境变量并只接受可确定的绝对
`.exe` 路径。歧义、非字符串类型或解析失败返回 unresolved/read-only，不执行命令。

#### `_read_shell_link` / `_publisher` / `_approval_status`

分别通过 ShellLink COM 只读 target/arguments/working directory，通过版本资源读取辅助公司名，
以及仅解释已知 12-byte StartupApproved 状态。可执行文件没有版本资源时 `_publisher` 返回
`None` 并由策略保持只读，而不是中断整个清单；未知证据不推断成 enabled/disabled。

#### `_unresolved_registry_observation` / `_unresolved_folder_observation`

为不可安全解析的条目创建可展示但不可操作的 observation，保留来源/范围/错误证据而不泄露
完整命令。

#### `_require_registry` / `_require_folder` / `_require_disabled_path` / `_read_bounded_file` / `_view_flag` / `_raise_registry_error`

内部 fail-closed 守卫：验证来源和 identity 类型、禁用路径必须在 Agent 根内、文件大小受限、
注册表视图仅限固定枚举、Win32 错误转换为 stable exceptions。

#### `WindowsCurrentUserDataProtector.__init__(entropy=None)`

准备可选应用熵和 Win32 `CryptProtectData`/`CryptUnprotectData` 签名。熵不是密码；安全性仍由
当前 Windows 用户 DPAPI 上下文提供。

#### `protect(plaintext)` / `unprotect(ciphertext)`

加密或解密 bytes，正确释放 `LocalAlloc` 输出。空/损坏/其他用户上下文等失败抛
`OSError`，不会退回明文存储。

### 命令对象和注册工具

#### `DisableStartupCommand.execute()` / `verify()` / `rollback()`

`execute` 调平台 disable；`verify` 要求 active 位置缺失且禁用材料精确匹配；`rollback` 调
exact restore 并重新 inspect。它不吞异常，也不改变风险/确认。

#### `RestoreStartupCommand.execute()` / `verify()` / `rollback()`

`execute` 调 exact restore；`verify` 要求原 identity 恢复；`rollback` 重新 disable 并验证
active 缺失且禁用材料匹配。

#### `DisableStartupTool.__init__` / `manifest` / `execute(request, cancellation)` / `_load_payload`

注入平台、vault、repository；manifest 固定为 R2/FULL/双确认/单对象。执行时校验 action、
写前取消和 backup identity，运行命令并验证；验证失败立刻尝试 rollback，返回 typed
`StartupMutationResult`，不声称未来启动行为。

#### `RestoreStartupTool.__init__` / `manifest` / `execute(request, cancellation)`

除相同 manifest 约束外，还要求 Agent disabled 索引存在且身份匹配，再从 verified vault
恢复。验证失败执行 inverse disable；取消仅在 mutation 前生效。

#### `_manifest(name, description)`

构造固定 manifest：ordinary-user、current-user-startup-only、30 秒、批量 1、支持取消/
Preview、两级确认、FULL、Windows-only。调用方不能改变风险或来源。

### 审计

#### `StartupActionAuditLogger.__init__(repository, app_version, git_commit)`

注入通用审计库和版本信息；不持有 backup vault，避免审计接触恢复字节。

#### `previewed(plan, preview)` / `confirmation_resolved(plan, confirmation)`

记录计划/Preview/身份/状态/backup 摘要、风险、策略和用户决议。参数经过固定字段选择，原始
命令和精确 backup 字段不会被写入。

#### `started(plan, preview, runtime_confirmation_id)` / `completed(plan, result)` / `failed(...)`

分别记录强制性的写前证据、验证完成结果和阻止/失败/回滚状态。审计失败由编排器视为执行
阻断条件；写后审计失败不会伪造成功或自动重复系统写。

#### `_tool_name(plan)`

把 DISABLE/RESTORE 映射到唯一两个固定工具名，仅用于审计字段。

### GUI 与 worker

#### `StartupManagementTab.__init__(runtime)` / `_build_ui()` / `refresh()`

创建两个表格、筛选、刷新和单对象操作按钮；初始化后启动只读 worker。`refresh` 防止并发重复
读取并禁用操作按钮，UI 线程不访问平台。

#### `_inventory_ready` / `_render` / `_selection_changed`

类型校验 worker 结果、按文本筛选并填充 active/disabled 表、依据精确 ALLOW/管理模式控制
按钮。显示值从不变成新的执行参数。

#### `_disable_selected` / `_restore_selected` / `_open_dialog` / `_selected_active` / `_selected_disabled`

只从当前明确选中行取一个 typed 身份或 backup ID，创建模态式安全对话；无选择/多义时不猜测。
对话结束刷新清单。

#### `_failed(message)` / `shutdown()` / `_table` / `_active_text` / `_disabled_text`

显示友好错误、拒绝所有打开确认对话、构造只读表格和生成本地筛选文本。shutdown 不授权或
恢复动作。

#### `StartupActionDialog.__init__` / `_build_ui` / `_start_prepare`

默认取消、禁用确认按钮并在 worker 中捕获 backup/构建 Preview；窗口创建本身不写启动配置。

#### `_prepared` / `_show_preview` / `_has_plan_state` / `_risk_text` / `_preview_html`

验证 typed worker 结果并显示对象、来源、状态、程序路径、安全分类、风险、权限、备份、回滚
条件和原因。HTML 全部转义；只有 ALLOW、verified backup 和匹配状态允许继续。

#### `_primary_clicked` / `_approve_plan` / `_runtime_prepared` / `_approve_runtime`

驱动两个明确阶段：批准计划后启动实时重验，返回新 Preview 后才允许短期即时确认。按钮文字
描述具体动作；状态不完整会安全失败。

#### `_completed` / `_result_html` / `_cancel_clicked` / `_reject_plan_and_close` / `_reject_runtime_and_close` / `_failed`

显示 verified/rollback 结果；取消/关闭会记录相应拒绝并不调用工具；错误显示后恢复安全按钮
状态。结果文字不把配置状态说成未来启动保证。

#### `_set_busy` / `closeEvent` / `shutdown`

管理 worker 期间按钮状态；用户关窗默认为取消，`shutdown` 主动拒绝尚未决确认并关闭。已开始
的单个 Win32 mutation 不被强杀，而由 worker 完成验证。

#### `_require_plan_state()`

在确认/拒绝边界显式要求 services、plan 和 Preview 同时存在，缺失时抛 `RuntimeError`。
生产安全状态不依赖 Python 优化模式会移除的 `assert`。

#### `StartupInventoryWorker.run()` / `StartupPrepareWorker.run()`

每个 worker 在线程内 `CoInitialize/CoUninitialize`；前者读取/分类 active 与 disabled 清单，
后者只为明确 action+identity/backup 准备计划。异常转为一个 failed signal。

这两个 worker 的 `__init__` 只保存 runtime 和明确选择的 action/identity/backup 引用；
`StartupRuntimePreviewWorker.__init__` 保存 service、父确认和 plan；
`StartupExecutionWorker.__init__` 保存 service、父子确认、plan 和 Preview。构造函数均不执行
扫描、确认或写操作。

#### `StartupRuntimePreviewWorker.run()` / `StartupExecutionWorker.run()`

前者执行实时重验和申请即时确认；后者消费两级批准并执行一个窄工具。两者都在后台运行并只
通过 typed Qt signal 返回。

#### `require_inventory` / `require_prepared_startup` / `require_runtime_startup` / `require_startup_result`

Qt `object` signal 的运行时类型收窄函数。类型不符抛 `TypeError`，防止 UI 把任意对象当作
授权或结果。

`StartupWorkerSignals` 只定义 completed/failed 信号；`StartupInventory`、
`PreparedStartupAction` 和 `RuntimeStartupPreview` 是冻结的数据传输对象，无系统副作用。

## Stage 4A 受控进程管理 API

### 领域模型与摘要

#### `RiskLevel.severity`（Stage 4A 扩展）

返回显式映射：R0=0、R1=1、R2=2、R2_HIGH_IMPACT=3、R3=4、R4=5。不能再从枚举字符串
取数字，因为强制终止使用非数字子等级。安全门槛仍以具体 manifest/动作校验为准。

#### `ToolManifest.__post_init__()`（Stage 4A 扩展）

写工具通常必须声明非 NONE rollback；Stage 4A 仅在 `irreversible=True`、风险为 R2 或
R2_HIGH_IMPACT、同时需要 runtime confirmation 且支持 Preview 时允许 NONE。反过来，
irreversible 工具不得声称任何可恢复 rollback。矛盾清单构造时即抛 `ValueError`。

#### `ApplicationRuntime.create_process_action_services()`

读取 Agent 自身完整身份，构造 2000 项本地 resolver、当前 owner/session/Agent PID 策略、
SQLite 执行守卫、只含两个进程工具的 registry、Preview/validator/audit/service 并注入配置
超时。若 Agent 身份无法安全建立则失败；不请求管理员权限。

#### `ApplicationRuntime.close()`（Stage 4A 扩展）

在其他本地仓库之前关闭 process action repository，释放数据库连接并使之后的写能力失效。

#### Stage 4A 配置字段

`process_action_max_applications`、`process_action_max_processes`、
`process_graceful_timeout_seconds`、`process_force_timeout_seconds` 和
`process_runtime_confirmation_ttl_seconds` 分别限制应用数、成员数、两类等待和即时确认有效
期；Pydantic 强制安全范围，并支持相应 `PC_MANAGER_*` 环境变量。

#### `ProcessIdentity.canonical_digest()`

将 PID、进程名、UTC 创建时间、规范化可执行路径、owner SID 和 session ID 编码为稳定
SHA-256。该摘要防止 PID 复用，并绑定计划、Preview、确认、工具参数和验证结果。只读；
字段无法证明时上游不得创建可执行身份。

#### `ProcessObservation.graceful_supported`

当该成员拥有至少一个顶层窗口时返回 `True`。应用组 Preview 会把“任一成员有窗口”作为
组级正常退出能力，但所有成员仍分别分类、列出和验证。

#### `ProcessTargetQuery.require_one_query_value()`

校验 NAME 只能有 `text`，PID/SELECTED_PROCESS 只能有 `pid`。混合或缺失选择器抛出
`ValueError`，阻止模型/界面用模糊文本和 PID 同时扩大目标。

#### `ResolvedProcessTarget.identity_set_digest()`

排序所有成员身份摘要后计算组摘要。浏览器/多进程应用成员变化会使旧 Preview 失效。

#### `ProcessActionPlan.validate_safety_contract()`

强制正常退出为 R2、强制终止为 R2_HIGH_IMPACT；两者都必须双确认且 rollback 为 NONE；
预计进程数必须与实际成员完全一致。违约抛出 `ValueError`。

#### `ProcessActionPlan.canonical_digest()` / `target_set_digest()`

前者摘要完整不可变计划，后者只摘要所有目标身份。任一执行字段或身份变化都会改变确认
绑定；均无系统副作用。

#### `ProcessActionPreview.executable` / `validate_counts()` / `canonical_digest()`

`executable` 仅在每个成员都有 ALLOW 评估时为真；`validate_counts` 核对应用数、进程数和
身份集合摘要；`canonical_digest` 绑定完整实时 Preview。伪造总数/成员/分类会校验失败。

#### `ProcessActionRequest.validate_digest()`

注册工具输入的身份集合必须与 `target_set_digest` 一致。工具只接受 1–20 个身份和 1–30
秒超时，不接受命令、自由参数、管理员标志或未验证 PID。

#### `ProcessActionToolResult.all_exited`

仅当所有成员状态为 EXITED 或 ALREADY_EXITED 时返回真。IDENTITY_CHANGED、超时、取消、
未尝试、权限失败都不能伪装成完成。

### 目标解析与计划

#### `is_process_action_request(user_goal)` / `preferred_process_action(user_goal)`

用有限关键词识别进程生命周期请求。只有显式“强制终止/force kill”等选择强制动作，其他
关闭意图默认正常退出。返回布尔值或 `ProcessActionType`；不解析系统状态。

#### `process_target_query(user_goal)`

本地提取 PID 或精确名称；拒绝“所有进程”和无上下文“它”。返回严格
`ProcessTargetQuery`；模糊/空目标抛出 `ValueError`。输出仍只是查询，不是执行授权。

#### `ProcessActionPlanCompiler.compile(...)` / `compile_from_text(...)`

通过 resolver 读取新状态，创建一个有限不可变计划并设置确定风险。名称歧义、目标缺失、
数量超限由解析/Schema 抛错；不调用模型或平台写 API。

#### `ProcessActionPlanCompiler.compile_resolved(...)`

仅用于正常退出后的独立强制流程，把 resolver 刚取得的“仍存活成员”写入新事务；空集合
拒绝。它不允许复用旧 plan/operation/transaction ID。

#### `ProcessTargetResolver.resolve(query)`

PID/表格选择会重新 inspect；名称会在最多 2000 个可完整识别进程中精确匹配进程名/文件名/
stem，并按规范化可执行路径分组。不同路径同名返回 TARGET_AMBIGUOUS；找不到返回
TARGET_NOT_FOUND。`include_application_group` 决定是否绑定同可执行路径所有成员。

#### `re_resolve(target)` / `resolve_current_application_group(target)` / `inspect_pid(pid)`

`re_resolve` 要求当前应用组身份集合与旧组完全相同，否则 TARGET_GROUP_CHANGED；用于确认和
执行前失效检查。`resolve_current_application_group` 只为新强制计划返回当前剩余成员，不会
继承旧授权。`inspect_pid` 返回当前完整观察或 `None`。

### 安全 Preview 与验证

#### `ProcessSafetyPolicy.assess(observation, action, application_has_window=None)`

按顺序阻止 Agent PID、SYSTEM SID、其他 owner/session、关键/系统进程、受保护进程、安全
软件、SCM 服务和 Windows 目录程序；无法可靠分类时默认阻止。正常退出另要求应用组有可
关闭窗口。返回分类、ALLOW/BLOCK、稳定原因码和说明；模型不能覆盖。

#### `ProcessPreviewEngine.build(plan, targets=None)`

对原始或重验证目标逐成员执行策略，计算身份摘要、应用/进程/窗口数、CPU 和内存影响，
返回不可变 Preview。只读；目标集合与计划不符会在独立 validator 中拒绝。

#### `ProcessActionSafetyValidator.review(plan, preview)`

核对注册工具、输入 Schema、精确风险、双确认、Preview、irreversible/NONE 声明、5 应用/
20 进程上限和每个 ALLOW 评估。返回 `ProcessSafetyReview`；任一 issue 都使 approved=false。

### 双确认

#### `ProcessActionConfirmationService.request_plan(...)` / `resolve_plan(...)`

只为可执行且匹配计划的 Preview 创建 PLAN 请求，并在有效期内批准/拒绝。绑定 action、
transaction、operation、plan/preview/target 摘要和进程数；状态不对或过期抛
`ProcessConfirmationError`。

#### `request_runtime(...)` / `resolve_runtime(...)` / `consume_runtime(...)`

RUNTIME 请求要求同动作的已批准 PLAN，并绑定刚重验证的新 Preview。`consume_runtime` 在
执行边界同时消费父/子确认，防重放；动作、身份、摘要、数量、Preview ID 或有效期变化均
拒绝。确认只存内存授权，重启不可恢复。

### 平台协议与 Windows 实现

#### `ProcessManagementPlatform.list_processes(max_processes)` / `inspect_process(pid)`

协议返回完整 `ProcessObservation`，或对无法安全识别的 PID 返回 `None`。Windows 实现查询
窗口、服务关系、路径、创建时间、owner SID、session、关键标志、保护等级和有限资源值；
从不读取命令行或窗口文本。

#### `request_graceful_exit(identity, timeout_seconds, cancellation)`

打开 QUERY_LIMITED + SYNCHRONIZE 句柄，在同一句柄上重读身份，枚举当前 PID 的顶层窗口，
用 `PostMessageW(WM_CLOSE)` 请求退出，再轮询 `WaitForSingleObject`。取消仅停止等待；已经
发送的消息不能撤销。返回逐成员状态，不提权。

#### `force_terminate(identity, timeout_seconds)`

打开 QUERY_LIMITED + TERMINATE + SYNCHRONIZE 句柄，先做同句柄身份比较，再调用
`TerminateProcess` 并等待原句柄 signal。权限不足返回 ACCESS_DENIED；不尝试 UAC 或备用
命令。Wait 异常/超时分别返回 FAILED/STILL_RUNNING。

#### Windows 私有辅助函数

`_identity_from_handle` 组合路径、创建时间、owner、session 和父 PID；`_query_*` 各查询一
个字段；`_is_process_critical` 和 `_process_protection_level` 失败时让整个观察不可执行；
`_window_counts`/`_windows_for_pid` 用 EnumWindows + GetWindowThreadProcessId；
`_active_service_processes` 仅用 SCM 枚举/查询句柄；`_wait_for_process` 支持取消；
`_open_failure`、`_identity_changed`、`_platform_failure` 把 Win32 错误转换为稳定成员结果；
`protection_level_none()` 返回 Windows 未保护 sentinel。均不使用 shell。

### 注册工具

#### `RequestProcessExitTool.execute(request, cancellation)`

校验 action 必须为 REQUEST_GRACEFUL_EXIT，然后并发请求应用组成员，确保组总等待时间不按
成员倍增。取消前未开始成员返回 CANCELLED_WAITING。返回固定长度 typed result。

#### `ForceTerminateProcessTool.execute(request, cancellation)`

校验 action 必须为 FORCE_TERMINATE，按顺序处理成员；取消阻止后续成员，意外 FAILED 后
停止并把剩余成员标为 NOT_ATTEMPTED。无自动 fallback。

#### `_manifest(name, description, risk)`

构造完整 Windows 普通用户工具清单：非只读、非幂等、可取消、Preview、PLAN+RUNTIME、
irreversible、rollback NONE、20 项、35 秒、固定审计字段。错误风险/声明在模型校验时拒绝。

### 持久化、审计与编排

#### `ProcessActionRepository.initialize()` / `create()` / `transition()`

创建 additive 表并验证 SQLite。启动时把 VALIDATING/REQUESTING/FORCE_TERMINATING 等活动
状态标为 INTERRUPTED，把仅等待确认的旧状态取消；绝不自动恢复。`create` 持久化精确工具/
参数摘要，`transition` 只允许显式状态图边。数据库失败抛 `ProcessActionStoreError`。

#### 确认/能力方法

`record_confirmation` 保存非秘密证据；`bind_plan_confirmation`、
`bind_runtime_confirmation` 和 `bind_runtime_preview` 更新精确绑定；
`consume_confirmation_pair` 原子验证并消费双确认；`require_execution_authorization` 同时核对
事务状态、operation/plan/preview、工具、参数摘要、父子确认和 action。任何不一致拒绝工具。

#### `get()` / `list_recent()` / `close()`

读取单事务、返回最多 500 条最新记录、释放 engine。未初始化或未知事务抛存储错误。

#### `ProcessExecutionGuard.require(...)`

把 ToolRegistry 写能力检查委派给持久化仓库，确保 UI 或模型无法直接调用非只读工具。

#### `ProcessActionAuditLogger.previewed()` / `confirmation_resolved()` / `started()` /
`completed()` / `failed()`

分别记录 Preview 决策、两层确认、写前事件、逐成员验证结果和失败阶段。写前审计不可用会
fail closed。记录身份/路径但不含命令行、窗口内容、token 或文档内容；所有事件仍经过通用
递归脱敏。

#### `ProcessActionService.prepare*()`

`prepare_from_text`、`prepare`、`prepare_plan` 串联解析、Preview、独立审查、事务和审计。
阻止计划不会发确认。`prepare_force_after_graceful` 只从当前仍存活原应用成员创建全新的
高影响事务；目标已退出则拒绝。

#### 计划/即时确认服务方法

`request_plan_confirmation`、`resolve_plan_confirmation`、
`request_runtime_confirmation`、`resolve_runtime_confirmation` 同步内存确认、SQLite 状态和
审计。即时请求先重新解析身份/组和策略；变化会转 BLOCKED/ALREADY_EXITED。

#### `ProcessActionService.execute(...)`

消费双确认，持久化 VALIDATING，再次解析身份/策略，核对目标和分类，写 mandatory started
审计，构造 `ExecutionAuthorization` 并通过 registry 调用唯一动作工具。验证结果映射到明确
终态并审计。工具异常在可能开始写后标 UNKNOWN；不猜测完成。

#### 编排私有辅助函数

`_arguments` 生成 exact typed 工具参数并让窗口成员优先；`_revalidate_targets` 检查组集合或
单 PID 身份；`_tool_name` 只映射两个注册名；`_terminal_state` 把成员状态映射事务终态；
`_already_exited_result` 为执行前自然退出生成零写操作的可验证结果。

### Qt 后台与对话框

#### `ProcessPrepareWorker.run()` / `ProcessForcePreviewWorker.run()`

后台创建服务并生成正常/强制 Preview。异常转为 failed signal；不会在 GUI 线程查询 SCM 或
进程。`PreparedProcessAction` 承载 typed 结果。

#### `ProcessRuntimePreviewWorker.run()` / `ProcessExecutionWorker.run()` / `cancel()`

分别后台即时重验证和消费确认执行。`cancel` 设置协作 token，仅停止等待/后续对象。三个
`require_*` 函数校验跨线程 object 类型，错误抛 `TypeError`。

#### `ProcessActionDialog`

构造即启动只读 Preview；`_show_prepared` 只给通过策略的目标请求 PLAN；`_approve_plan` 后台
重验证；`_approve_runtime_and_execute` 才启动工具 Worker；`_completed` 显示逐成员验证；
`_start_force_preview` 创建全新高影响事务；`_cancel_clicked` 按阶段拒绝确认或协作取消；
`closeEvent` 不允许遗留未处理确认/worker；`shutdown` 用于应用退出。Cancel 是默认按钮。

#### GUI 辅助函数与系统诊断入口

`_risk_text`、`_preview_html`、`_result_html` 只格式化已验证对象；
`_force_is_valid_alternative` 仅在所有阻止原因都是 UNSUPPORTED_GRACEFUL_EXIT 时开放强制
Preview。`SystemDiagnosticsTab.open_process_action`、`_selected_process` 和选择槽只传具体
本地查询；`MainWindow._remember_process_reference` 与 `_references_previous_process` 仅在
最近明确 PID 存在时解析“它”，实际身份仍会重新读取。

## Stage 3 Windows 系统只读诊断 API

本节逐项说明 Stage 3 新增或扩展的生产函数。除 Qt 展示方法外，所有入口均可在无 GUI、
无模型的测试中调用。系统采集只通过已注册 R0 工具进入 Windows 查询适配器。

### 领域模型校验函数

#### `DiagnosticPlan.validate_safety_contract()`

在 Pydantic 构造后校验计划必须是 R0、`read_only=True`、需要计划确认且收集器不重复。
返回校验后的同一计划；违约抛出 `ValueError`。它只校验结构，不读取系统。

#### `DiagnosticPlan.canonical_digest()`

将计划以排序键、稳定分隔符的 JSON 编码后计算 SHA-256。摘要覆盖计划 ID、时间、目标、
收集器、采样参数、批量上限和安全字段；任一变化使确认失效。返回 64 字符十六进制字符串。

#### `SuggestedAction.prohibit_execution()`

强制 Stage 3 建议的 `executable` 为 `False`。任何试图把只读报告建议变成执行入口的模型或
调用方会在 Schema 校验时得到 `ValueError`。

#### `DiagnosticNarrativeObservation.reject_numeric_claims(value)`

拒绝模型解释中的任何数字字符，防止供应商重述或编造测量值。返回原字符串；检测到数字抛出
`ValueError`，确定性代码仍是所有数值的唯一来源。

### 平台协议 `SystemDiagnosticsPlatform`

#### `collect_system_info()`

返回 `SystemInfoSnapshot`：Windows edition/release/build、主机名、架构、处理器型号、安装
内存、启动时间和运行秒数。协议不允许产品密钥、凭据或设备秘密。

#### `collect_cpu(sample_count, interval_seconds, cancellation)`

在 2–10 个点、0.1–2 秒间隔内采样总 CPU 与每核心使用率，返回平均、峰值、核心数和频率。
`cancellation` 提供动态取消信号；实现可抛出取消异常，不修改调度或电源设置。

#### `collect_memory()`

返回物理内存、可用/已用、缓存、页面文件以及可用时的 commit 计数。字段不可可靠取得时
使用 `None`，不得猜测。

#### `collect_disks()`

返回 `(DiskSnapshot 元组, 警告元组)`。默认只保留 Windows 本地固定卷，读取文件系统、
总量、已用和剩余；不可访问卷变成警告，不遍历文件内容。

#### `collect_processes(interval_seconds, max_processes, cancellation)`

两次读取之间按指定间隔采样，返回进程元数据、保守名称分组和完整/部分/跳过计数。请求
Schema 中没有命令行选项，返回模型也没有命令行字段。受保护或已退出进程不会使整体崩溃。

#### `collect_startup(max_items)`

以只读注册表视图枚举 HKCU/HKLM `Run`/`RunOnce`（含 32/64 位视图）及用户/公共 Startup
文件夹，返回条目、警告和截断标志。不解析或执行命令，不写注册表。

#### `collect_services(max_items)`

用 SCM 枚举和 `SERVICE_QUERY_CONFIG` 句柄返回服务名称、显示名、状态、启动类型、账户、
可执行路径和可用描述。协议不存在启动、停止、重启、禁用或配置方法。

#### `collect_software(max_items)`

只读 HKCU、HKLM 32 位和 64 位卸载注册表，返回去重后的名称、版本、发布者、安装日期/
位置、估算大小、范围和架构。返回类型没有卸载命令字段。

### Windows 查询适配器辅助函数

#### `_utc_from_timestamp(value)`

把 Unix 时间戳转换为带 UTC 时区的 `datetime`。无系统副作用。

#### `_wait_with_cancellation(seconds, cancellation)`

按最多 50 毫秒切片等待采样周期，持续检查取消信号。取消时抛出
`DiagnosticCollectionCancelled`，避免 GUI 在长睡眠中失去响应。

#### `_read_registry_value(hive, key_path, value_name, access=KEY_READ)`

只用 `OpenKey`/`QueryValueEx` 读取单值，缺失或无权访问时返回 `None`。`access` 仅用于选择
32/64 位只读视图，生产调用不传写权限。

#### `_drive_kind(mountpoint)`

把 `GetDriveTypeW` 数值映射成 fixed/removable/network/optical/ramdisk/unknown，供磁盘工具
只分析本地固定卷。

#### `_service_state(value)` / `_service_start_type(value)`

把 pywin32 SCM 常量映射为稳定小写字符串；未知值返回 `unknown`，不猜测含义。

#### `_extract_executable_path(binary_path)`

展开环境变量并从服务 image path 中只提取可执行文件部分，丢弃参数，减少凭据/令牌泄露。
返回 `Path | None`，不检查或启动文件。

### `WindowsSystemDiagnosticsPlatform` 方法

`collect_system_info`、`collect_cpu`、`collect_memory`、`collect_disks`、`collect_processes`、
`collect_startup`、`collect_services`、`collect_software` 分别实现上述协议。实现只使用
`psutil`、Python 标准库、`GetDriveTypeW`、`winreg` 读取与 SCM 查询。它们不创建子进程，
不调用 PowerShell/CMD/WMI/`Win32_Product`，不提权，也不调用进程/服务/注册表写 API。

### 工具清单与八个注册工具

#### `_manifest(...)`

为系统工具构造完整 `ToolManifest`：R0、只读、需要计划确认、不需要运行时 R2 确认、回滚
`NONE`、普通用户查询权限、Windows 平台、30 秒超时和批量上限。返回不可变清单。

#### `SystemInfoTool.__init__(platform)` / `manifest` / `execute(request, cancellation)`

构造 `system.info` 并注入平台；`manifest` 返回清单；`execute` 只接受
`EmptyCollectorRequest`，否则 `TypeError`，成功返回 `SystemInfoResult`。

#### `CpuTool.__init__(platform)` / `manifest` / `execute(...)`

注册 `system.cpu`。`execute` 只接受 `CpuCollectorRequest`，把边界内采样次数、间隔和取消
信号交给平台，返回 `CpuResult`。

#### `MemoryTool.__init__(platform)` / `manifest` / `execute(...)`

注册 `system.memory`。空参数查询并返回 `MemoryResult`。

#### `DiskTool.__init__(platform)` / `manifest` / `execute(...)`

注册 `system.disks`。空参数查询，保留不可访问卷警告，返回 `DiskResult`。

#### `ProcessTool.__init__(platform)` / `manifest` / `execute(...)`

注册 `system.processes`。验证采样间隔与进程上限后返回 `ProcessResult`；不接收命令行开关。

#### `StartupTool.__init__(platform)` / `manifest` / `execute(...)`

注册 `system.startup`。验证 `max_items` 后返回 `StartupResult` 及截断状态。

#### `ServiceTool.__init__(platform)` / `manifest` / `execute(...)`

注册 `system.services`。验证上限后通过查询型平台接口返回 `ServiceResult`。

#### `SoftwareTool.__init__(platform)` / `manifest` / `execute(...)`

注册 `system.software`。验证上限后返回不含卸载命令的 `SoftwareResult`。

### 诊断确认

#### `DiagnosticConfirmationService.__init__(ttl_seconds=300, now=None)`

建立内存确认状态机。`now` 可注入以测试过期；保存的是计划摘要和到期时间，不保存系统快照。

#### `request(plan)`

生成绑定 plan ID、canonical digest、收集器摘要和有效期的 `PLAN` 确认。返回
`ConfirmationRequest`，不执行工具。

#### `resolve(confirmation_id, approved, plan)`

验证确认存在、尚未处理、未过期、计划 ID 与摘要未变，再写入批准/拒绝状态。未知、复用、
过期或变更抛出 `DiagnosticConfirmationError`。

#### `require_approved(plan)`

执行前再次要求摘要一致且批准仍未过期；否则抛出 `DiagnosticConfirmationError`。

### 计划与安全审查

#### `classify_diagnostic_intent(user_goal)`

用有限中英文关键词把目标分为 overview/performance/cpu/memory/disks/processes/startup/
services/software。未命中返回 overview，不访问模型或系统。

#### `is_diagnostic_request(user_goal)`

判断聊天文本是否明确包含系统诊断概念，供 UI 路由。返回布尔值，不把普通文件请求误当系统查询。

#### `extract_software_search_term(user_goal)`

从“电脑上安装了哪些 Adobe 软件”等已确定的软件清单问题中去掉固定功能词，返回可选的本地
名称/发布者筛选词；不调用模型、不猜测软件类别，空泛“查看软件清单”返回 `None`。

#### `DiagnosticPlanCompiler.__init__(registry, sample_count=3, sample_interval_seconds=1.5, max_processes=500, max_items=5000)`

注入注册表和集中限制。这些默认值形成约 4.5 秒 CPU 观察窗口，均受领域 Schema 上限约束。

#### `local_draft(user_goal)`

根据本地分类器返回有限 `DiagnosticIntentDraft`。模型未配置或未获外发同意时使用此路径。

#### `compile(user_goal, draft)`

把不可信 draft 与意图允许集合求交，自动加入 system.info，检查每个清单均已注册且为 R0
只读，最后生成不可变计划。越权收集器或未知工具抛出异常。

#### `DiagnosticSafetyValidator.__init__(registry)`

保存唯一的注册工具边界，无副作用。

#### `review(plan)`

独立检查 R0、只读、零修改、`NONE` 回滚、计划确认、无重复收集器、工具存在、参数 Schema、
清单风险和确认语义。返回包含全部问题的 `DiagnosticSafetyReview`；不自动修正危险计划。

#### `arguments_for(plan, collector)`

只从已校验计划派生 CPU、进程和有限枚举参数；无参数工具返回空字典。模型不能提供参数。

### 确定性诊断引擎

#### `_review_action(title, description)`

构造 `executable=False` 的 R0 REVIEW 建议。

#### `DiagnosticEngine.__init__(thresholds=None)`

注入集中、可测试且会写入报告的 `DiagnosticThresholds`；省略时用保守默认值。

#### `analyze(plan, snapshot)`

调用所有可用类别规则，汇总失败收集器数与 finding 数，返回含实际阈值和免责声明的报告。

#### `_cpu_findings(snapshot)`

只根据多样本平均/峰值产生持续 CPU 观察，可信度 MEDIUM；瞬时单点不会独立触发。

#### `_memory_findings(snapshot)`

按已用百分比和可用字节形成内存观察，并明确缓存/工作负载可能改变含义。

#### `_disk_findings(snapshot)`

同时要求使用率和绝对剩余字节达到阈值后才形成 finding，避免大容量盘仅凭百分比误报。

#### `_process_findings(snapshot)`

按本地测量找出达到 CPU 或内存观察阈值的进程并确定性排序；不称其恶意且不提供终止动作。

#### `_startup_findings(snapshot)`

仅按条目数量给出 LOW 可信度复核提示，明确数量不能证明启动问题。

### 快照采集与编排

#### `SystemSnapshotService.__init__(registry, audit)`

注入唯一工具执行入口和隐私最小化审计器。

#### `collect(plan, cancellation)`

在写前审计后用最多四个线程并发执行独立 R0 收集器，再按计划顺序组装；取消后把未启动项
标记 CANCELLED。单个异常由 `_collect_one` 转成 outcome，其他收集器继续。

#### `_collect_one(plan, collector, cancellation)`

在线程池中通过注册表执行并测量耗时，成功生成 SUCCEEDED/PARTIAL，异常只生成类型化安全
消息和 FAILED/CANCELLED。为避免 SQLite 并发写争用，调用方 `collect` 在主编排线程先写全部
started 事件，再按计划顺序写 completion 事件。

#### `_item_count(result)`

按输出 Schema 确定性计算审计条目数，不展开原始对象。

#### `_assemble(collected, outcomes)`

类型收窄八种工具结果并组装可选字段。失败类别保持空值，其 outcome 仍保留。

#### `DiagnosticOrchestrator.__init__(...)`

注入编译器、安全器、确认器、快照服务、引擎和审计器，保持每个边界可替换测试。

#### `prepare(user_goal)`

本地生成计划、执行独立审查并审计结果，返回 `(plan, review)`；尚不读取系统。

#### `request_confirmation(plan)`

重新安全审查，通过后创建确认；被篡改计划抛出 `ValueError`。

#### `resolve_confirmation(confirmation_id, approved, plan)`

委托确认状态机处理并审计用户决定。

#### `execute(plan, cancellation=None)`

执行时重新审查、要求有效批准、采集快照、运行确定性引擎并写报告审计。没有批准、审计失败、
安全失败均阻止工具；收集器访问失败则形成部分报告。

### 审计函数

`DiagnosticAuditLogger.__init__(repository, git_commit=None)` 注入 SQLite 审计和可选提交号。
`plan_reviewed` 记录计划/审查；`confirmation_resolved` 记录决定；`collector_started` 提供
写前证据；`collector_completed` 只记状态、计数、警告数、缓存标记、错误类型和耗时；
`report_completed` 只记报告 ID、finding/collector/失败数量及零修改验证。均不保存进程、
启动命令、服务或软件清单。

### 模型供应商边界

#### `LLMProvider.create_diagnostic_intent(request)` / `explain_system_diagnostics(request)`

供应商中立的异步扩展点。前者只能返回有限 `DiagnosticIntentDraft`；后者只能返回绑定 finding
code 的定性叙述。基类默认抛出 `NotImplementedError`，所以旧供应商不会被误认为支持 Stage 3。

#### `DiagnosticProviderPlanner.__init__(provider, compiler, consent)`

注入可替换模型、确定性编译器和外发确认。

#### `build_request(user_goal)` / `request_consent(user_goal)`

前者只生成用户目标及有限意图/收集器清单；后者为准确 payload 建立摘要确认。均不含快照。

#### `plan(user_goal, confirmation_id)`

要求外发批准后调用 `create_diagnostic_intent`，再由本地编译器限制模型输出。返回计划和 trace。

#### `DiagnosticExplainer.__init__(provider, consent)`

注入模型与外发确认，不持有系统读取接口。

#### `build_request(plan, report)` / `request_consent(plan, report)`

删除数值和对象身份，仅保留最多 20 个 finding 的 code/category/severity/title/evidence 字段名，
随后建立准确 payload 的外发确认。

#### `explain(plan, report, confirmation_id)`

确认后请求定性解释，并拒绝引用不存在 finding code 的结果。叙述 Schema 还拒绝所有数字声明。

#### `OpenAILLMProvider.create_diagnostic_intent(request)`

使用 Responses 结构化解析为 `DiagnosticIntentDraft`；API 错误和类型错误被包装为不含密钥的
`OpenAIProviderError`。

#### `OpenAILLMProvider.explain_system_diagnostics(request)`

解析 `DiagnosticNarrativeDraft`，校验所有 finding code 在请求中，拒绝未知引用和数字断言。

### Qt 后台与 Dashboard

#### `DiagnosticWorker.__init__(orchestrator, plan)` / `run()` / `cancel()`

保存已确认计划和独立取消令牌；`run` 在线程池执行并发射 completed/failed 信号；`cancel`
只设置协作信号，不强杀线程或 OS 查询。

#### `require_diagnostic_report(value)`

收窄 Qt `object` 信号为 `DiagnosticReport`，类型不符抛出 `TypeError`。

#### `_bytes_text(value)`

把字节显示为 B/KiB/MiB/GiB/TiB，不改变原始模型值。

#### `SystemDiagnosticsTab.__init__(runtime)` / `_build_ui()` / `_table(headers)`

构建目标输入、快捷计划、风险/确认、进度、摘要、筛选和六个只读结果表。UI 不直接调用工具。

#### `_plan_clicked()` / `start_planning(goal=None)`

从按钮/聊天进入本地编译与安全审查，显示 JSON 计划并创建确认；不在此阶段读取系统。

#### `_approve()` / `_reject()`

把用户决定交给确认状态机，只有批准后启用运行按钮。

#### `_run()` / `cancel()` / `_completed(value)` / `_failed(message)`

启动后台 worker、协作取消、类型校验完成结果或呈现友好错误；主线程不等待 CPU/进程采样。

#### `_populate(report)` / `_fill(table, rows)` / `_filter_tables(text)`

分别渲染报告、填表和对当前可见数据本地筛选。进程按内存排序；这些函数只更新 Qt 控件。

#### `shutdown()` / `_show_error(message)`

退出时请求取消；错误用对话框和状态信号显示，不伪造完成结果。

### 运行时扩展

#### `ApplicationRuntime.create_system_diagnostic_services()`

创建 Windows 查询适配器、注册八个 R0 工具、配置编译/安全/确认/快照/引擎/审计并返回依赖包。
若模型启用，再添加 consent-gated planner/explainer；模型始终不是系统查询依赖。

## Stage 2B Windows Recycle Bin API（新增）

本节逐项说明 Stage 2B 新增或扩展的生产函数。所有路径均为不可信输入；只有
`TrashService.execute` 在两级确认、SQLite 证明和执行前复验全部通过后才可能调用
Windows Shell。

### `domain.trash._digest_model(value)`

把不可变 Pydantic 模型按键排序并序列化为 JSON，再计算 SHA-256。用于绑定计划、
Preview、确认与恢复记录；不读取文件。序列化失败会传播异常。

### `RecycleBinCapability.validate_capability()`

校验卷能力声明。`available=True` 只有在本地固定、可写、非热插拔、NTFS 且回收站查询
成功时合法；不可用结论必须带原因，防止适配器用含糊的“可用”绕过安全层。

### `TrashPlanItem.validate_item()`

确保文件使用 `RECYCLE_FILE`、目录使用 `RECYCLE_DIRECTORY`，源路径等于身份快照路径，
工具固定为 `file.trash`，恢复等级固定为 MANUAL。

### `TrashPlan.validate_plan()` / `TrashPlan.canonical_digest()`

前者要求至少一个授权根和明确对象、R2、计划确认、即时确认、MANUAL 恢复、连续顺序以及
唯一对象/路径。后者生成包含全部执行字段的稳定摘要；计划任一变化都会改变摘要。

### `TrashObjectSnapshot.canonical_digest()`

散列根身份、递归树摘要、数量、大小和属性统计，用于发现目录在 Preview 后新增、删除或
替换内容。

### `TrashPreviewItem.validate_status()`

READY 必须同时具备对象快照和已证明的卷能力，且不能含阻止原因；BLOCKED 必须解释原因。

### `TrashPreview.validate_totals()` / `TrashPreview.canonical_digest()`

重新计算选择数、READY/BLOCKED、目录内对象数、总大小和最大对象，拒绝 UI 或持久化层
伪造统计；随后为完整 Preview 生成确认摘要。

### `TrashRecoveryRecord.canonical_digest()`

散列原路径、原身份、Shell 结果、MANUAL 状态和恢复说明，用于读取 SQLite 时检测损坏或
篡改。它不提供自动恢复。

### `TrashPathPolicy.__init__(base_policy, extra_protected_roots=())`

在已有授权策略上叠加 Windows、Program Files、ProgramData、AppData 和调用方指定保护根。
构造只建立规则，不读取文件。

### `TrashPathPolicy.approved_roots`

返回基础策略的不可变授权根，仅用于编译器/界面显示；返回值不会扩大授权。

### `TrashPathPolicy.validate_source(path)`

验证绝对、已授权、现存普通文件/目录，拒绝授权根自身、系统/应用数据、重解析点、SYSTEM
和 OFFLINE 对象。返回规范路径；违反边界时抛出 `PathSecurityError`。

### `TrashPathPolicy.entry_rejection_reason(path)`

为递归目录中的每个后代调用 `validate_source`，把异常转换为稳定阻止原因。无问题返回
`None`；不会跟随链接。

### `TrashPreviewEngine.__init__(...)`

注入 R2 路径策略、身份适配器和回收站能力适配器，并验证选择数、目录对象数、字节和高影响
阈值均为正数。

### `TrashPreviewEngine.generate(plan, transaction_id=None)`

逐个验证路径、身份、完整目录树和卷能力，保留每个 BLOCKED 项，计算影响和对象集合摘要。
只读；不会调用 `recycle()`。超过任何硬上限时所有原 READY 项改为 BLOCKED。

### `TrashPreviewEngine.require_unchanged(plan, preview)`

第二次确认前及执行前重新生成快照，比较事务、对象集合、状态、数量和总大小。任何变化抛出
`PathSecurityError`，要求重新生成计划和两次确认。

### `TrashPreviewEngine.snapshot(source, kind)`

使用显式栈递归枚举，不跟随链接；为相对路径、类型、卷序列、File ID、大小、时间和属性生成
稳定树摘要，同时统计隐藏/系统/重解析/offline 项。达到对象或字节上限立即拒绝。

### `TrashPreviewEngine._object_set_digest(items)`

将操作 ID、路径、状态、快照摘要、能力结果和问题列表合并为选择集合摘要；供确认链绑定。

### `TrashSafetyValidator.__init__(registry, policy, max_selected=100)`

保存注册表和 R2 策略，并拒绝非正批量上限；没有系统副作用。

### `TrashSafetyValidator.review(plan)`

独立检查 R2/MANUAL/双确认、`file.trash` 清单、路径、操作类型以及父子重叠选择。任何问题令
`SafetyReview.approved=False`，不会尝试“修正”危险计划。

### `classify_trash_intent(text)`

纯本地确定性路由。永久删除、跳过/清空回收站词句返回
`PROHIBITED_PERMANENT_DELETE`；明确回收站意图返回 `RECYCLE_BIN`；其他返回 `NONE`。
返回值不含路径或工具参数。

### `TrashPlanCompiler.__init__(authorized_paths, policy, identity_platform, max_selected=100)`

注入授权服务、R2 路径策略和身份读取器；拒绝非正上限。模型供应商不是依赖项。

### `TrashPlanCompiler.compile(user_goal, selected_paths, authorized_root_ids)`

只接受用户明确传入的路径，验证它们位于本计划指定授权根内，去重、读取身份并生成连续的
`TrashPlanItem`。空目标、空选择、超限、越界或重复均抛异常；不执行写操作。

### `TrashConfirmationService.__init__(plan_ttl_seconds=300, runtime_ttl_seconds=60, now=None)`

创建内存一次性确认库。计划与即时确认分别使用有效期；可注入时钟便于测试。非正 TTL
抛出 `ValueError`，进程重启会自然丢失未消费能力。

### `request_plan(plan, preview)` / `resolve_plan(...)`

第一项要求 Preview 与计划完全匹配且所有对象 READY，然后绑定事务、计划/Preview/对象摘要、
数量、大小和到期时间。第二项只解析仍待处理且未过期的同一请求。

### `request_runtime(plan_confirmation_id, plan, revalidated_preview)`

只允许从已批准、未过期的 PLAN 确认和同一事务/对象集合创建短期 RUNTIME 请求。新的 Preview
ID 可以变化，但对象集合、数量和大小不得变化。

### `resolve_runtime(...)` / `consume_runtime(...)`

前者记录第二次用户决定；后者在事务进入 RUNNING 时消费一次，并同时消费父 PLAN 能力。
错误层级、拒绝、过期、重放或摘要变化抛 `TrashConfirmationError`。

### `TrashConfirmationService._create(...)`

构造并保存不可变确认对象，记录父确认、所有摘要、数量、大小和到期时间。

### `_resolve(...)` / `_get(...)` / `_require_not_expired(...)`

内部状态机辅助：查找请求、验证层级/状态、更新批准或拒绝，并把到期请求改为 EXPIRED。
未知 ID 和缺失父确认默认拒绝。

### `_require_executable(plan, preview)` / `_require_current(...)`

前者要求所有选择 READY；后者比较事务、计划、摘要、对象集合、数量与总大小。它们是确定性
权限检查，不接受 UI 自报状态。

### `WindowsRecycleBinPlatform.__init__()`

仅 Windows 可构造，加载 `kernel32` 和 `shell32` Unicode API。非 Windows 抛 `OSError`。

### `_hresult_succeeded(value)`

按 COM `SUCCEEDED` 规则检查 HRESULT 的最高位。它接受 `S_OK` 和
`COPYENGINE_S_DONT_PROCESS_CHILDREN` 等信息型成功状态，拒绝所有失败位为 1 的值；避免把
pywin32/Windows Shell 的合法成功码误判为错误。

### `WindowsRecycleBinPlatform.capability(path)`

调用卷根、驱动类型、文件系统/只读标志和 `SHQueryRecycleBinW`。第一版只允许 Windows 系统
卷上的固定、可写、NTFS；其他卷的热插拔状态记为 UNKNOWN 并拒绝。检查失败返回带原因的
`available=False`，不会尝试回收。

### `WindowsRecycleBinPlatform.recycle(path)`

再次检查能力和路径，在线程中初始化 STA COM，创建一次性 `IFileOperation`，只排队一个
`DeleteItem`，设置 RECYCLEONDELETE/ADDUNDORECORD/EARLYFAILURE/WANTNUKEWARNING 等标志。
成功必须同时满足操作 HRESULT 和逐项 HRESULT 的 COM 成功语义、未中止、回收传输标志、非空回收站 Shell
对象以及原路径消失；否则返回 FAILED/UNKNOWN 或抛 `RecycleBinPlatformError`。不存在旧 API、
命令行或永久删除回退。

### `_RecycleProgressSink.__init__()`

初始化 COM 包装和结果字段，不执行文件操作。

### `_RecycleProgressSink.PreDeleteItem(flags, item)`

只有 Shell 声明 `TSF_DELETE_RECYCLE_IF_POSSIBLE` 才返回 S_OK，否则返回 E_FAIL 取消该项。

### `_RecycleProgressSink.PostDeleteItem(flags, item, hr_delete, newly_created)`

保存真实逐项 HRESULT；仅当 `newly_created` 非空时保存回收站 Shell 标识。空值意味着可能被
完全删除，因此绝不标记 VERIFIED。

### `_RecycleProgressSink.StartOperations/FinishOperations/UpdateProgress/ResetTimer/PauseTimer/ResumeTimer`

满足 COM 协议的无副作用回调；返回 S_OK。进度仅由上层事务按对象更新。

### `_RecycleProgressSink.PreRenameItem(...)` / `PostRenameItem(...)`

重命名不属于回收工具，两个回调均返回 E_FAIL，防止同一回调对象意外批准重命名。

### `_RecycleProgressSink.PreMoveItem(...)` / `PostMoveItem(...)`

普通移动不属于回收工具，两个回调均返回 E_FAIL；回收只能来自已排队的 `DeleteItem`。

### `_RecycleProgressSink.PreCopyItem(...)` / `PostCopyItem(...)`

复制不属于回收工具，两个回调均返回 E_FAIL，确保此适配器永远不会复制用户数据。

### `_RecycleProgressSink.PreNewItem(...)` / `PostNewItem(...)`

新建对象不属于回收工具，两个回调均返回 E_FAIL，限制 COM 进度接收器的批准范围。

### `WindowsRecycleBinPlatform._volume_root(path)`

使用 `GetVolumePathNameW` 取得真实卷根；失败抛 Windows 异常。

### `_volume_information(root)` / `_query_recycle_bin(root)`

前者返回文件系统名、卷序列和标志；后者通过公开 Shell API 查询回收站而不直接读取
`$Recycle.Bin`。任一失败使能力不可用。

### `_is_hotplug_or_unknown(root)`

系统卷返回 `False`；其他卷在尚未完成可靠设备 IOCTL 分类前返回 `None`，从而失败关闭。

### `TrashTool.__init__(policy, identity_platform, recycle_platform, snapshotter)`

注入全部确定性依赖，不持有任意命令执行器。

### `TrashTool.manifest`

返回 `file.trash` 清单：R2、非只读、非幂等、支持 Preview/对象间取消、MANUAL、普通用户、
Windows、批量 1，且同时要求计划和即时确认。

### `TrashTool.execute(request, cancellation)`

校验 Schema，检查取消，重新验证路径、根身份和完整目录快照，再调用一次 `recycle()`。
平台的 FAILED/UNKNOWN 结果原样交给事务服务处理，不能静默当作成功。

### `build_trash_arguments(plan, preview)`

为每个对象生成并序列化 `TrashRequest`，包含源路径、最新根身份和完整目录快照。缺少快照时
失败，不生成部分授权。

### `TrashService.__init__(validator, preview_engine, confirmations, repository, registry, audit)`

组合但不合并安全审查、Preview、确认、SQLite、注册表和审计边界，便于独立测试和故障注入。

### `TrashService.prepare(plan)`

执行独立审查和只读 Preview，要求全部 READY，持久化事务/参数预约，转入
AWAITING_CONFIRMATION，创建并持久化 PLAN 请求，最后审计 Preview。任何失败都不会写用户文件。

### `resolve_plan_confirmation(prepared, approved)`

解析第一层决定并持久化。拒绝转 CANCELLED；批准只转 AWAITING_RUNTIME_CONFIRMATION，仍不能
执行工具。

### `request_runtime_confirmation(prepared)`

验证事务状态，重新扫描完整对象集合，然后创建/持久化第二层待确认请求。变化或第一层未批准
时失败。

### `resolve_runtime_confirmation(runtime, approved)`

解析即时决定。批准转 CONFIRMED，拒绝转 CANCELLED；记录审计和确认 ID/时间。

### `TrashService.execute(runtime, cancellation=None)`

一次性消费 RUNTIME，持久化 CONSUMED 证明，第三次复验，转 RUNNING。每个对象先写 PREPARED
恢复和 started 审计，再用含 runtime confirmation ID 的能力调用注册表。验证成功升级为
AVAILABLE/MANUAL；含糊为 UNKNOWN；异常为 FAILED；任一非成功停止后续对象。支持对象之间取消。

### `TrashService._report(...)` / `recovery_records(transaction_id)`

前者从持久事务生成终态报告；后者返回逐条校验摘要的 MANUAL 恢复记录供 GUI 展示。

### `OperationRepository.create_trash_from_preview(...)`

在共享事务表中原子写入 Stage 2B Preview 和参数摘要；源路径同时作为 source-only 操作的唯一
subject 预约，不代表目标路径。

### `record_confirmation(...)`

向加法表写入或更新 PLAN/RUNTIME 层级、状态、绑定摘要和确认时间；ID、事务或层级变化即拒绝。

### `begin_trash_operation(...)`

要求事务 RUNNING、项 PENDING，并在任何 Shell 调用前原子写入 PREPARED
`TrashRecoveryRecord`，随后把项改为 RUNNING。

### `complete_trash_operation(...)`

校验 recovery/operation ID，把 AVAILABLE 映射为 COMPLETED、UNKNOWN 映射为 UNKNOWN、其他映射
为 FAILED，并同时更新事务计数和恢复摘要。

### `get_recovery(operation_id)` / `list_recovery(transaction_id)`

读取后重新计算摘要，损坏即抛 `OperationStoreError`。单项按 ID 查询，列表按原执行顺序返回。

### `OperationRepository._mark_running_trash_unknown(session, transaction_id, current)`

启动恢复时把仍 RUNNING 的 `file.trash` 项及 PREPARED 记录改为 UNKNOWN，记录人工检查原因；
绝不自动重试可能已完成的 Shell 操作。

### `require_execution_authorization(...)`（Stage 2B 扩展）

除既有事务/计划/Preview/工具/参数摘要外，`file.trash` 还必须携带 runtime confirmation ID，且
SQLite 中存在同事务已批准 PLAN 和已消费 RUNTIME 记录，否则注册表拒绝调用平台。

### `TrashAuditLogger.__init__(repository, app_version, git_commit)`

保存审计仓库和版本追踪字段；不缓存文件内容。

### `previewed(plan, preview)` / `confirmation_resolved(plan, confirmation)`

分别记录无写入的 R2 Preview，以及 PLAN/RUNTIME 决定及全部摘要、数量和总大小。日志不含文件
正文或密钥。

### `item_started(plan, item, recovery, runtime_confirmation_id)`

在 Shell 前写 mandatory audit；数据库不可用时抛错，从而平台函数不会被调用。

### `item_result(plan, item, result, recovery, error=None)`

记录 HRESULT、回收证据、验证状态、MANUAL recovery ID 和脱敏错误；不声称自动恢复。

### `ApplicationRuntime.create_trash_services(progress_callback=None)`

为当前授权根构造 R2 策略、事务守卫、Preview、`file.trash`、验证器、编译器、审计和服务。
模型供应商不会进入依赖图；没有授权根时失败。

### `ApplicationRuntime.audit_prohibited_request(original_request, reason)`

把永久删除、绕过或清空回收站等 R4 请求记为 `trash.request_refused`。记录明确说明没有执行、
没有注册相关工具；不调用模型或平台。审计数据库不可用时异常传播给 UI，拒绝本身仍保持生效。

### `RecycleBinPlatform.capability(path)` / `RecycleBinPlatform.recycle(path)`

平台协议：前者只读并失败关闭；后者只允许移动一个对象到回收站并返回验证证据。跨平台实现
不得增加永久删除回退。

### `ToolManifest.__post_init__()`（Stage 2B 扩展）

R2 工具必须 `requires_runtime_confirmation=True`；非 R2 工具不得声明该字段，防止错误确认语义。

### `TrashPreviewWorker.run()` / `TrashExecutionWorker.run()`

前者在线程中生成/持久化 Preview，不执行写；后者只运行已完成两次确认的事务。所有异常通过
Qt failed 信号传回，不在工作线程吞掉。

### `TrashExecutionWorker.cancel()`

设置协作取消令牌，只停止后续对象；已开始的 Shell 原子调用不被强制终止。

### `require_prepared_trash(value)` / `require_trash_report(value)`

收窄 Qt `object` 信号值；类型不符抛 `TypeError`，防止 UI 把任意对象当作安全服务结果。

### `FileAnalysisTab._request_trash_selected()`

读取当前页明确勾选的结果；空选择提示错误，否则只把路径元组发给 Stage 2B 页面，不直接调用
工具。

### `MainWindow._build_trash_tab()` / `_open_trash_for_paths(value)`

前者注册独立标签页及状态信号；后者过滤为 `Path` 元组并转交，不执行计划或写操作。

### `TrashTab.set_sources(paths)`

替换待处理列表并使旧 Preview 和两级确认失效；只更新界面。

### `TrashTab._add_files()` / `_add_directory()` / `_clear_sources()`

维护用户明确选择。文件对话框结果仍必须经过编译器和 R2 策略；清空会使确认失效。

### `TrashTab._prepare()` / `_preview_completed(value)`

前者确定性编译并启动只读 worker；后者验证类型、显示逐项对象/大小/属性/阻止原因，并只启用
第一确认按钮。

### `TrashTab._confirm_plan()`

显示数量和总大小，默认按钮为取消。批准后调用服务解析第一确认并重新验证；不会执行平台。

### `TrashTab._confirm_runtime()`

显示即时、对象特定的 R2 对话框，明确 MANUAL 恢复，默认取消。批准才启动执行 worker。

### `TrashTab._execution_completed(value)`

显示事务计数、原路径、恢复状态和 Windows“还原”步骤；不显示自动 Undo。

### `TrashTab.cancel()` / `shutdown()`

请求停止后续对象；应用退出复用同一协作取消，不强制杀死线程。

### `TrashTab._source_paths()` / `_all_root_ids()` / `_invalidate()`

分别读取 UI 路径、当前授权 root ID，以及清除 Preview/两级确认。它们不授予新路径权限。

### `TrashTab._worker_failed(message)` / `_show_error(message)`

恢复按钮/进度状态并向初级用户展示“未执行”错误；不会忽略异常或继续下一项。

本文档说明 `src/pc_manager_agent` 当前生产代码中的全部函数和方法，包括公开接口、
私有辅助方法、抽象协议方法、Qt 槽函数以及函数内部的回调。测试辅助函数不属于生产
API，因此不在本文档范围内。

当前项目仍处于 MVP 0.1 阶段；没有下划线前缀的接口也不代表已经承诺长期兼容。
名称以下划线开头的函数、方法或协议仅供模块内部使用，调用方不应直接依赖。

## 通用约定

- 所有路径使用 `pathlib.Path`；安全判断由 `PathPolicy` 和执行前复验负责。
- Pydantic 模型默认拒绝未知字段，计划、确认和报告模型为不可变对象。
- 时间字段使用带时区的 UTC `datetime`；持续时间使用毫秒。
- 工具只能经 `ToolRegistry` 注册和调用；模型输出不能直接取得执行权限。
- 审计存储不可用时采用失败关闭策略，不继续执行工具。
- `R0` 是只读，`R1` 是低风险可逆操作，`R2` 需要即时确认，`R3` 在 MVP 中禁止
  执行，`R4` 始终拒绝。

## 主要数据模型

| 模型 | 主要字段 | 用途 |
| --- | --- | --- |
| `AppSettings` | 模型供应商、数据目录、扫描上限、超时、确认有效期 | 经过校验的运行时配置 |
| `TaskScope` | `included_paths`、`excluded_paths` | 定义计划允许和排除的路径边界 |
| `PlanStep` | 工具名、参数、风险、确认和回滚声明 | 描述一次确定性的工具调用 |
| `TaskPlan` | 目标、范围、步骤、影响估计、确认要求 | 安全审查和用户确认的不可变计划快照 |
| `ScanRequest` | 根目录、排除目录、文件上限、超时 | `file.scan` 的严格输入模型 |
| `FileMetadata` | 路径、名称、类型、大小和时间字段 | 不读取文件正文的元数据记录 |
| `ScanIssue` | 代码、消息、可选路径 | 表示被跳过对象或非致命文件系统错误 |
| `ScanSummary` | 数量、大小、取消、超时、截断和耗时 | 扫描结果汇总 |
| `ScanReport` | 根目录、文件、问题、汇总 | 完整只读扫描输出 |
| `ConfirmationRequest` | 计划/参数摘要、对象说明、到期时间、状态 | 与不可变计划快照绑定的确认请求 |
| `AuditEvent` | 计划、工具、确认、结果、错误、回滚和追踪字段 | 写入前会脱敏的结构化审计事件 |
| `PlannerRequest` | 用户目标、允许/排除路径、允许工具 | 调用方明确同意发送给模型的数据 |
| `ProviderPlanResult` | 已校验计划、供应商、请求 ID | 与供应商无关的规划结果 |
| `ToolManifest` | Schema、风险、权限、上限、回滚、平台等 | 工具注册前必须具备的不可变安全清单 |
| `ReviewIssue` / `SafetyReview` | 拒绝代码、消息、步骤、最终结论 | 安全审查的机器可读结果 |
| `UndoRecord` | 操作 ID、路径、标识、元数据、有效条件 | 写操作未来使用的真实回滚记录 |

## `pc_manager_agent.app.runtime`

### `pc_manager_agent.app.runtime.ApplicationRuntime.__init__`

```python
ApplicationRuntime(settings: AppSettings) -> None
```

- **作用：** 组合应用级依赖。保存已经校验的设置，创建并初始化
  `AuditRepository`，再按配置的有效期创建共享 `ConfirmationService`。
- **参数：** `settings` 必须是完整的 `AppSettings`；其中的 `database_path` 决定
  SQLite 审计库位置。
- **返回与异常：** 不返回值。数据库建表或健康查询失败时透传
  `AuditUnavailableError`，应用应停止初始化。
- **副作用与安全：** 可能创建应用数据目录和 SQLite 文件；不会连接模型，也不会扫描
  用户目录。审计初始化失败时采用失败关闭。

### `pc_manager_agent.app.runtime.ApplicationRuntime.create_scan_orchestrator`

```python
create_scan_orchestrator(root: Path) -> ScanOrchestrator
```

- **作用：** 针对用户本次选择的单一根目录，创建独立的 `PathPolicy`、工具注册表、
  `DirectoryScannerTool`、`SafetyReviewer` 和 `ScanOrchestrator`。
- **参数：** `root` 是准备授权的扫描根目录；这里只建立范围对象，真正计划和执行时仍会
  再次验证路径。
- **返回：** 返回只拥有 `file.scan` 工具权限的编排器。
- **安全：** 每次调用都创建新的根目录限定注册表，避免旧任务的路径权限泄漏到新任务。

### `pc_manager_agent.app.runtime.ApplicationRuntime.create_llm_provider`

```python
create_llm_provider() -> LLMProvider | None
```

- **作用：** 根据设置构造模型供应商适配器。`disabled` 返回 `None`；`openai` 返回
  `OpenAILLMProvider`。
- **返回与异常：** 未启用供应商时返回 `None`。OpenAI 缺少模型名或 API Key、或者供应商
  名称不受支持时抛出 `ProviderConfigurationError`。
- **安全：** 只有显式配置才创建云端客户端；API Key 从 `SecretStr` 中临时读取，不写日志。
  创建适配器本身不会发起网络请求。

### `pc_manager_agent.app.runtime.ApplicationRuntime.close`

```python
close() -> None
```

- **作用：** 关闭应用级持久化资源，目前委托 `AuditRepository.close()` 释放连接池。
- **返回：** 无。
- **调用要求：** 正常退出和测试清理都应调用；它不删除数据库或审计记录。

## `pc_manager_agent.audit.redaction`

### `pc_manager_agent.audit.redaction.is_sensitive_key`

```python
is_sensitive_key(key: str) -> bool
```

- **作用：** 对键名进行不区分大小写的规范化，判断是否包含 `api_key`、`token`、
  `password`、`cookie`、`session` 等敏感片段。
- **参数与返回：** 输入任意字符串键名；疑似凭据字段返回 `True`，否则返回 `False`。
- **注意：** 这是保守的名称启发式判断，允许误报以降低凭据写入日志的风险。

### `pc_manager_agent.audit.redaction.redact_text`

```python
redact_text(value: str) -> str
```

- **作用：** 在自由文本中查找形如 `token=...`、`password:...` 的内联赋值，并把值替换为
  `[REDACTED]`。
- **参数与返回：** 返回新的脱敏字符串，不修改原字符串。
- **限制：** 它只处理明显的键值形式，不应把它当作任意文档内容的完整隐私识别器。

### `pc_manager_agent.audit.redaction.redact_json`

```python
redact_json(value: JsonValue) -> JsonValue
```

- **作用：** 递归遍历 JSON 字典和列表。敏感键的值整体替换为 `[REDACTED]`；普通字符串
  继续交给 `redact_text()`；数字、布尔值和 `None` 原样保留。
- **返回：** 保持输入 JSON 结构形状的新值。
- **安全：** 是结构化审计写入前的统一脱敏入口，不负责修改源事件对象。

## `pc_manager_agent.audit.repository`

### `pc_manager_agent.audit.repository.AuditRepository.__init__`

```python
AuditRepository(database_path: Path) -> None
```

- **作用：** 创建 SQLite SQLAlchemy 引擎和会话工厂，但尚未建立表或宣布仓库可用。
- **参数：** `database_path` 是应用本地状态数据库的完整路径。
- **副作用：** 引擎创建过程会确保父目录存在；`_initialized` 初始为 `False`。

### `pc_manager_agent.audit.repository.AuditRepository.initialize`

```python
initialize() -> None
```

- **作用：** 创建 `audit_events` 表，并执行 `SELECT 1` 验证数据库连接和 SQLite 配置可用。
- **异常：** SQLAlchemy 操作失败时包装成 `AuditUnavailableError`，原异常保留为异常链。
- **安全：** 只有全部成功才把仓库标记为已初始化；失败后高风险流程不得继续。

### `pc_manager_agent.audit.repository.AuditRepository.record`

```python
record(event: AuditEvent) -> None
```

- **作用：** 把一个 `AuditEvent` 转换为关系模型，在转换过程中递归脱敏，然后以单事务追加。
- **参数：** `event` 是不可变结构化事件；调用方应提供真实的计划、确认和执行结果。
- **异常：** 未初始化或事务写入失败时抛出 `AuditUnavailableError`；不会吞掉错误。
- **安全：** 不执行更新或覆盖；事件写入失败会阻断当前安全工作流。

### `pc_manager_agent.audit.repository.AuditRepository.list_recent`

```python
list_recent(limit: int = 100) -> tuple[AuditEventRow, ...]
```

- **作用：** 按发生时间倒序读取最近的审计行。
- **参数：** `limit` 会被强制限制到 `1..500`，避免界面请求无界数据。
- **返回与异常：** 返回不可变元组；未初始化或查询失败时抛出
  `AuditUnavailableError`。
- **副作用：** 只读查询，不改变数据库。

### `pc_manager_agent.audit.repository.AuditRepository.close`

```python
close() -> None
```

- **作用：** 释放 SQLAlchemy 连接池，并把仓库恢复为未初始化状态。
- **返回：** 无；不会删除表、数据库文件或历史事件。

### `pc_manager_agent.audit.repository.AuditRepository._redact_mapping`

```python
_redact_mapping(value: dict[str, JsonValue] | None) -> dict[str, Any] | None
```

- **作用：** 私有适配器，将可选字典交给 `redact_json()`，并验证脱敏后仍是字典。
- **返回：** 输入为 `None` 时返回 `None`，否则返回脱敏字典。
- **异常：** 若脱敏函数意外改变顶层形状，抛出 `TypeError`，防止错误数据静默写入。

### `pc_manager_agent.audit.repository.AuditRepository._to_row`

```python
_to_row(event: AuditEvent) -> AuditEventRow
```

- **作用：** 私有类方法，把领域事件逐字段映射为 SQLAlchemy 行；自由文本使用
  `redact_text()`，JSON 映射使用 `_redact_mapping()`，枚举转换为字符串值。
- **返回：** 尚未加入会话的 `AuditEventRow`。
- **安全：** 是所有审计写入的脱敏边界，调用方不应绕过它直接构造并保存数据库行。

## `pc_manager_agent.config.settings`

### `pc_manager_agent.config.settings.AppSettings.validate_provider`

```python
validate_provider(value: str) -> str
```

- **作用：** Pydantic 字段验证器，去除首尾空白并转为小写，只接受 `disabled` 或
  `openai`。
- **返回与异常：** 返回规范化供应商标识；其他值抛出 `ValueError`，阻止隐式加载未知实现。

### `pc_manager_agent.config.settings.AppSettings.normalize_optional_model`

```python
normalize_optional_model(value: str | None) -> str | None
```

- **作用：** 规范化可选 OpenAI 模型名；去除首尾空白，并把空字符串视为未配置。
- **返回：** 规范化模型名或 `None`。

### `pc_manager_agent.config.settings.AppSettings.database_path`

```python
database_path: Path
```

- **作用：** 只读属性，在 `data_directory` 下生成固定的 `state.db` 路径。
- **返回：** 路径对象；读取属性不会创建文件。

### `pc_manager_agent.config.settings.AppSettings.from_environment`

```python
from_environment() -> AppSettings
```

- **作用：** 从进程环境变量读取供应商、模型、密钥、扫描上限、超时和可选数据目录，再由
  Pydantic 完成类型转换及范围校验。
- **返回与异常：** 返回不可变 `AppSettings`；非法数字、范围或供应商由 Pydantic 抛出
  `ValidationError`。
- **安全：** 不读取项目内 `.env` 文件；API Key 使用排除显示和导出的 `SecretStr` 字段。

## `pc_manager_agent.confirmation.state_machine`

### `pc_manager_agent.confirmation.state_machine.ConfirmationService.__init__`

```python
ConfirmationService(
    ttl_seconds: int = 300,
    now: Callable[[], datetime] | None = None,
) -> None
```

- **作用：** 初始化内存确认状态机、确认请求表、计划批准摘要表和运行时批准绑定集合。
- **参数：** `ttl_seconds` 控制确认有效期；`now` 可注入时钟以进行确定性测试。
- **安全：** 确认只在当前进程内有效，应用重启后不会沿用旧授权。

### `pc_manager_agent.confirmation.state_machine.ConfirmationService.request_plan`

```python
request_plan(plan: TaskPlan, object_summary: str) -> ConfirmationRequest
```

- **作用：** 创建 `PLAN` 类型确认，将确认 ID、计划 ID、完整计划摘要、对象说明和过期时间
  绑定在一起，并保存为 `PENDING`。
- **参数：** `plan` 是待确认快照；`object_summary` 是展示给用户的具体影响说明。
- **返回：** 新的不可变 `ConfirmationRequest`；不代表用户已经批准。

### `pc_manager_agent.confirmation.state_machine.ConfirmationService.request_runtime`

```python
request_runtime(
    plan: TaskPlan,
    step: PlanStep,
    object_summary: str,
) -> ConfirmationRequest
```

- **作用：** 为 R2 等需要即时确认的具体步骤创建 `RUNTIME` 请求，同时绑定完整计划摘要、
  `step_id` 和参数摘要。
- **前置条件：** 先调用 `require_plan_approved()`；总体计划没有精确批准时直接失败。
- **返回与异常：** 返回待处理请求；计划未批准或已经变化时抛出 `ConfirmationError`。

### `pc_manager_agent.confirmation.state_machine.ConfirmationService.resolve`

```python
resolve(
    confirmation_id: UUID,
    approved: bool,
    plan: TaskPlan,
    step: PlanStep | None = None,
) -> ConfirmationRequest
```

- **作用：** 解析用户决定。依次验证请求存在、仍为待处理、没有过期、计划 ID/摘要一致；
  运行时确认还要验证步骤和参数摘要。
- **返回：** 状态更新为 `APPROVED` 或 `REJECTED` 的新请求对象，并记录对应授权绑定。
- **异常：** 未知 ID、重复处理、过期、计划变化、步骤不匹配或参数变化均抛出
  `ConfirmationError`。过期请求会先保存为 `EXPIRED`。
- **安全：** 旧确认不能批准修改后的计划或参数；拒绝和过期不会产生执行授权。

### `pc_manager_agent.confirmation.state_machine.ConfirmationService.require_plan_approved`

```python
require_plan_approved(plan: TaskPlan) -> None
```

- **作用：** 执行前检查该 `plan_id` 保存的批准摘要是否与当前完整计划摘要完全一致。
- **返回与异常：** 成功时无返回；未批准或任意字段变化时抛出 `ConfirmationError`。

### `pc_manager_agent.confirmation.state_machine.ConfirmationService.require_runtime_approved`

```python
require_runtime_approved(plan: TaskPlan, step: PlanStep) -> None
```

- **作用：** 先验证总体计划批准，再检查 `(plan_id, step_id, arguments_digest)` 的即时批准
  是否存在。
- **异常：** 总体计划无效或运行时确认缺失/过期时抛出 `ConfirmationError`。
- **安全：** 用于真正执行 R2 步骤前的确定性门禁，不能由 UI 布尔状态替代。

## `pc_manager_agent.domain.plans`

### `pc_manager_agent.domain.plans.TaskScope.require_included_path`

```python
require_included_path() -> Self
```

- **作用：** `TaskScope` 的模型后置验证器，强制计划至少有一个明确允许路径。
- **返回与异常：** 合法时返回当前不可变模型；空 `included_paths` 抛出 `ValueError`，最终由
  Pydantic 汇总为 `ValidationError`。
- **安全：** 防止以“没有范围”等同于“任意范围”的方式扩大权限。

### `pc_manager_agent.domain.plans.PlanStep.arguments_digest`

```python
arguments_digest() -> str
```

- **作用：** 将步骤参数序列化为键顺序稳定、无多余空格的 UTF-8 JSON，再计算 SHA-256。
- **返回：** 64 个十六进制字符的参数摘要。
- **安全：** 摘要用于运行时确认绑定；相同语义但字典插入顺序不同会得到同一结果，参数内容
  改变则确认失效。摘要不是加密或凭据保护手段。

### `pc_manager_agent.domain.plans.TaskPlan.validate_steps`

```python
validate_steps() -> Self
```

- **作用：** `TaskPlan` 的模型后置验证器，要求至少一个步骤，并保证所有 `step_id` 唯一。
- **返回与异常：** 合法时返回当前模型；空步骤或重复 ID 抛出 `ValueError`。
- **安全：** 避免确认和审计无法唯一指向具体步骤。

### `pc_manager_agent.domain.plans.TaskPlan.canonical_digest`

```python
canonical_digest() -> str
```

- **作用：** 将整个计划以 JSON 模式导出、稳定排序并计算 SHA-256，覆盖计划 ID、版本、目标、
  范围、步骤、参数、影响估计和确认声明。
- **返回：** 64 字符计划摘要。
- **安全：** 计划确认以此摘要绑定；任何执行相关字段变化都会使旧确认失效。

## `pc_manager_agent.domain.risk`

### `pc_manager_agent.domain.risk.RiskLevel.severity`

```python
severity: int
```

- **作用：** 把 `R0` 至 `R4` 的枚举值转换为可排序整数 `0..4`。
- **返回：** 风险等级中的数字部分。
- **使用场景：** 安全审查用它判断是否达到必须即时确认的 `R2` 门槛；它不替代具体规则。

## `pc_manager_agent.main`

### `pc_manager_agent.main.build_parser`

```python
build_parser() -> argparse.ArgumentParser
```

- **作用：** 创建最小命令行解析器，注册 `--version` 和 `--smoke-test`。
- **返回：** 尚未解析参数的 `ArgumentParser`，便于单元测试注入参数。
- **副作用：** 不创建 GUI、不读取配置，也不启动应用。

### `pc_manager_agent.main.run_application`

```python
run_application(settings: AppSettings, *, smoke_test: bool = False) -> int
```

- **作用：** 获取或创建 `QApplication`，设置应用标识，取得单实例锁，初始化运行时、主窗口和
  托盘，然后进入 Qt 事件循环。退出时无论成功与否都关闭窗口、托盘、审计资源和单实例锁。
- **参数：** `settings` 是已校验配置；`smoke_test=True` 时用定时器在约 100 ms 后受控退出。
- **返回：** 已有实例时返回 `0`；运行时初始化失败并显示错误对话框时返回 `1`；正常运行
  返回 Qt 事件循环退出码。
- **安全：** 不自动提权。审计库无法初始化时不创建可执行工具的主界面。

### `pc_manager_agent.main.run_application.controlled_quit`

```python
controlled_quit() -> None
```

- **作用：** `run_application()` 内部闭包，先调用 `window.request_quit()` 完成取消和托盘清理，
  再请求 Qt 事件循环退出。
- **可见性：** 仅连接到托盘退出动作和烟雾测试定时器，不是模块级公共 API。

### `pc_manager_agent.main.main`

```python
main(argv: list[str] | None = None) -> int
```

- **作用：** 解析命令行。普通模式从环境加载设置；烟雾测试模式创建临时数据目录，避免测试
  写入真实用户审计库，然后委托 `run_application()`。
- **参数：** `argv=None` 表示使用进程参数；测试可传入显式列表。
- **返回：** `run_application()` 的退出码。

## `pc_manager_agent.orchestration.service`

### `pc_manager_agent.orchestration.service.ScanOrchestrator.__init__`

```python
ScanOrchestrator(
    *,
    registry: ToolRegistry,
    reviewer: SafetyReviewer,
    path_policy: PathPolicy,
    confirmation: ConfirmationService,
    audit: AuditRepository,
    max_files: int,
    timeout_seconds: float,
) -> None
```

- **作用：** 注入完成扫描生命周期所需的注册表、安全审查、路径策略、确认、审计和资源上限。
- **参数：** 所有依赖均由 `ApplicationRuntime` 组合；`max_files` 和 `timeout_seconds` 会写入
  计划和工具参数。
- **副作用：** 读取可选环境变量 `PC_MANAGER_GIT_COMMIT`，用于后续审计追踪；不执行扫描。

### `pc_manager_agent.orchestration.service.ScanOrchestrator.prepare_plan`

```python
prepare_plan(root: Path) -> tuple[TaskPlan, SafetyReview]
```

- **作用：** 校验并规范化根目录，计算位于该根目录内的禁止路径，构造唯一的 `file.scan`
  R0 步骤和完整 `TaskPlan`，随后交给独立 `SafetyReviewer`。
- **返回：** 计划与审查结果的二元组；即使审查拒绝也返回可解释的 `SafetyReview`。
- **异常：** 根目录无效时抛出 `PathSecurityError`；审计写入失败时抛出
  `AuditUnavailableError`。
- **副作用与安全：** 只记录 `plan.reviewed` 审计事件，不读取文件内容，也不执行工具。

### `pc_manager_agent.orchestration.service.ScanOrchestrator.request_plan_confirmation`

```python
request_plan_confirmation(plan: TaskPlan) -> ConfirmationRequest
```

- **作用：** 再次审查计划；通过后生成包含根目录、最大文件数和“不会修改文件”说明的计划确认。
- **返回与异常：** 返回待处理确认；审查不通过时抛出 `OrchestrationError`。
- **安全：** 不允许为被拒绝计划生成可用确认。

### `pc_manager_agent.orchestration.service.ScanOrchestrator.resolve_plan_confirmation`

```python
resolve_plan_confirmation(
    confirmation_id: UUID,
    approved: bool,
    plan: TaskPlan,
) -> ConfirmationRequest
```

- **作用：** 委托确认状态机验证并记录用户决定，然后追加 `confirmation.resolved` 审计事件。
- **返回：** 已解析的不可变确认对象。
- **异常：** 确认不匹配、过期或审计写入失败时透传相应异常；不会在审计失败后假装批准成功。

### `pc_manager_agent.orchestration.service.ScanOrchestrator.execute`

```python
execute(plan: TaskPlan, cancellation: CancellationToken) -> ScanReport
```

- **作用：** 按“重新审查 → 精确确认检查 → `tool.started` 审计 → 注册表执行 → 后置验证 →
  `tool.completed` 审计”的顺序完成一次只读扫描。
- **参数：** `plan` 必须是已确认且未变化的计划；`cancellation` 是线程安全的协作取消信号。
- **返回：** 类型和后置条件均验证通过的 `ScanReport`。
- **异常：** 审查拒绝、确认无效、工具异常、输出类型错误或后置条件失败都会记录
  `tool.failed`（含脱敏错误和耗时）后重新抛出。
- **安全：** 只通过 `ToolRegistry` 执行；审计开始事件写入失败时工具不会启动。

### `pc_manager_agent.orchestration.service.ScanOrchestrator._verify`

```python
_verify(plan: TaskPlan, report: ScanReport) -> None
```

- **作用：** 私有后置验证，确认报告根目录等于已确认范围，并确认汇总文件数等于实际文件元组
  长度。
- **异常：** 任一条件不成立时抛出 `OrchestrationError`，使执行被审计为失败。

### `pc_manager_agent.orchestration.service.ScanOrchestrator._within`

```python
_within(path: Path, root: Path) -> bool
```

- **作用：** 私有词法包含判断，用绝对路径、Windows 大小写规范化和 `commonpath` 判断候选路径
  是否位于根目录内。
- **返回：** 位于范围内返回 `True`；跨驱动器等导致 `ValueError` 时安全返回 `False`。

## `pc_manager_agent.persistence.database`

### `pc_manager_agent.persistence.database.create_sqlite_engine`

```python
create_sqlite_engine(database_path: Path) -> Engine
```

- **作用：** 创建父目录，使用结构化 SQLAlchemy `URL` 构造本地 SQLite 引擎，并注册连接
  初始化回调。
- **返回：** 尚未建业务表的 SQLAlchemy `Engine`。
- **副作用：** 可能创建父目录；第一次连接时可能创建数据库文件。
- **安全：** 路径作为 URL 参数传递，不拼接 SQL；连接强制使用完整性相关 PRAGMA。

### `pc_manager_agent.persistence.database.create_sqlite_engine.configure_connection`

```python
configure_connection(dbapi_connection: object, _connection_record: object) -> None
```

- **作用：** `create_sqlite_engine()` 内部 SQLAlchemy `connect` 事件回调，为每个新连接启用
  外键、WAL 日志和 `synchronous=FULL`。
- **异常处理：** 任一 PRAGMA 失败时先关闭底层连接再重新抛出；游标始终在 `finally` 中关闭。
- **可见性：** 由 SQLAlchemy 自动调用，调用方不应直接调用。

## `pc_manager_agent.platform_support.base`

### `pc_manager_agent.platform_support.base.SingleInstanceGuard.acquire`

```python
acquire() -> bool
```

- **作用：** 平台无关协议方法，要求实现尝试取得单实例所有权。
- **返回：** 当前进程取得锁返回 `True`；已经有活动实例返回 `False`。
- **实现要求：** 不得通过终止其他进程或提权来取得所有权。

### `pc_manager_agent.platform_support.base.SingleInstanceGuard.close`

```python
close() -> None
```

- **作用：** 平台无关协议方法，释放当前进程拥有的单实例资源。
- **要求：** 应支持重复调用，并且不能移除其他活动实例的锁。

## `pc_manager_agent.platform_support.windows.single_instance`

### `pc_manager_agent.platform_support.windows.single_instance.QtSingleInstanceGuard.__init__`

```python
QtSingleInstanceGuard(
    server_name: str = "WindowsPCManagerAgent-0.1",
) -> None
```

- **作用：** 保存本地 IPC 服务名，创建尚未监听的 `QLocalServer`，初始化未取得状态。
- **参数：** `server_name` 应在应用版本/用户范围内稳定；测试可注入不同名称避免冲突。

### `pc_manager_agent.platform_support.windows.single_instance.QtSingleInstanceGuard.server`

```python
server: QLocalServer
```

- **作用：** 暴露只读服务器对象，使启动代码能够监听 `newConnection` 并唤醒现有窗口。
- **返回：** 内部 `QLocalServer`；调用方不应替换或绕过守卫关闭它。

### `pc_manager_agent.platform_support.windows.single_instance.QtSingleInstanceGuard.acquire`

```python
acquire() -> bool
```

- **作用：** 先用 `QLocalSocket` 探测现有服务。150 ms 内成功连接表示已有活动实例，返回
  `False`；否则清理陈旧端点并尝试监听同名服务。
- **返回：** 监听成功返回 `True`，失败返回 `False`。
- **副作用与安全：** 只操作当前应用的本地 IPC 名称，不终止进程、不提权。

### `pc_manager_agent.platform_support.windows.single_instance.QtSingleInstanceGuard.close`

```python
close() -> None
```

- **作用：** 仅在当前对象已取得所有权时关闭服务器、移除本地端点并清除状态。
- **幂等性：** 未取得或已经释放时直接返回，可安全重复调用。

## `pc_manager_agent.providers.llm.base`

### `pc_manager_agent.providers.llm.base.LLMProvider.name`

```python
name: str
```

- **作用：** 抽象只读属性，要求供应商实现返回稳定、非敏感的标识。
- **返回：** 例如 `openai`；不得包含模型密钥或用户配置。
- **抽象行为：** 基类实现抛出 `NotImplementedError`，具体适配器必须覆盖。

### `pc_manager_agent.providers.llm.base.LLMProvider.create_plan`

```python
async create_plan(request: PlannerRequest) -> ProviderPlanResult
```

- **作用：** 抽象异步规划接口，只负责把明确允许的数据转换成结构化 `TaskPlan`。
- **参数：** `request` 明确列出用户目标、路径范围和允许工具。
- **返回：** 经过 Schema 校验的供应商中立结果。
- **安全：** 实现不得执行工具或扩大范围；基类只定义契约并抛出 `NotImplementedError`。

## `pc_manager_agent.providers.llm.openai_provider`

### `pc_manager_agent.providers.llm.openai_provider._ResponsesAPI.parse`

```python
async parse(**kwargs: object) -> object
```

- **作用：** 私有结构化协议，描述 OpenAI 客户端 `responses.parse` 所需的最小异步接口，便于
  注入测试替身。
- **返回：** 原始响应对象；真正的类型和内容由 `OpenAILLMProvider.create_plan()` 验证。

### `pc_manager_agent.providers.llm.openai_provider._OpenAIClient.responses`

```python
responses: _ResponsesAPI
```

- **作用：** 私有协议属性，抽象 OpenAI 客户端的 Responses API 资源。
- **用途：** 降低业务适配器对 SDK 具体客户端类型的耦合，测试无需网络。

### `pc_manager_agent.providers.llm.openai_provider.OpenAILLMProvider.__init__`

```python
OpenAILLMProvider(
    *,
    model: str,
    api_key: str,
    client: _OpenAIClient | None = None,
) -> None
```

- **作用：** 验证并保存显式模型名；使用提供的客户端，或创建超时 30 秒、最多重试一次的
  `AsyncOpenAI` 客户端。
- **异常：** 模型名或 API Key 为空时抛出 `ValueError`。
- **安全：** API Key 只传给 SDK，不保存为公开属性；依赖注入允许测试避免真实请求。

### `pc_manager_agent.providers.llm.openai_provider.OpenAILLMProvider.name`

```python
name: str
```

- **作用与返回：** 始终返回稳定标识 `openai`，不泄露模型 ID 或密钥。

### `pc_manager_agent.providers.llm.openai_provider.OpenAILLMProvider.create_plan`

```python
async create_plan(request: PlannerRequest) -> ProviderPlanResult
```

- **作用：** 把固定安全指令和 `PlannerRequest` JSON 发送到 Responses API，要求 SDK 直接按
  `TaskPlan` 解析；验证输出类型后封装供应商和请求追踪 ID。
- **返回：** `ProviderPlanResult`；它只表示规划结果，不表示计划已审查、确认或执行。
- **异常：** SDK `APIError` 被脱敏包装为 `OpenAIProviderError`；缺少合法结构化计划同样抛出
  `OpenAIProviderError`。
- **隐私与安全：** 只发送调用方显式放入 `PlannerRequest` 的数据；本方法不能调用工具，返回
  计划仍必须经过确定性安全审查和确认。

## `pc_manager_agent.rollback.base`

### `pc_manager_agent.rollback.base.OperationCommand.execute`

```python
execute() -> None
```

- **作用：** 写操作命令抽象方法；具体实现只能在安全审查和确认门禁之后改变状态。
- **抽象行为：** 基类抛出 `NotImplementedError`；实现应在失败时抛出明确异常而非吞掉错误。

### `pc_manager_agent.rollback.base.OperationCommand.verify`

```python
verify() -> bool
```

- **作用：** 抽象后置条件验证，检查真实系统状态是否符合命令声明。
- **返回：** 验证通过返回 `True`，失败返回 `False`；不得仅复述执行函数返回值。

### `pc_manager_agent.rollback.base.OperationCommand.build_undo_record`

```python
build_undo_record() -> UndoRecord
```

- **作用：** 从执行前后观察到的状态构建真实的 `UndoRecord`，记录路径、文件标识、元数据、
  回滚级别和有效条件。
- **要求：** 无法自动回滚时必须使用 `PARTIAL`、`MANUAL` 或 `NONE`，不得虚构 `FULL`。

### `pc_manager_agent.rollback.base.OperationCommand.rollback`

```python
rollback(record: UndoRecord) -> bool
```

- **作用：** 抽象回滚操作；只有记录有效条件仍成立时才能尝试恢复。
- **返回：** 已验证恢复成功返回 `True`，否则返回 `False` 或由实现抛出明确异常。
- **当前范围：** MVP 0.1 尚无实际写操作实现，这些方法是安全扩展契约。

## `pc_manager_agent.safety.path_policy`

### `pc_manager_agent.safety.path_policy._absolute_lexical`

```python
_absolute_lexical(path: Path) -> Path
```

- **作用：** 私有词法规范化函数，移除 `.` 等片段并转换为绝对路径，但不调用
  `Path.resolve()`，因此不会主动跟随符号链接、目录联接或其他重解析点。
- **返回：** 词法规范化后的 `Path`。
- **安全：** 仅做字符串/路径级比较；存在性和链接检查必须由后续函数完成。

### `pc_manager_agent.safety.path_policy._is_within`

```python
_is_within(path: Path, root: Path) -> bool
```

- **作用：** 私有范围比较，先对两条路径进行词法绝对化和 Windows 大小写规范化，再通过
  `os.path.commonpath()` 判断包含关系。
- **返回：** 候选位于根目录内（包括根本身）时返回 `True`；不同驱动器等比较错误时返回
  `False`。
- **安全：** 不使用容易产生前缀误判的字符串 `startswith()`。

### `pc_manager_agent.safety.path_policy.is_reparse_point`

```python
is_reparse_point(path: Path) -> bool
```

- **作用：** 使用 `os.lstat()` 在不跟随链接的情况下读取文件属性，同时检查
  `Path.is_symlink()` 和 Windows `FILE_ATTRIBUTE_REPARSE_POINT`。
- **返回：** 符号链接、目录联接或其他重解析点返回 `True`；普通对象返回 `False`。
- **错误处理：** `lstat` 失败时返回 `False`，调用方仍需通过存在性、权限和实际访问错误检查
  进行失败关闭，不能仅依赖本函数授权访问。

### `pc_manager_agent.safety.path_policy.PathPolicy.__init__`

```python
PathPolicy(
    approved_roots: Iterable[Path],
    forbidden_roots: Iterable[Path] = (),
    *,
    current_user_root: Path | None = None,
) -> None
```

- **作用：** 把允许和禁止根目录转换为不可变、词法规范化元组，并记录当前用户目录及其
  `Users` 父目录。
- **参数：** 至少需要一个 `approved_root`；测试可显式传入 `current_user_root`。
- **异常：** 允许根目录为空时抛出 `ValueError`。
- **安全：** 构造只建立策略，不证明路径存在；所有执行入口仍须调用 `validate_scan_root()`。

### `pc_manager_agent.safety.path_policy.PathPolicy.for_scan_root`

```python
for_scan_root(
    root: Path,
    extra_forbidden: Iterable[Path] = (),
) -> PathPolicy
```

- **作用：** 为一次扫描创建保守策略，自动加入 SSH、OneDrive Personal Vault、Chrome、
  Edge、Firefox、1Password、Bitwarden 和 Windows 安全数据库等禁止根目录。
- **参数：** `root` 是唯一批准根；`extra_forbidden` 用于用户自定义禁止目录。
- **返回：** 绑定当前用户配置文件的新 `PathPolicy`。

### `pc_manager_agent.safety.path_policy.PathPolicy.approved_roots`

```python
approved_roots: tuple[Path, ...]
```

- **作用与返回：** 返回不可变的批准根目录元组，调用方不能通过修改返回值扩大策略。

### `pc_manager_agent.safety.path_policy.PathPolicy.forbidden_roots`

```python
forbidden_roots: tuple[Path, ...]
```

- **作用与返回：** 返回不可变禁止根目录元组，用于计划展示、排除计算和审计说明。

### `pc_manager_agent.safety.path_policy.PathPolicy.is_forbidden`

```python
is_forbidden(path: Path) -> bool
```

- **作用：** 检查路径名称是否命中固定禁止名、是否位于任一禁止根目录，或者是否位于其他
  Windows 用户的配置文件中。
- **返回：** 任一禁止条件成立时返回 `True`。
- **安全：** 使用词法路径，不读取目录内容；“其他用户目录”默认拒绝。

### `pc_manager_agent.safety.path_policy.PathPolicy.is_approved`

```python
is_approved(path: Path) -> bool
```

- **作用：** 同时要求候选路径位于至少一个批准根内且不属于禁止范围。
- **返回：** 满足两个条件才返回 `True`。
- **注意：** 这是词法授权检查，不替代存在性、重解析点和执行时身份复验。

### `pc_manager_agent.safety.path_policy.PathPolicy.validate_scan_root`

```python
validate_scan_root(path: Path) -> Path
```

- **作用：** 拒绝显式 `..`，严格解析现有路径，确认它是目录，拒绝输入路径或解析结果上的
  重解析点，并检查批准/禁止范围。
- **返回：** 已存在的规范化绝对根目录。
- **异常：** 路径不可用、不是目录、包含重解析点或越权时抛出 `PathSecurityError`，错误消息
  包含被拒绝原因。
- **安全：** 是计划和执行都会调用的根目录强校验；后续扫描仍会逐项复验。

### `pc_manager_agent.safety.path_policy.PathPolicy.entry_rejection_reason`

```python
entry_rejection_reason(path: Path) -> str | None
```

- **作用：** 扫描每个对象前执行轻量策略检查，并返回稳定的机器可读原因。
- **返回：** 可能为 `outside-approved-root`、`forbidden-path`、`reparse-point`；允许时返回
  `None`。
- **用途：** 扫描器把拒绝原因写入 `ScanIssue`，而不是静默跳过或抛弃整个报告。

## `pc_manager_agent.safety.plan_reviewer`

### `pc_manager_agent.safety.plan_reviewer.SafetyReviewer.__init__`

```python
SafetyReviewer(registry: ToolRegistry, path_policy: PathPolicy) -> None
```

- **作用：** 注入允许列表注册表和当前任务路径策略，构造不执行任何工具的独立审查器。
- **副作用：** 无；不会注册工具、确认计划或访问文件内容。

### `pc_manager_agent.safety.plan_reviewer.SafetyReviewer.review`

```python
review(plan: TaskPlan) -> SafetyReview
```

- **作用：** 对计划执行完整确定性审查：要求计划确认；验证包含路径；确认工具已注册；校验
  参数 Schema；比较风险和回滚声明；要求 R2+ 即时确认；拒绝 R3/R4；检查清单声明的路径
  参数没有扩大范围。
- **返回：** 包含所有发现问题的 `SafetyReview`。只要有一个 `ReviewIssue`，`approved` 就是
  `False`。
- **错误处理：** 可预期的未知工具、参数和路径错误被转换为机器可读问题，而不是直接执行或
  猜测修复。
- **安全：** 审查是只读的；返回批准也不等于用户确认，更不等于执行授权。

## `pc_manager_agent.tools.file_tools.scanner`

### `pc_manager_agent.tools.file_tools.scanner.DirectoryScannerTool.__init__`

```python
DirectoryScannerTool(
    path_policy: PathPolicy,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> None
```

- **作用：** 保存根目录安全策略和可注入单调时钟，并创建 `file.scan` 的不可变
  `ToolManifest`。
- **清单声明：** R0、只读、支持取消、回滚 `NONE`、最多 100000 项、最长 3600 秒、仅
  Windows，作用域参数为 `root`。
- **参数：** 测试可注入确定性时钟；生产默认使用不受系统时间回拨影响的 `monotonic`。

### `pc_manager_agent.tools.file_tools.scanner.DirectoryScannerTool.manifest`

```python
manifest: ToolManifest
```

- **作用与返回：** 返回构造时创建的不可变安全清单，供注册、审查和输出类型校验使用。

### `pc_manager_agent.tools.file_tools.scanner.DirectoryScannerTool.execute`

```python
execute(request: BaseModel, cancellation: CancellationToken) -> BaseModel
```

- **作用：** 注册工具统一入口，先确认输入实际为 `ScanRequest`，再委托 `_scan()`。
- **返回：** 以协议基类标注、实际类型为 `ScanReport`。
- **异常：** 绕过注册表传入错误模型时抛出 `TypeError`；路径安全错误由扫描实现透传。

### `pc_manager_agent.tools.file_tools.scanner.DirectoryScannerTool._scan`

```python
_scan(request: ScanRequest, cancellation: CancellationToken) -> ScanReport
```

- **作用：** 私有扫描核心。执行前重新验证根目录；用显式栈遍历目录；逐项检查取消、超时、
  排除范围和路径策略；不跟随链接；只读取 `stat` 元数据并生成报告。
- **边界：** 达到 `max_files` 标记 `truncated`；超时标记 `timed_out`；取消标记
  `cancelled`。非致命权限和文件系统错误转换为 `ScanIssue`。
- **竞态防护：** 发现目录时记录 `(st_dev, st_ino)`，枚举前再次读取；身份变化时记录
  `path-identity-changed` 并跳过，降低扫描后替换风险。
- **返回：** 包含文件元组、问题元组和数量/大小/耗时汇总的 `ScanReport`。
- **安全：** 不打开文件正文、不写元数据、不永久删除；回滚为 `NONE` 是因为没有写操作。

### `pc_manager_agent.tools.file_tools.scanner.DirectoryScannerTool._path_is_within`

```python
_path_is_within(path: Path, root: Path) -> bool
```

- **作用：** 私有排除范围判断，使用绝对路径、Windows 大小写规范化和 `commonpath`。
- **返回：** 位于排除根内返回 `True`；无法比较时返回 `False`。

### `pc_manager_agent.tools.file_tools.scanner.DirectoryScannerTool._os_issue`

```python
_os_issue(path: Path, error: OSError) -> ScanIssue
```

- **作用：** 把操作系统异常转换为报告问题；`PermissionError` 映射为
  `permission-denied`，其他 `OSError` 映射为 `filesystem-error`。
- **返回：** 包含原路径和错误文本的 `ScanIssue`；不会吞掉到审计之外，因为问题会进入报告。

### `pc_manager_agent.tools.file_tools.scanner.DirectoryScannerTool._directory_identity`

```python
_directory_identity(path: Path) -> tuple[int, int]
```

- **作用：** 使用 `os.stat(..., follow_symlinks=False)` 获取卷/设备号和文件标识号。
- **返回：** `(st_dev, st_ino)`，用于目录发现与枚举前的身份一致性检查。
- **异常：** 路径消失或不可访问时抛出 `OSError`，上层会记录问题并跳过。

## `pc_manager_agent.tools.manifest`

### `pc_manager_agent.tools.manifest.CancellationToken.__init__`

```python
CancellationToken() -> None
```

- **作用：** 创建内部 `threading.Event`，初始状态为未取消。
- **线程安全：** `Event` 允许 GUI 线程发出取消、工作线程读取，无需全局可变状态。

### `pc_manager_agent.tools.manifest.CancellationToken.cancel`

```python
cancel() -> None
```

- **作用：** 设置内部事件，请求协作取消。
- **行为：** 幂等且不强制终止线程；工具应在安全检查点主动读取状态。

### `pc_manager_agent.tools.manifest.CancellationToken.is_cancelled`

```python
is_cancelled: bool
```

- **作用与返回：** 只读属性，返回取消事件当前是否已设置。

### `pc_manager_agent.tools.manifest.CancellationToken.cancellation_requested`

```python
cancellation_requested() -> bool
```

- **作用与返回：** 动态读取当前取消状态，供长循环反复调用；与属性值语义相同，但明确表达
  “每次重新检查”而不是缓存一次结果。

### `pc_manager_agent.tools.manifest.ToolManifest.__post_init__`

```python
__post_init__() -> None
```

- **作用：** dataclass 构造后验证工具名格式、非空描述、正数超时/批量上限，并强制 R0 工具
  必须只读。
- **异常：** 任一清单矛盾时抛出 `ValueError`，阻止工具进入注册表。
- **安全：** 把关键声明一致性检查放在对象创建时，而不是依赖调用方自觉。

### `pc_manager_agent.tools.manifest.RegisteredTool.manifest`

```python
manifest: ToolManifest
```

- **作用：** `RegisteredTool` 协议属性，要求每个工具提供不可变、完整的安全清单。
- **返回：** 与实际执行实现一致的 `ToolManifest`。

### `pc_manager_agent.tools.manifest.RegisteredTool.execute`

```python
execute(request: BaseModel, cancellation: CancellationToken) -> BaseModel
```

- **作用：** 确定性工具协议方法，接受已经按清单 Schema 校验的模型和取消令牌。
- **返回：** 必须是清单声明的 `output_model` 实例；注册表会在运行后再次验证。
- **限制：** 实现不得解释任意 Shell 文本或扩大已批准范围。

## `pc_manager_agent.tools.registry`

### `pc_manager_agent.tools.registry.ToolRegistry.__init__`

```python
ToolRegistry() -> None
```

- **作用：** 创建空的工具名到 `RegisteredTool` 映射；默认没有任何执行权限。

### `pc_manager_agent.tools.registry.ToolRegistry.register`

```python
register(tool: RegisteredTool) -> None
```

- **作用：** 按清单名称加入一个工具。
- **异常：** 名称已存在时抛出 `ToolRegistryError`，禁止静默覆盖已有权限定义。
- **安全：** 清单在工具构造时已经完成一致性校验。

### `pc_manager_agent.tools.registry.ToolRegistry.manifest`

```python
manifest(name: str) -> ToolManifest
```

- **作用：** 查找已注册工具的清单。
- **返回与异常：** 存在时返回不可变清单；未知名称抛出 `UnknownToolError`，采用默认拒绝。

### `pc_manager_agent.tools.registry.ToolRegistry.validate_input`

```python
validate_input(
    name: str,
    arguments: Mapping[str, object],
) -> BaseModel
```

- **作用：** 取得工具清单，把原始参数复制为普通字典，并调用声明的 Pydantic 输入模型校验。
- **返回：** 具体输入模型实例。
- **异常：** 未知工具抛出 `UnknownToolError`；Schema 错误包装为 `ToolInputError`。

### `pc_manager_agent.tools.registry.ToolRegistry.execute`

```python
execute(
    name: str,
    arguments: Mapping[str, object],
    cancellation: CancellationToken | None = None,
) -> BaseModel
```

- **作用：** 注册表的唯一执行入口：查找允许工具、校验输入、提供取消令牌、调用工具、验证
  输出类型。
- **返回：** 与清单 `output_model` 一致的结果模型。
- **异常：** 未知工具、输入错误和输出类型违约分别抛出 `UnknownToolError`、`ToolInputError`
  和 `ToolOutputError`。
- **安全：** 不接受动态导入或任意命令；模型只能选择已经注册的名称。

### `pc_manager_agent.tools.registry.ToolRegistry.names`

```python
names: tuple[str, ...]
```

- **作用与返回：** 返回按名称排序的不可变工具名元组，便于稳定展示、测试和提供给规划器。

## `pc_manager_agent.ui.main_window`

本模块是展示层。带下划线的方法是 Qt 信号连接的内部槽或界面构造辅助函数；它们不应被
业务代码直接调用。所有扫描动作都委托给编排器，UI 不直接调用文件工具。

### `pc_manager_agent.ui.main_window.MainWindow.__init__`

```python
MainWindow(runtime: ApplicationRuntime) -> None
```

- **作用：** 保存运行时依赖，初始化当前编排器、计划、确认、工作线程、托盘和退出状态，
  创建四个页签并设置窗口标题、尺寸和初始状态栏消息。
- **参数：** `runtime` 必须已经完成审计数据库初始化。
- **副作用：** 构造 Qt 控件，但不扫描目录、不调用模型、不执行工具。

### `pc_manager_agent.ui.main_window.MainWindow.attach_tray`

```python
attach_tray(tray: SystemTrayController) -> None
```

- **作用：** 在窗口和托盘对象都构造完成后注入托盘控制器，解决双向引用的初始化顺序。
- **副作用：** 只保存引用，不显示托盘或执行退出。

### `pc_manager_agent.ui.main_window.MainWindow._build_chat_tab`

```python
_build_chat_tab() -> None
```

- **作用：** 创建对话显示区、输入框和发送按钮，连接按钮点击及回车信号到 `_handle_chat()`。
- **安全：** 明确提示阶段 0 聊天仅本地显示；本方法不创建模型供应商或网络连接。

### `pc_manager_agent.ui.main_window.MainWindow._build_scan_tab`

```python
_build_scan_tab() -> None
```

- **作用：** 创建目录选择、计划显示、风险提示、确认/拒绝、开始/取消、进度条和结果表格；
  配置初始禁用状态、只读表格、排序及全部 Qt 信号连接。
- **安全：** 默认禁用执行按钮，只有计划准备并确认后才由其他槽函数启用。

### `pc_manager_agent.ui.main_window.MainWindow._build_audit_tab`

```python
_build_audit_tab() -> None
```

- **作用：** 创建审计刷新按钮和只读表格，展示时间、事件、风险、工具、确认结果及计划 ID。
- **副作用：** 只构造控件；不会在初始化时读取或改变审计数据库。

### `pc_manager_agent.ui.main_window.MainWindow._build_settings_tab`

```python
_build_settings_tab() -> None
```

- **作用：** 以只读标签展示供应商、模型、数据目录、扫描上限和当前 MVP 限制。
- **安全：** 只显示 API Key 的存储原则，不读取或显示密钥值。

### `pc_manager_agent.ui.main_window.MainWindow._handle_chat`

```python
_handle_chat() -> None
```

- **作用：** Qt 槽。读取并去除输入空白；空输入直接返回；非空文本追加到本地对话区，显示
  安全提示后清空输入框。
- **安全：** 不调用 `create_llm_provider()`，所以聊天文本不会上传，也不会触发工具执行。

### `pc_manager_agent.ui.main_window.MainWindow._choose_directory`

```python
_choose_directory() -> None
```

- **作用：** Qt 槽。打开系统目录选择对话框，并把用户选择的路径写入扫描根目录输入框。
- **注意：** 选择目录不等于授权执行；路径仍需计划、安全审查和确认。

### `pc_manager_agent.ui.main_window.MainWindow._prepare_plan`

```python
_prepare_plan() -> None
```

- **作用：** Qt 槽。检查根目录输入，创建根目录限定编排器，生成并审查计划，再请求计划确认；
  成功后保存对象、显示结构化 JSON 和 R0 风险说明，启用确认/拒绝按钮。
- **错误处理：** 空路径或任意计划/审查异常通过 `_show_error()` 告知用户并停止流程。
- **安全：** 审查拒绝时不会保存可执行状态；扫描按钮保持禁用。

### `pc_manager_agent.ui.main_window.MainWindow._approve_plan`

```python
_approve_plan() -> None
```

- **作用：** Qt 槽。确认当前编排器、计划和确认请求均存在，调用确定性确认服务记录批准，
  然后禁用决定按钮并启用只读扫描按钮。
- **错误处理：** 确认过期或计划变化等异常显示给用户，扫描按钮不会因此错误启用。

### `pc_manager_agent.ui.main_window.MainWindow._reject_plan`

```python
_reject_plan() -> None
```

- **作用：** Qt 槽。把当前计划决定解析为拒绝，并禁用确认、拒绝和扫描按钮。
- **安全：** 拒绝只记录状态和审计，不执行扫描；缺少当前对象时安全返回。

### `pc_manager_agent.ui.main_window.MainWindow._start_scan`

```python
_start_scan() -> None
```

- **作用：** Qt 槽。在编排器和计划存在且没有活动工作器时创建 `ScanWorker`，连接完成/失败
  信号，更新按钮和不确定进度条，并提交给全局线程池。
- **安全：** 工作器仍会调用编排器重新审查和验证确认；UI 不能绕过安全层直接执行扫描器。

### `pc_manager_agent.ui.main_window.MainWindow._cancel_scan`

```python
_cancel_scan() -> None
```

- **作用：** Qt 槽。若存在活动工作器，则设置其协作取消令牌并更新状态栏。
- **行为：** 不强杀线程；扫描器在下一个安全检查点结束并返回带 `cancelled=True` 的报告。

### `pc_manager_agent.ui.main_window.MainWindow._scan_completed`

```python
_scan_completed(value: object) -> None
```

- **作用：** Qt 槽。先用 `require_scan_report()` 收窄跨线程信号对象，再清理工作状态、恢复
  控件、填充结果、显示完成/取消摘要并刷新审计表。
- **错误处理：** 信号载荷类型错误时转入 `_scan_failed()`，不会把任意对象当作报告显示。

### `pc_manager_agent.ui.main_window.MainWindow._scan_failed`

```python
_scan_failed(message: str) -> None
```

- **作用：** Qt 槽。清除活动工作器、恢复按钮和进度条，并通过统一错误对话框显示失败原因。
- **安全：** 只更新界面；工具失败的结构化详情由编排器审计。

### `pc_manager_agent.ui.main_window.MainWindow._populate_results`

```python
_populate_results(report: ScanReport) -> None
```

- **作用：** 暂停排序，按报告文件数建立表格行，写入名称、扩展名、媒体类型、大小、修改
  时间和完整路径；为大小列保存数值排序数据，最后恢复排序并调整列宽。
- **副作用：** 只操作内存中的 Qt 表格，不打开或修改报告中的文件。

### `pc_manager_agent.ui.main_window.MainWindow._refresh_audit`

```python
_refresh_audit() -> None
```

- **作用：** Qt 槽。读取最近最多 100 条审计行，按当前查询顺序填入审计表。
- **错误处理：** 数据库查询失败时显示错误并保持应用可见，不伪造空审计结果。
- **副作用：** 只读数据库。

### `pc_manager_agent.ui.main_window.MainWindow.request_quit`

```python
request_quit() -> None
```

- **作用：** 标记受控退出，调用 `shutdown()` 请求取消并等待后台任务，隐藏托盘，然后关闭
  主窗口。
- **安全：** 确保退出路径不会把仍运行的扫描任务遗留为失控后台工作。

### `pc_manager_agent.ui.main_window.MainWindow.shutdown`

```python
shutdown() -> None
```

- **作用：** 若有工作器则请求协作取消，然后让全局线程池最多等待 5000 ms。
- **行为：** 有界等待避免 UI 永久卡死；不会强制终止线程或进程。

### `pc_manager_agent.ui.main_window.MainWindow.closeEvent`

```python
closeEvent(event: QCloseEvent) -> None
```

- **作用：** Qt 关闭事件覆盖。非受控退出且托盘可用时忽略关闭、隐藏窗口并提示仍在托盘运行；
  受控退出或无托盘时接受关闭。
- **安全：** 普通窗口关闭不会绕过统一退出流程；托盘退出会先请求取消任务。

### `pc_manager_agent.ui.main_window.MainWindow._show_error`

```python
_show_error(message: str) -> None
```

- **作用：** 私有统一错误展示，在警告对话框和状态栏显示同一条用户可见消息。
- **限制：** 调用方应传入已经脱敏的错误；本方法本身不写审计也不做脱敏。

## `pc_manager_agent.ui.system_tray`

### `pc_manager_agent.ui.system_tray.SystemTrayController.__init__`

```python
SystemTrayController(
    window: QMainWindow,
    quit_callback: Callable[[], None],
) -> None
```

- **作用：** 创建系统托盘图标和“打开、隐藏、安全退出”菜单，把动作分别连接到窗口方法和
  受控退出回调，并连接托盘激活事件。
- **参数：** `quit_callback` 应是应用统一清理函数，而不是直接调用进程退出。
- **安全：** 托盘只负责展示和委托，不直接调用编排器或工具。

### `pc_manager_agent.ui.system_tray.SystemTrayController.is_available`

```python
is_available: bool
```

- **作用与返回：** 查询当前桌面会话是否提供系统托盘；不缓存结果。

### `pc_manager_agent.ui.system_tray.SystemTrayController.show`

```python
show() -> None
```

- **作用：** 仅在托盘可用时显示图标；不可用时安全地不执行任何操作。

### `pc_manager_agent.ui.system_tray.SystemTrayController.hide`

```python
hide() -> None
```

- **作用：** 从系统托盘隐藏图标；不关闭窗口或后台资源。

### `pc_manager_agent.ui.system_tray.SystemTrayController.show_window`

```python
show_window() -> None
```

- **作用：** 恢复主窗口的正常状态、提升到前台并请求键盘焦点，供菜单和第二实例连接复用。

### `pc_manager_agent.ui.system_tray.SystemTrayController._on_activated`

```python
_on_activated(reason: QSystemTrayIcon.ActivationReason) -> None
```

- **作用：** 私有托盘激活处理器；只有普通单击 `Trigger` 时调用 `show_window()`，其他激活
  原因不处理。

## `pc_manager_agent.ui.workers`

### `pc_manager_agent.ui.workers.ScanWorker.__init__`

```python
ScanWorker(orchestrator: ScanOrchestrator, plan: TaskPlan) -> None
```

- **作用：** 初始化 `QRunnable`、线程安全信号对象和新的 `CancellationToken`，保存已经准备
  的编排器与计划。
- **副作用：** 不立即执行；只有提交给 `QThreadPool` 后才调用 `run()`。

### `pc_manager_agent.ui.workers.ScanWorker.run`

```python
run() -> None
```

- **作用：** Qt 工作线程入口，调用编排器执行；成功时发射 `completed(report)`，任意异常时
  发射包含异常类型和消息的 `failed(str)`。
- **异常边界：** 捕获所有 `Exception` 是 Qt 线程边界的有意设计，避免异常丢失；业务层已经
  负责结构化失败审计。

### `pc_manager_agent.ui.workers.ScanWorker.cancel`

```python
cancel() -> None
```

- **作用：** 把取消请求转交给工作器的 `CancellationToken`。
- **行为：** 线程安全、幂等、协作式，不强制中断当前系统调用。

### `pc_manager_agent.ui.workers.require_scan_report`

```python
require_scan_report(value: object) -> ScanReport
```

- **作用：** 对 Qt `Signal(object)` 传输的宽类型对象执行运行时收窄。
- **返回与异常：** 输入是 `ScanReport` 时原样返回；其他类型抛出 `TypeError`。
- **安全：** 防止跨线程错误载荷被当作可信扫描结果使用。

## 典型调用顺序

```text
AppSettings.from_environment
  -> ApplicationRuntime
  -> ApplicationRuntime.create_scan_orchestrator
  -> ScanOrchestrator.prepare_plan
  -> SafetyReviewer.review
  -> ScanOrchestrator.request_plan_confirmation
  -> ConfirmationService.resolve
  -> ScanOrchestrator.execute
  -> ToolRegistry.execute
  -> DirectoryScannerTool.execute
  -> ScanOrchestrator._verify
  -> AuditRepository.record
```

调用方不得跳过中间门禁直接调用 `_scan()`，也不得把模型返回的工具名或参数直接交给系统
API。新增工具时应先定义严格输入/输出模型和 `ToolManifest`，再补充安全审查、确认、审计、
回滚和测试。

## Stage 1 新增与修订 API

本节补充阶段 1 的全部生产函数。若前文的阶段 0 说明与本节冲突，以本节为准。阶段 1
新增的关键模型包括 `AuthorizedPath`、`FileAnalysisIntentDraft`、`FileAnalysisPlan`、
`FileAnalysisProgress`、`FileAnalysisReport`、`StoredFileRecord`、`DuplicateGroup`、
`InactiveAssessment`、`ExternalDataConsentRequest`、`ReportExportResult`、`ScanBatch` 和
`ScanProgress`。这些模型都拒绝未知字段；意图/计划/报告模型不可变。

### 运行时组合与审计回调

#### `pc_manager_agent.app.runtime.ApplicationRuntime.create_file_analysis_services`

```python
create_file_analysis_services(
    progress_callback: Callable[[FileAnalysisProgress], None] | None = None,
) -> FileAnalysisServices
```

- **作用：** 从当前已授权根目录创建一次完整的阶段 1 依赖包：根目录限定
  `PathPolicy`、四个 R0 工具、注册表、通用/专用安全审查器、编译器、编排器，以及
  可选规划器/说明器。
- **参数与返回：** 可选回调接收跨扫描/分析阶段的不可变进度；返回的服务对象可供 GUI
  或无界面测试使用。没有授权目录时抛出 `ValueError`，模型配置不完整时抛出
  `ProviderConfigurationError`。
- **副作用与安全：** 只组合对象，不扫描。每次调用重新从持久化授权构建策略，不能复用
  旧范围；工具输出批量写入应用 SQLite。

| 函数 | 详细作用、输入/输出与安全约束 |
|---|---|
| `pc_manager_agent.app.runtime.ApplicationRuntime.create_file_analysis_services.scanner_progress(value)` | 内部适配器；把 `ScanProgress` 转换成带会话 ID 的 `FileAnalysisProgress`。没有外部回调或扫描没有会话 ID 时不发射，防止错误关联任务。 |
| `pc_manager_agent.app.runtime.ApplicationRuntime.create_file_analysis_services.analyzer_progress(session_id, phase, completed, total)` | 内部适配器；把各分析器的分页进度统一为 GUI 进度模型。只传递计数，不读取或发送文件详情。 |
| `pc_manager_agent.app.runtime.ApplicationRuntime._audit_external_consent(request)` | 在外部数据确认被批准/拒绝后写 `external_data.confirmation.resolved`；记录目的、供应商、载荷摘要和状态，不保存原始载荷。审计失败会失败关闭。 |
| `pc_manager_agent.app.runtime.ApplicationRuntime._audit_authorized_path_change(action, record)` | 记录授权/禁止根的添加、移除或恢复，风险 R1；保存精确记录和反向动作，用于 FULL 配置回滚，不授权父目录。 |
| `pc_manager_agent.app.runtime.ApplicationRuntime._audit_report_export(result)` | 记录显式报告创建的路径、格式、行数和大小，风险 R1、回滚 MANUAL；不会自动删除报告。 |

`ApplicationRuntime.__init__` 现在还初始化外部确认、授权仓库、分析结果仓库、报告导出器
和 Explorer 服务；`ApplicationRuntime.close()` 依次释放分析、授权和审计连接池，不删除
数据库或用户报告。

### 授权目录服务

#### `pc_manager_agent.authorization.service.AuthorizedPathService.__init__`

```python
AuthorizedPathService(
    repository: AuthorizedPathRepository,
    *,
    network_path_detector: Callable[[Path], bool] | None = None,
    on_change: Callable[[str, AuthorizedPath], None] | None = None,
) -> None
```

- **作用：** 把授权持久化、Windows 网络路径检测和审计回调组合为唯一的目录授权入口。
- **安全：** 供应商/模型不持有此服务；检测器可在测试中注入，生产使用 Windows 驱动器
  类型检查。构造本身不授予任何路径。

| 函数 | 详细作用、输入/输出、异常与副作用 |
|---|---|
| `pc_manager_agent.authorization.service.AuthorizedPathService.add_authorized(path, label=None, favorite=False)` | 规范化并验证现有本地目录，拒绝网络、重解析和禁止根；持久化为 `AUTHORIZED` 并返回记录。只授权该根及安全子树，不授权父目录；重复规范路径抛 `AuthorizedPathStoreError`。 |
| `pc_manager_agent.authorization.service.AuthorizedPathService.add_forbidden(path, label=None)` | 验证并保存用户自定义禁止目录，返回 `FORBIDDEN` 记录。它增加拒绝范围，不授予读取权。 |
| `pc_manager_agent.authorization.service.AuthorizedPathService.remove(path_id)` | 按不透明 UUID 删除一项决定；存在时触发审计并返回 `True`，未知 ID 返回 `False`。它不删除目录或其中内容。 |
| `pc_manager_agent.authorization.service.AuthorizedPathService.restore(record)` | 对审计/回滚提供的完整记录重新做路径安全与身份规范化后恢复；规范路径变化时抛 `ValueError`，冲突时抛存储异常。 |
| `pc_manager_agent.authorization.service.AuthorizedPathService.list_authorized()` | 从 SQLite 按创建顺序返回所有授权根的不可变元组；无数据返回空元组。 |
| `pc_manager_agent.authorization.service.AuthorizedPathService.list_forbidden()` | 返回自定义禁止记录，不包含代码内置的系统保护根。 |
| `pc_manager_agent.authorization.service.AuthorizedPathService.forbidden_roots()` | 从禁止记录投影出规范 `Path` 元组，供策略组合；不返回可变数据库对象。 |
| `pc_manager_agent.authorization.service.AuthorizedPathService.build_policy(root_ids)` | 只解析给定授权 UUID，拒绝空集合、未知/禁止 ID，构建精确多根策略并逐根复验。返回 `PathPolicy`；不接受路径字符串替代 ID。 |
| `pc_manager_agent.authorization.service.AuthorizedPathService.resolve_authorized(root_ids)` | 把 UUID 元组解析成授权记录；保持顺序，任何未知或非授权记录都抛 `PathNotAuthorizedError`，避免部分成功扩大语义。 |
| `pc_manager_agent.authorization.service.AuthorizedPathService.require_authorized(path)` | 在所有当前授权根和禁止根下复验一个目录；成功返回规范路径，失败统一转换为 `PathNotAuthorizedError`。 |
| `pc_manager_agent.authorization.service.AuthorizedPathService.require_authorized_file(path)` | 执行路径、范围、重解析、网络和普通文件检查；成功返回规范文件路径，供 Explorer/哈希使用。不会打开内容。 |
| `pc_manager_agent.authorization.service.AuthorizedPathService._notify(action, record)` | 私有回调门；仅在注入 `on_change` 时通知，数据库成功前不会误报。回调异常向上传播，使安全审计问题可见。 |

### 外部数据确认

#### `pc_manager_agent.confirmation.external_data.ExternalDataConsentService.__init__`

```python
ExternalDataConsentService(
    ttl_seconds: int = 300,
    now: Callable[[], datetime] | None = None,
    on_resolved: Callable[[ExternalDataConsentRequest], None] | None = None,
) -> None
```

- **作用：** 创建只驻留内存的外发数据确认仓库；时钟和完成回调可注入以便测试/审计。
- **安全：** 重启即丢失批准；这是有意的安全默认值。服务保存摘要而非原始载荷。

| 函数 | 详细作用、输入/输出、异常与安全约束 |
|---|---|
| `pc_manager_agent.confirmation.external_data.ExternalDataConsentService.request(purpose, provider, payload, object_summary)` | 对 JSON 兼容载荷计算稳定 SHA-256，创建 PENDING、带目的/供应商/说明/到期时间的请求并返回。原始载荷不进入服务状态。 |
| `pc_manager_agent.confirmation.external_data.ExternalDataConsentService.resolve(confirmation_id, approved)` | 只解析已知、未处理、未过期请求，设为 APPROVED/REJECTED 并触发审计；未知、重复或过期抛 `ExternalDataConsentError`。 |
| `pc_manager_agent.confirmation.external_data.ExternalDataConsentService.require_approved(confirmation_id, purpose, provider, payload)` | 外部调用前的强制门禁；重新计算载荷摘要并逐项比较状态、到期、目的、供应商和摘要。任何变化均拒绝且不调用网络。 |
| `pc_manager_agent.confirmation.external_data.ExternalDataConsentService.payload_digest(payload)` | 用 UTF-8、排序键和紧凑分隔符序列化后返回 64 位 SHA-256 十六进制；同一 JSON 语义产生稳定摘要。 |

### 阶段 1 模型校验函数

| 函数 | 作用与拒绝条件 |
|---|---|
| `pc_manager_agent.domain.file_analysis.FileAnalysisIntentDraft.require_scope_and_analysis()` | Pydantic 后置校验；授权根和分析类型都必须非空且无重复。失败抛 `ValidationError`，不会进入编译器。 |
| `pc_manager_agent.domain.file_analysis.FileAnalysisPlan.validate_task_plan()` | 确认嵌套 `TaskPlan` 修改/删除数量均为零，且所有步骤都是 R0；矛盾计划无法实例化。 |
| `pc_manager_agent.domain.file_analysis.FileAnalysisPlan.canonical_digest()` | 返回嵌套任务计划的规范确认摘要；不单独创造另一套易漂移的摘要算法。 |
| `pc_manager_agent.domain.reports.ScanRequest.require_session_for_streaming()` | 当 `retain_files=False` 时强制要求 `session_id`，确保流式批次可归属到唯一 SQLite 会话。 |
| `pc_manager_agent.providers.llm.base.AnalysisNarrativeDraft.reject_numeric_claims(value)` | 拒绝任何观察文本中的数字字符，防止模型生成与本地测量值冲突的数量；无数字时原样返回元组。 |

### 计划编译与模型规划

#### `pc_manager_agent.orchestration.file_analysis_planner.FileAnalysisPlanCompiler.__init__`

```python
FileAnalysisPlanCompiler(
    authorization: AuthorizedPathService,
    registry: ToolRegistry,
    *, max_files: int, timeout_seconds: float, batch_size: int = 250,
) -> None
```

- **作用：** 保存确定性的授权/注册表边界和全任务资源上限。模型不能修改这些上限。
- **行为：** 构造不读取目录；`compile()` 才解析 ID 和检查需要的分析器是否已注册。

#### `pc_manager_agent.orchestration.file_analysis_planner.FileAnalysisPlanCompiler.compile`

```python
compile(user_goal: str, draft: FileAnalysisIntentDraft) -> FileAnalysisPlan
```

- **作用：** 把不可信意图变成完整可执行计划。只在本地解析根 ID，拒绝重叠根，生成会话
  ID，为每根分配文件/时间限额，派生禁止子树，为所选分析生成固定工具和参数。
- **返回与异常：** 返回只含 R0、零修改/删除、需要计划确认的不可变计划。未知 ID、重叠
  根、缺失注册工具、路径复验或模型校验失败均中止。
- **安全：** 不解析自然语言、不接受供应商工具参数，重复分析默认启用逐字节验证。

| 函数 | 详细作用 |
|---|---|
| `pc_manager_agent.orchestration.file_analysis_planner.FileAnalysisPlanCompiler._reject_overlapping_roots(roots)` | 两两用组件关系比较根；相等、父子嵌套都抛 `ValueError`，避免重复扫描、重复计数和模糊限制分配。 |
| `pc_manager_agent.orchestration.file_analysis_planner.FileAnalysisPlanCompiler._analysis_description(analysis)` | 从封闭 `AnalysisType` 枚举返回面向用户的固定说明；不存在模型生成的描述或命令。 |

#### `pc_manager_agent.orchestration.file_analysis_planner.FileAnalysisPlanner.__init__`

```python
FileAnalysisPlanner(provider, authorization, registry, compiler, external_consent) -> None
```

- **作用：** 组合可替换供应商和确定性编译器；供应商只产生意图，不能执行。

| 函数 | 详细作用、输入/输出与外发边界 |
|---|---|
| `pc_manager_agent.orchestration.file_analysis_planner.FileAnalysisPlanner.build_provider_request(user_goal, root_ids=None)` | 从所选或全部授权记录构造目标、标签/不透明 ID、允许分析和实际注册工具名；没有根抛 `ValueError`。返回值不含真实路径。 |
| `pc_manager_agent.orchestration.file_analysis_planner.FileAnalysisPlanner.request_external_consent(user_goal, root_ids=None)` | 为上一个函数产生的精确 JSON 请求创建 PLANNING 确认，说明会发送/不会发送的字段；不调用供应商。 |
| `pc_manager_agent.orchestration.file_analysis_planner.FileAnalysisPlanner.plan(user_goal, confirmation_id, root_ids=None)` | 异步重建同一请求，要求精确批准，调用供应商 Schema 输出，再交给编译器。返回计划、供应商和追踪 ID；任何确认/Schema/授权错误都中止。 |

### 文件分析编排器

#### `pc_manager_agent.orchestration.file_analysis.FileAnalysisOrchestrator.__init__`

```python
FileAnalysisOrchestrator(*, registry, validator, confirmation, audit, results) -> None
```

- **作用：** 组合阶段 1 的最终权限边界；保存 Git commit 环境值用于审计。
- **安全：** 没有注册表外执行路径；构造不创建会话或读取文件。

#### `pc_manager_agent.orchestration.file_analysis.FileAnalysisOrchestrator.review`

```python
review(plan, *, provider=None, provider_request_id=None) -> SafetyReview
```

- **作用：** 调用独立专用审查器，并把完整计划、批准/拒绝结论及可选模型追踪写入审计。
- **副作用与异常：** 只写审计，不执行工具。审计写失败向上传播并阻止后续可信执行。

#### `pc_manager_agent.orchestration.file_analysis.FileAnalysisOrchestrator.request_plan_confirmation`

```python
request_plan_confirmation(plan: FileAnalysisPlan) -> ConfirmationRequest
```

- **作用：** 再次安全审查后创建计划确认，摘要明确根、分析、阈值和“不移动/重命名/删除”。
- **异常：** 审查拒绝时抛 `FileAnalysisOrchestrationError`，不生成可批准对象。

#### `pc_manager_agent.orchestration.file_analysis.FileAnalysisOrchestrator.resolve_plan_confirmation`

```python
resolve_plan_confirmation(confirmation_id, approved, plan) -> ConfirmationRequest
```

- **作用：** 按当前计划规范摘要解析批准/拒绝并写审计。变更、过期、重复处理或未知 ID
  由确认服务拒绝。

#### `pc_manager_agent.orchestration.file_analysis.FileAnalysisOrchestrator.execute`

```python
execute(plan: FileAnalysisPlan, cancellation: CancellationToken) -> FileAnalysisReport
```

- **作用：** 重审并要求精确批准，创建本地会话，严格按计划经注册表执行，验证每个输出，
  响应取消，计算 ALL/ANY 候选和分类，验证最终报告并审计终态。
- **错误：** 工具、路径、SQLite、审计、输出类型或报告校验错误会标记会话 FAILED、记录
  工具/任务失败并重新抛出；不会猜测结果或继续后续步骤。
- **副作用与安全：** 仅写应用 SQLite；内容读取只可能发生在已批准的重复候选哈希中。

| 私有函数 | 详细作用 |
|---|---|
| `pc_manager_agent.orchestration.file_analysis.FileAnalysisOrchestrator._record_tool_started(plan, step_id, tool_name, arguments)` | 在调用注册表前记录确切步骤、脱敏参数、R0 和已批准状态；失败会阻止调用。 |
| `pc_manager_agent.orchestration.file_analysis.FileAnalysisOrchestrator._record_tool_completed(plan, step_id, tool_name, arguments, result, duration_ms)` | 记录安全压缩后的真实结果、验证通过和耗时；不会把候选文件内容写入审计。 |
| `pc_manager_agent.orchestration.file_analysis.FileAnalysisOrchestrator._record_tool_failed(plan, step_id, tool_name, arguments, error, duration_ms)` | 记录失败工具、异常类型/消息、验证失败和耗时，然后由调用方终止整个任务。 |
| `pc_manager_agent.orchestration.file_analysis.FileAnalysisOrchestrator._validate_step_result(tool_name, result)` | 将四个允许工具映射到确切 Pydantic 结果类型；不匹配抛 `FileAnalysisOrchestrationError`。 |
| `pc_manager_agent.orchestration.file_analysis.FileAnalysisOrchestrator._result_cancelled(result)` | 对扫描读取 `summary.cancelled`，对分析读取类型化 `cancelled`；供执行循环决定是否停止。 |
| `pc_manager_agent.orchestration.file_analysis.FileAnalysisOrchestrator._safe_tool_result(result)` | 把结果缩减为计数、字节数、置信度或问题数，避免审计中复制路径列表/哈希组；未知类型返回空映射。 |
| `pc_manager_agent.orchestration.file_analysis.FileAnalysisOrchestrator._aggregate_scan_summaries(summaries, forced_status, duration_ms)` | 合并多根计数和字节；终态优先级为强制状态、CANCELLED、TIMED_OUT、TRUNCATED、COMPLETED。返回一致 `ScanSummary`。 |
| `pc_manager_agent.orchestration.file_analysis.FileAnalysisOrchestrator._verify_report(plan, report)` | 验证会话 ID、计划 ID 和候选数量不超过扫描数量；违反即拒绝把报告标为可信。 |

### 聚合结果说明

#### `pc_manager_agent.orchestration.explanation.FileAnalysisExplainer.__init__`

```python
FileAnalysisExplainer(provider: LLMProvider, external_consent: ExternalDataConsentService)
```

- **作用：** 组合可替换供应商与外发确认，不持有结果仓库或文件访问能力。

| 函数 | 详细作用、返回与安全约束 |
|---|---|
| `pc_manager_agent.orchestration.explanation.FileAnalysisExplainer.build_request(plan, summary)` | 构造仅含 `FileAnalysisSummary`、阈值和分析类型的请求；不含路径、名称、问题明细或内容。 |
| `pc_manager_agent.orchestration.explanation.FileAnalysisExplainer.request_external_consent(plan, summary)` | 对精确聚合请求创建 EXPLANATION 确认，明确列出发送和排除字段；不发起网络。 |
| `pc_manager_agent.orchestration.explanation.FileAnalysisExplainer.explain(plan, summary, confirmation_id)` | 异步复核确认、调用供应商取得无数字定性观察，再调用本地渲染器。确认/供应商错误向上传播。 |
| `pc_manager_agent.orchestration.explanation.render_analysis_explanation(summary, narrative=None)` | 用本地测量值格式化文件、目录、字节、候选和错误数，可追加模型定性观察；返回纯文本，不访问网络/文件。 |

### 分析结果持久化

#### `pc_manager_agent.persistence.analysis_results.AnalysisResultRepository.__init__`

```python
AnalysisResultRepository(database_path: Path) -> None
```

- **作用：** 创建启用安全 SQLite 设置的 SQLAlchemy 引擎和会话工厂；尚未建表。
- **副作用：** 数据库父目录可由公共引擎工厂创建；必须调用 `initialize()` 才能读写。

| 函数 | 详细作用、输入/输出、异常与资源约束 |
|---|---|
| `pc_manager_agent.persistence.analysis_results.AnalysisResultRepository.initialize()` | 创建会话/文件/问题表，并删除上次崩溃遗留的 RUNNING 应用临时会话；SQL 错误包装为 `AnalysisResultStoreError`。不触碰用户文件。 |
| `pc_manager_agent.persistence.analysis_results.AnalysisResultRepository.create_session(session_id, roots)` | 在扫描前创建唯一 RUNNING 会话并以 JSON 保存根列表；空根抛 `ValueError`，重复 ID/SQL 故障失败关闭。 |
| `pc_manager_agent.persistence.analysis_results.AnalysisResultRepository.store_batch(batch)` | 把一个 `ScanBatch` 转为 ORM 行并在单事务追加；空批次无操作。批量大小由扫描请求上限控制，不长期保留模型。 |
| `pc_manager_agent.persistence.analysis_results.AnalysisResultRepository.store_issues(session_id, issues)` | 追加问题代码、最多 1000 字符消息和可选路径；空序列无操作。它记录失败而不改变授权范围。 |
| `pc_manager_agent.persistence.analysis_results.AnalysisResultRepository.complete_scan(session_id, summary)` | 给已知会话写完成时间、真实终态、计数、字节、问题和耗时；未知会话抛 `AnalysisResultStoreError`。 |
| `pc_manager_agent.persistence.analysis_results.AnalysisResultRepository.iter_records(session_id, batch_size=500)` | 按递增主键进行 keyset 分页并生成 `StoredFileRecord` 元组；批次限制 1–2000，避免 OFFSET 和全量内存。迭代期间 SQL 错误向上传播。 |
| `pc_manager_agent.persistence.analysis_results.AnalysisResultRepository.duplicate_sizes(session_id)` | 用 SQL 分组返回出现至少两次且大于零的大小，作为重复检测第一阶段；空文件不会进入哈希。 |
| `pc_manager_agent.persistence.analysis_results.AnalysisResultRepository.record_count(session_id)` | 返回会话元数据行数；无行返回 0，SQL 错误包装。供进度总量使用。 |
| `pc_manager_agent.persistence.analysis_results.AnalysisResultRepository.records_by_size(session_id, size_bytes)` | 返回一个同尺寸候选组的类型化记录；只供后续快速/完整哈希，不声称重复。 |
| `pc_manager_agent.persistence.analysis_results.AnalysisResultRepository.mark_large(record_ids)` | 在单个受界更新中把给定记录标为大文件；空 ID 列表无操作。只改应用索引。 |
| `pc_manager_agent.persistence.analysis_results.AnalysisResultRepository.mark_inactive(record_id, assessment)` | 保存置信度、证据和阈值；只有分析器判为“疑似”才调用，不保存“无用/可删除”建议。 |
| `pc_manager_agent.persistence.analysis_results.AnalysisResultRepository.mark_duplicate(record_ids, group_id)` | 给经过内容验证的记录写中性组 ID；空列表无操作，不选择原件/副本。 |
| `pc_manager_agent.persistence.analysis_results.AnalysisResultRepository.finalize_matches(session_id, analyses, match_mode)` | 先清除旧匹配，再按所选分析条件用 SQL `AND`/`OR` 标记最终候选；无分析条件抛 `ValueError`。 |
| `pc_manager_agent.persistence.analysis_results.AnalysisResultRepository.page_candidates(session_id, offset=0, limit=200, category=None, search='', minimum_size_bytes=0, sort_by='size_bytes', descending=True)` | 返回最多 1000 行的已匹配结果，支持类型、转义后的名称/路径搜索、最小大小和允许列排序；未知排序列抛 `ValueError`，负 offset/大小安全钳制。 |
| `pc_manager_agent.persistence.analysis_results.AnalysisResultRepository.matching_totals(session_id)` | 用 SQL 返回候选数量和总字节；无候选返回 `(0, 0)`。 |
| `pc_manager_agent.persistence.analysis_results.AnalysisResultRepository.iter_matching(session_id, batch_size=500)` | 按主键 keyset 生成所有最终候选批次，批次 1–2000；供大报告流式导出。 |
| `pc_manager_agent.persistence.analysis_results.AnalysisResultRepository.category_summaries(session_id)` | 在 SQLite 中按集中分类枚举聚合候选数量/字节，返回稳定排序的 `CategorySummary` 元组。 |
| `pc_manager_agent.persistence.analysis_results.AnalysisResultRepository.list_issues(session_id, limit=1000)` | 返回按发现顺序、最多 5000 条问题；将可选路径恢复为 `Path`。 |
| `pc_manager_agent.persistence.analysis_results.AnalysisResultRepository.delete_session(session_id)` | 删除应用拥有的一个分析会话及外键级联元数据；不删除扫描根或报告。当前仅用于临时结果生命周期，不是用户文件工具。 |
| `pc_manager_agent.persistence.analysis_results.AnalysisResultRepository.close()` | 释放引擎连接并标为未初始化；不删除数据库。 |
| `pc_manager_agent.persistence.analysis_results.AnalysisResultRepository._mark_boolean(record_ids, field_name)` | 私有布尔批量更新入口；空列表短路，非空委托统一事务函数。字段名只由受信代码传入。 |
| `pc_manager_agent.persistence.analysis_results.AnalysisResultRepository._execute_update(statement, message)` | 要求仓库已初始化，在事务内执行已构造 SQLAlchemy 语句；SQL 错误用调用方消息包装。 |
| `pc_manager_agent.persistence.analysis_results.AnalysisResultRepository._require_initialized()` | 未初始化或关闭后抛 `AnalysisResultStoreError`，防止默默使用不可信存储。 |
| `pc_manager_agent.persistence.analysis_results.AnalysisResultRepository._metadata_to_row(session_id, value)` | 把不可变 `FileMetadata` 映射为 ORM 行，保留时间、属性、分类和可选身份；初始化所有分析标志为 false。 |
| `pc_manager_agent.persistence.analysis_results.AnalysisResultRepository._row_to_record(row)` | 把 ORM 行恢复为 `StoredFileRecord`，重建枚举、Path、可选闲置证据和整数身份；SQLite 时间由 `_as_utc` 修正。 |
| `pc_manager_agent.persistence.analysis_results._as_utc(value)` | SQLite 返回无时区时把数值解释为原写入的 UTC；已有时区则转换 UTC，避免闲置/变更比较出现本地时区偏差。 |

### 授权持久化

#### `pc_manager_agent.persistence.authorized_paths.AuthorizedPathRepository.__init__`

```python
AuthorizedPathRepository(database_path: Path) -> None
```

- **作用：** 创建隔离的授权 ORM 引擎/会话工厂；必须显式初始化。

| 函数 | 详细作用、返回与异常 |
|---|---|
| `pc_manager_agent.persistence.authorized_paths.AuthorizedPathRepository.initialize()` | 创建唯一规范路径的授权表并标记可用；SQL 错误包装为 `AuthorizedPathStoreError`。 |
| `pc_manager_agent.persistence.authorized_paths.AuthorizedPathRepository.add(record)` | 在事务内插入完整不可变记录并原样返回；路径或主键冲突抛“已配置”错误，其他 SQL 错误失败关闭。调用方必须先做路径策略校验。 |
| `pc_manager_agent.persistence.authorized_paths.AuthorizedPathRepository.remove(path_id)` | 按 UUID 删除一行；返回是否存在。不会删除对应目录。 |
| `pc_manager_agent.persistence.authorized_paths.AuthorizedPathRepository.list(kind=None)` | 可选按 AUTHORIZED/FORBIDDEN 筛选，按创建时间返回模型元组；无过滤时返回全部。 |
| `pc_manager_agent.persistence.authorized_paths.AuthorizedPathRepository.get(path_id)` | 返回单项模型或 `None`；不把 ORM 行泄露到业务层。 |
| `pc_manager_agent.persistence.authorized_paths.AuthorizedPathRepository.close()` | 释放连接并标为不可用，不删除设置。 |
| `pc_manager_agent.persistence.authorized_paths.AuthorizedPathRepository._require_initialized()` | 在任意 CRUD 前执行；未初始化抛 `AuthorizedPathStoreError`。 |
| `pc_manager_agent.persistence.authorized_paths.AuthorizedPathRepository._to_model(row)` | 将字符串 UUID/路径/枚举和 UTC 时间恢复为严格 `AuthorizedPath`。 |
| `pc_manager_agent.persistence.authorized_paths._as_utc(value)` | 恢复 SQLite 丢失的 UTC 时区信息，保证持久化前后模型可比较。 |

### Windows 平台辅助

| 函数 | 详细作用、输入/输出与安全约束 |
|---|---|
| `pc_manager_agent.platform_support.windows.explorer.WindowsExplorerService.__init__(authorization)` | 注入授权服务；构造不启动进程。Explorer 服务没有通用命令执行能力。 |
| `pc_manager_agent.platform_support.windows.explorer.WindowsExplorerService.select_file(path)` | 先执行 `require_authorized_file`，再以固定 `explorer.exe` 和参数数组 `/select,<path>` 启动并设置超时；不使用 shell。不存在/越界/进程错误向上传播。 |
| `pc_manager_agent.platform_support.windows.explorer.WindowsExplorerService._explorer_path()` | 通过 `GetWindowsDirectoryW` 定位系统 Explorer，验证它是现有文件并返回绝对路径；不搜索可被污染的进程 `PATH`。API/文件失败抛 `ExplorerOpenError`。 |
| `pc_manager_agent.platform_support.windows.path_info.is_network_path(path)` | UNC/设备语法直接返回 `True`；否则调用 `GetDriveTypeW` 判断根是否 `DRIVE_REMOTE`。不确定时策略调用方可失败关闭。 |
| `pc_manager_agent.platform_support.windows.path_info.last_access_time_reliable(root)` | 仅在 NTFS 且注册表 `NtfsDisableLastAccessUpdate` 明确为 0/1 时返回可靠布尔值；其他文件系统、自动管理值、权限/API 错误返回 `None`，促使降低置信度。 |
| `pc_manager_agent.platform_support.windows.path_info._filesystem_name(root)` | 通过固定 Windows API 读取卷文件系统名称；任何不确定性返回空字符串，不抛出并伪称 NTFS。 |

### LLM 阶段 1 接口

| 函数 | 详细作用、返回与异常 |
|---|---|
| `pc_manager_agent.providers.llm.base.LLMProvider.create_file_analysis_intent(request)` | 供应商中立异步扩展点；返回 `ProviderIntentResult`。基类默认抛 `NotImplementedError`，实现仍需本地编译/审查。 |
| `pc_manager_agent.providers.llm.base.LLMProvider.explain_file_analysis(request)` | 聚合说明异步扩展点；返回无数字 `ProviderNarrativeResult`。基类默认不实现。 |
| `pc_manager_agent.providers.llm.openai_provider.OpenAILLMProvider.create_file_analysis_intent(request)` | 调用 OpenAI Responses 结构化解析为 `FileAnalysisIntentDraft`，返回供应商和请求 ID；连接、API 或空解析包装为 `OpenAIProviderError`。它不解析真实路径或执行。 |
| `pc_manager_agent.providers.llm.openai_provider.OpenAILLMProvider.explain_file_analysis(request)` | 结构化解析 `AnalysisNarrativeDraft`；请求仅含聚合。数字声明由模型校验拒绝，SDK 错误统一包装。 |

### 报告导出

#### `pc_manager_agent.reporting.exporter.ReportExporter.__init__`

```python
ReportExporter(
    results: AnalysisResultRepository,
    *, on_export: Callable[[ReportExportResult], None] | None = None,
) -> None
```

- **作用：** 注入分页结果源和可选审计回调；构造不创建文件。

#### `pc_manager_agent.reporting.exporter.ReportExporter.export`

```python
export(target, format, plan, report) -> ReportExportResult
```

- **作用：** 验证用户选定的绝对本地新路径，流式写 CSV/JSON，读取最终文件大小，触发
  审计并返回已验证结果。
- **异常与安全：** 相对/模糊/网络/错误后缀/不存在父目录/已有目标均抛
  `ReportExportError`。写入失败也包装该错误并保留可能的部分文件；从不覆盖或删除。

| 私有函数 | 详细作用 |
|---|---|
| `pc_manager_agent.reporting.exporter.ReportExporter._validate_target(target, format)` | 检查绝对路径、无 `..`/尾点空格、目标不存在、父目录存在、本地驱动和精确 `.csv`/`.json` 后缀；返回原目标。 |
| `pc_manager_agent.reporting.exporter.ReportExporter._write_csv(target, plan, report)` | 以 `x` 独占模式、UTF-8 BOM 和固定字段写表头，分页写所有候选，返回行数。包含时间/阈值/证据但不读文件正文。 |
| `pc_manager_agent.reporting.exporter.ReportExporter._write_json(target, plan, report)` | 以 `x` 模式写结构化头和流式 `files` 数组，避免全量内存；返回候选行数。 |
| `pc_manager_agent.reporting.exporter.ReportExporter._row(record, plan, report)` | 把一条真实索引记录转换为稳定导出字段，闲置和重复只使用已验证注解，不生成删除建议。 |

### 阶段 1 专用安全审查与路径扩展

#### `pc_manager_agent.safety.file_analysis_validator.FileAnalysisSafetyValidator.__init__`

```python
FileAnalysisSafetyValidator(reviewer, registry, authorization) -> None
```

- **作用：** 在通用 `SafetyReviewer` 外组合阶段 1 语义约束；构造不执行计划。

#### `pc_manager_agent.safety.file_analysis_validator.FileAnalysisSafetyValidator.review`

```python
review(plan: FileAnalysisPlan) -> SafetyReview
```

- **作用：** 汇总通用问题，并检查零修改/删除、匹配模式摘要、授权 ID、精确 scope、四工具
  白名单、R0/read-only manifest、会话 ID、扫描根/排除、大小/闲置阈值和分析集合。
- **返回：** 任一问题使 `approved=False`，所有问题以机器可读代码返回；不自动修复或猜测。

| 函数 | 详细作用 |
|---|---|
| `pc_manager_agent.safety.path_policy.path_is_within(path, root)` | 对绝对、规范大小写路径使用 `commonpath` 判断等于或位于根下；跨驱动 `ValueError` 返回 `False`，不使用易绕过的字符串前缀。 |
| `pc_manager_agent.safety.path_policy._has_ambiguous_segment(path)` | 检查任一 Windows 段尾随空格/点，发现会被 Win32 规范化的歧义则返回 `True`。 |
| `pc_manager_agent.safety.path_policy._is_unc_or_device_path(path)` | 在解析前识别 `\\server`、`\\?\`、`\\.\` 等 UNC/扩展设备语法。 |
| `pc_manager_agent.safety.path_policy.PathPolicy.default_forbidden_roots()` | 基于当前用户/Windows 环境构造凭据、浏览器、密码管理器、SSH、钱包、Personal Vault 和系统安全根；返回规范元组。 |
| `pc_manager_agent.safety.path_policy.PathPolicy.for_authorized_roots(roots, extra_forbidden=(), network_path_detector=None)` | 只从显式根创建多根策略，并合并内置/用户禁止目录和网络检测器；空根由构造器拒绝。 |
| `pc_manager_agent.safety.path_policy.PathPolicy.validate_file(path)` | 在每次内容读取前要求绝对无穿越本地普通文件，拒绝歧义、重解析组件、越界/保护和网络路径；成功返回严格解析路径。 |
| `pc_manager_agent.safety.path_policy.PathPolicy.canonicalize_authorization_root(path, extra_forbidden=(), network_path_detector=None)` | 通过临时精确策略复用完整根验证，返回可持久化规范目录；不会扩大到父目录。 |
| `pc_manager_agent.safety.path_policy.PathPolicy._reject_reparse_components(path)` | 从锚点逐段检查所有已存在组件；任一符号链接/联接/重解析立即抛 `PathSecurityError`，缺失组件停止逐段检查并由严格解析处理。 |

### 文件分类、扫描与分析工具

#### `pc_manager_agent.tools.file_tools.classifier.FileTypeClassifier.classify`

```python
classify(path: Path) -> FileCategory
```

- **作用：** 对扩展名不区分大小写，在唯一集中映射中分类图片、视频、音频、文档、PDF、
  压缩包、安装包、磁盘镜像、代码、数据库、备份和其他。
- **安全：** 不打开文件、不嗅探内容；未知扩展名返回 `OTHER`。

#### `DirectoryScannerTool` 阶段 1 私有辅助

前文的 `DirectoryScannerTool.__init__` 现新增 `classifier`、`batch_consumer`、
`issue_consumer` 和 `progress_callback` 注入项；`ScanRequest` 新增会话、批次和是否保留
内存结果。公开 `execute()` 仍要求 `ScanRequest` 并在执行时复验根。

| 函数 | 详细作用、错误和资源行为 |
|---|---|
| `pc_manager_agent.tools.file_tools.scanner.DirectoryScannerTool._scan.record_issue(issue)` | `_scan` 内部闭包；递增总问题数、加入有界问题批次，仅在非流式模式保留完整问题；达到批次大小即提交。 |
| `pc_manager_agent.tools.file_tools.scanner.DirectoryScannerTool._validated_exclusions(paths, root)` | 要求每项为绝对、无穿越且位于当前根内，用规范本地路径返回元组；越界/相对路径抛 `ValueError`。 |
| `pc_manager_agent.tools.file_tools.scanner.DirectoryScannerTool._flush_batch(request, batch)` | 非空且有消费者/会话时构造不可变 `ScanBatch`，调用消费者后清空列表；即使无消费者也清空，保证内存有界。 |
| `pc_manager_agent.tools.file_tools.scanner.DirectoryScannerTool._flush_issues(request, issues)` | 以会话 ID 提交问题元组并清空工作列表；无问题短路。消费者异常向上传播，避免假装持久化成功。 |
| `pc_manager_agent.tools.file_tools.scanner.DirectoryScannerTool._emit_progress(request, files_seen, directories_seen, total_size, issue_count)` | 构造类型化 `ScanProgress`；只有注入回调才发射。未知总数时不虚构百分比。 |

#### 大文件分析器

##### `pc_manager_agent.tools.file_tools.large_file_analyzer.LargeFileAnalyzer.__init__`

```python
LargeFileAnalyzer(repository, *, progress_callback=None) -> None
```

- **作用：** 注入分页结果库/进度回调并建立 R0、只读、可取消 manifest。

| 函数 | 详细作用与结果 |
|---|---|
| `pc_manager_agent.tools.file_tools.large_file_analyzer.LargeFileAnalyzer.manifest` | 只读属性，返回 `file.analyze.large` 的不可变安全清单。 |
| `pc_manager_agent.tools.file_tools.large_file_analyzer.LargeFileAnalyzer.execute(request, cancellation)` | 注册表入口；要求精确 `LargeFileAnalysisRequest`，否则 `TypeError`，然后委托 `analyze`。 |
| `pc_manager_agent.tools.file_tools.large_file_analyzer.LargeFileAnalyzer.analyze(request, cancellation)` | 分页读取元数据，以“等于或大于”阈值标记，累计数量/字节和按扩展名、目录、大小带统计；每批检查取消/发进度。返回 `LargeFileAnalysisResult`。 |
| `pc_manager_agent.tools.file_tools.large_file_analyzer.LargeFileAnalyzer._size_band(size_bytes)` | 返回 `under_1_gib`、`1_to_5_gib`、`5_to_10_gib` 或 `10_gib_and_above`，边界按二进制 GiB 计算。 |

#### 疑似长期未使用分析器

##### `pc_manager_agent.tools.file_tools.inactive_file_analyzer.InactiveFileAnalyzer.__init__`

```python
InactiveFileAnalyzer(
    repository, *, atime_reliability=None, now=None, progress_callback=None,
) -> None
```

- **作用：** 注入结果库、Windows atime 可靠性探针、UTC 时钟和进度；默认不假设 atime
  可靠。

| 函数 | 详细作用与保守语义 |
|---|---|
| `pc_manager_agent.tools.file_tools.inactive_file_analyzer.InactiveFileAnalyzer.manifest` | 返回 `file.analyze.inactive` 的 R0、只读、可取消清单。 |
| `pc_manager_agent.tools.file_tools.inactive_file_analyzer.InactiveFileAnalyzer.execute(request, cancellation)` | 要求 `InactiveFileAnalysisRequest` 后委托分页分析；错误类型抛 `TypeError`。 |
| `pc_manager_agent.tools.file_tools.inactive_file_analyzer.InactiveFileAnalyzer.analyze(request, cancellation)` | 计算 UTC 阈值，按扫描根缓存 atime 探针，对每条调用 `assess`，只保存 possibly_inactive，汇总置信度/字节并支持取消。 |
| `pc_manager_agent.tools.file_tools.inactive_file_analyzer.InactiveFileAnalyzer.assess(metadata, threshold, inactive_days, atime_reliable)` | 只有访问和修改时间都旧才成为候选；创建时间和 atime 策略调节 HIGH/MEDIUM/LOW，证据不足返回 UNKNOWN/false。返回证据文本，不评价价值。 |

#### 安全哈希器

##### `pc_manager_agent.tools.file_tools.hashing.SafeFileHasher.__init__`

```python
SafeFileHasher(path_policy: PathPolicy, *, chunk_size: int = 1_048_576) -> None
```

- **作用：** 保存执行时文件策略并把块大小钳制为至少 4096 字节；不打开文件。

| 函数 | 详细作用、验证与取消行为 |
|---|---|
| `pc_manager_agent.tools.file_tools.hashing._Digest.update(data)` | 私有协议方法，描述 hashlib 兼容摘要对象所需的最小 `update(bytes)` 接口；无实现。 |
| `pc_manager_agent.tools.file_tools.hashing.SafeFileHasher.quick_hash(record, cancellation, sample_bytes=65536)` | 摘要包含文件大小；小文件哈希全文，大文件哈希首尾有界样本。打开前/后验证身份并在读块间检查取消；返回 SHA-256 十六进制，仅用于候选过滤。 |
| `pc_manager_agent.tools.file_tools.hashing.SafeFileHasher.sha256(record, cancellation)` | 对已批准普通文件分块计算完整 SHA-256，读前后验证；文件变化/取消抛类型化错误，不返回部分摘要。 |
| `pc_manager_agent.tools.file_tools.hashing.SafeFileHasher.byte_equal(left, right, cancellation)` | 尺寸不同直接 false；否则同时打开并分块比较，检查取消和双方身份。相等返回 true，不修改位置以外状态。 |
| `pc_manager_agent.tools.file_tools.hashing.SafeFileHasher._open_verified(record)` | 拒绝 offline 占位符，经 `PathPolicy.validate_file` 后以二进制只读打开，立即用句柄复验；失败关闭句柄再抛出。 |
| `pc_manager_agent.tools.file_tools.hashing.SafeFileHasher._hash_stream(handle, digest, cancellation)` | 分块读到 EOF，每块前检查取消并更新摘要；不缓存完整内容。 |
| `pc_manager_agent.tools.file_tools.hashing.SafeFileHasher._verify_open_handle(handle, record)` | 用 `fstat` 比较尺寸、修改时间和可用 file/device ID；不一致抛 `FileChangedDuringScanError`。未知 ID 不伪造检查。 |
| `pc_manager_agent.tools.file_tools.hashing.SafeFileHasher._raise_if_cancelled(cancellation)` | 已请求取消时抛 `ScanCancelledError`，供上层转为正常 CANCELLED 终态。 |

#### 重复文件分析器

##### `pc_manager_agent.tools.file_tools.duplicate_analyzer.DuplicateFileAnalyzer.__init__`

```python
DuplicateFileAnalyzer(repository, hasher, *, progress_callback=None) -> None
```

- **作用：** 组合分页元数据、受控内容读取和进度，建立 R0 重复分析 manifest。

| 函数 | 详细作用与真实性约束 |
|---|---|
| `pc_manager_agent.tools.file_tools.duplicate_analyzer.DuplicateFileAnalyzer.manifest` | 返回 `file.analyze.duplicates` 的 R0、只读、可取消清单。 |
| `pc_manager_agent.tools.file_tools.duplicate_analyzer.DuplicateFileAnalyzer.execute(request, cancellation)` | 要求 `DuplicateFileAnalysisRequest`，再执行分阶段检测；错误类型抛 `TypeError`。 |
| `pc_manager_agent.tools.file_tools.duplicate_analyzer.DuplicateFileAnalyzer.analyze(request, cancellation)` | 查询非空同尺寸组，逐组快速哈希→完整 SHA-256→可选字节比较，写中性组 ID，汇总文件/可回收理论字节/问题；取消返回 `cancelled=True`。 |
| `pc_manager_agent.tools.file_tools.duplicate_analyzer.DuplicateFileAnalyzer._analyze_same_size(records, cancellation, sample_bytes, byte_verify, issues)` | 先按快速指纹筛掉不同内容，再按完整 SHA-256 分组；可选以首文件为参照逐字节确认。只有至少两条验证相等才返回组。 |
| `pc_manager_agent.tools.file_tools.duplicate_analyzer.DuplicateFileAnalyzer._safe_hash(record, cancellation, issues, quick, sample_bytes)` | 调用快速或完整哈希；取消错误原样抛出，文件变化/权限/IO 错误转成带路径 `ScanIssue` 并返回 `None`，不让单文件失败崩溃整个分析。 |

### 阶段 1 GUI 控制器

#### `pc_manager_agent.ui.analysis_tab.FileAnalysisTab.__init__`

```python
FileAnalysisTab(runtime: ApplicationRuntime) -> None
```

- **作用：** 初始化页面状态、建立控件/信号并从本地刷新授权列表。没有工具执行逻辑。
- **线程：** 规划、分析和说明均委托 `QRunnable`；结果分页查询发生在 UI 操作边界。

| 函数 | GUI 行为、委托边界与错误处理 |
|---|---|
| `pc_manager_agent.ui.analysis_tab.FileAnalysisTab._build_ui()` | 构造授权/禁止列表、目标、阈值/分析、计划/确认、进度、筛选、九列表格、分页/定位/导出/说明控件并连接“输入变化→失效计划”。不读取文件。 |
| `pc_manager_agent.ui.analysis_tab.FileAnalysisTab.refresh_paths()` | 从授权服务重建两列表，保留 UUID 在 `UserRole`，默认选择首个授权根；用 `_building` 防止刷新本身使计划误失效。 |
| `pc_manager_agent.ui.analysis_tab.FileAnalysisTab._add_authorized()` | 打开目录选择器，具体确认只读授权和 favorite，调用授权服务；取消无副作用，错误用友好警告显示。 |
| `pc_manager_agent.ui.analysis_tab.FileAnalysisTab._add_forbidden()` | 选择并确认要始终阻止的目录，再调用 `add_forbidden`；不开始扫描。 |
| `pc_manager_agent.ui.analysis_tab.FileAnalysisTab._remove_authorized()` | 用固定提示委托 `_remove_selected`；只移除应用授权记录，不操作目录。 |
| `pc_manager_agent.ui.analysis_tab.FileAnalysisTab._remove_forbidden()` | 委托移除一项用户禁止记录；需要明确确认。 |
| `pc_manager_agent.ui.analysis_tab.FileAnalysisTab._remove_selected(widget, prompt)` | 要求当前项存在并二次确认，从 `UserRole` 解析 UUID 调用服务；成功刷新并使旧计划失效。无选择或异常安全提示。 |
| `pc_manager_agent.ui.analysis_tab.FileAnalysisTab.start_planning(goal=None)` | 校验目标/根并创建服务。未配置模型时根据控件确定性编译；配置模型时先询问精确外发确认，再启动 `PlannerWorker`。不确认时不发送。 |
| `pc_manager_agent.ui.analysis_tab.FileAnalysisTab._manual_intent(root_ids)` | 将勾选分析、MB→字节、闲置天数和 ALL/ANY 转为严格 `FileAnalysisIntentDraft`；空分析由模型校验拒绝。 |
| `pc_manager_agent.ui.analysis_tab.FileAnalysisTab._planning_completed(value)` | 收窄跨线程结果；类型错误走失败 UI，成功交给 `_accept_planning_result`。 |
| `pc_manager_agent.ui.analysis_tab.FileAnalysisTab._accept_planning_result(result)` | 调用编排器审查并要求批准，创建确认，展示完整 JSON/R0 声明并启用确认/拒绝；审查问题不会被 UI 忽略。 |
| `pc_manager_agent.ui.analysis_tab.FileAnalysisTab._planning_failed(message)` | 清理工作器引用、重新启用规划并显示“生成计划失败”；不生成替代计划。 |
| `pc_manager_agent.ui.analysis_tab.FileAnalysisTab._approve_plan()` | 对当前精确确认调用编排器 resolve；成功后只启用“开始分析”，异常不执行。 |
| `pc_manager_agent.ui.analysis_tab.FileAnalysisTab._reject_plan()` | 记录拒绝并禁用执行；状态明确说明未读取文件。 |
| `pc_manager_agent.ui.analysis_tab.FileAnalysisTab._run_analysis()` | 当前有计划且无活动任务时创建 `FileAnalysisWorker`，连接进度/成功/失败，设置不确定进度并在线程池启动。 |
| `pc_manager_agent.ui.analysis_tab.FileAnalysisTab._progress_changed(value)` | 只接受 `FileAnalysisProgress`；扫描显示真实文件/目录/字节/错误，分析显示阶段和已知单位。未知总量用忙碌条而非虚构百分比。 |
| `pc_manager_agent.ui.analysis_tab.FileAnalysisTab._analysis_completed(value)` | 收窄报告、清理工作状态，显示 COMPLETED/CANCELLED，渲染确定性摘要、加载第一页并启用导出/可选说明。 |
| `pc_manager_agent.ui.analysis_tab.FileAnalysisTab._analysis_failed(message)` | 恢复按钮/进度并显示明确失败；不保留伪造完成状态。 |
| `pc_manager_agent.ui.analysis_tab.FileAnalysisTab.cancel()` | 对活动工作器设置协作取消令牌并提示正在安全结束；不强杀线程。 |
| `pc_manager_agent.ui.analysis_tab.FileAnalysisTab._load_page()` | 从结果仓库按当前类型/搜索/大小/排序/方向加载最多 200 行，填充状态/置信度/重复组，更新分页和定位按钮；查询失败友好提示。 |
| `pc_manager_agent.ui.analysis_tab.FileAnalysisTab._reset_and_load_page()` | 筛选改变后把 offset 归零并重载，避免停在超出结果集的页。 |
| `pc_manager_agent.ui.analysis_tab.FileAnalysisTab._previous_page()` | offset 安全减去页大小且不低于零，然后查询。 |
| `pc_manager_agent.ui.analysis_tab.FileAnalysisTab._next_page()` | offset 增加固定页大小后查询；按钮只有满页时启用。 |
| `pc_manager_agent.ui.analysis_tab.FileAnalysisTab._open_selected_folder()` | 读取选中行保存的路径并委托授权限定 Explorer 服务；无行无操作，越界/不存在/启动错误提示。 |
| `pc_manager_agent.ui.analysis_tab.FileAnalysisTab._export_report()` | 要求当前计划/报告，选择 CSV/JSON 新路径、补正确后缀，委托 `ReportExporter` 并显示真实行数；取消无副作用。 |
| `pc_manager_agent.ui.analysis_tab.FileAnalysisTab._explain_report()` | 要求说明器/计划/报告，显示聚合外发确认；批准后在线程池调用，拒绝时不发送，结果只更新摘要视图。 |
| `pc_manager_agent.ui.analysis_tab.FileAnalysisTab._selected_root_ids()` | 从当前多选项的 `UserRole` 返回 UUID 元组，不从显示路径反解析权限。 |
| `pc_manager_agent.ui.analysis_tab.FileAnalysisTab._invalidate_plan()` | 控件/选择变化且已有计划时清除计划/确认、禁用执行并显示旧确认失效；构建/刷新期间短路。 |
| `pc_manager_agent.ui.analysis_tab.FileAnalysisTab.shutdown()` | 应用退出前调用 `cancel()`；线程池的有界等待由主窗口负责。 |
| `pc_manager_agent.ui.analysis_tab.FileAnalysisTab._show_error(message)` | 统一显示“不执行”警告；仅呈现错误，不吞掉业务层安全判断。 |
| `pc_manager_agent.ui.main_window.MainWindow._build_analysis_tab()` | 创建正式 `FileAnalysisTab`，把状态信号接到主状态栏，并加入“文件分析”标签。 |

### 阶段 1 Qt 工作器与类型收窄

#### `pc_manager_agent.ui.workers.FileAnalysisWorker.__init__`

```python
FileAnalysisWorker(runtime: ApplicationRuntime, plan: FileAnalysisPlan) -> None
```

- **作用：** 创建信号、取消令牌，并以信号发射器为进度回调构建本次分析服务。构造不会
  执行计划，但会复验当前授权以组合服务。

| 函数 | 跨线程行为与异常边界 |
|---|---|
| `pc_manager_agent.ui.workers.FileAnalysisWorker.run()` | 在线程池调用已确认计划的编排器；成功发射 `completed(report)`，任何线程边界异常发射类型+消息的 `failed`。底层已负责失败审计。 |
| `pc_manager_agent.ui.workers.FileAnalysisWorker.cancel()` | 线程安全、幂等设置令牌；扫描/哈希/分析循环协作检查。 |

#### `pc_manager_agent.ui.workers.PlannerWorker.__init__`

```python
PlannerWorker(planner, user_goal, confirmation_id, root_ids) -> None
```

- **作用：** 保存已经显示并确认的规划输入，不解析或扩大根 ID。

| 函数 | 跨线程行为 |
|---|---|
| `pc_manager_agent.ui.workers.PlannerWorker.run()` | 在线程中用 `asyncio.run` 调用 `planner.plan`；成功发射结构化结果，异常转换为 failed 文本。确认复验发生在 planner 内。 |

#### `pc_manager_agent.ui.workers.ExplanationWorker.__init__`

```python
ExplanationWorker(explainer, plan, report, confirmation_id) -> None
```

- **作用：** 保存聚合说明所需不可变对象和确认 ID；不复制候选行。

| 函数 | 跨线程行为 |
|---|---|
| `pc_manager_agent.ui.workers.ExplanationWorker.run()` | 异步调用说明器并发射最终本地数字文本；确认/网络/Schema 错误发射 failed，不把失败当说明。 |
| `pc_manager_agent.ui.workers.require_analysis_report(value)` | `Signal(object)` 运行时收窄；只有 `FileAnalysisReport` 原样返回，否则 `TypeError`。 |
| `pc_manager_agent.ui.workers.require_planning_result(value)` | 只有 `FileAnalysisPlanningResult` 原样返回，否则 `TypeError`，防止错误载荷进入确认 UI。 |

## 阶段 1 典型调用顺序

```text
AuthorizedPathService.add_authorized
  -> FileAnalysisPlanner.request_external_consent (可选)
  -> FileAnalysisPlanner.plan / FileAnalysisPlanCompiler.compile
  -> FileAnalysisSafetyValidator.review
  -> FileAnalysisOrchestrator.request_plan_confirmation
  -> FileAnalysisOrchestrator.resolve_plan_confirmation
  -> FileAnalysisOrchestrator.execute
  -> ToolRegistry.execute
  -> DirectoryScannerTool / analyzers
  -> AnalysisResultRepository
  -> FileAnalysisTab paged results / ReportExporter
  -> FileAnalysisExplainer.request_external_consent (可选)
  -> AuditRepository.record
```

任何调用方都不得以直接调用私有扫描/哈希函数来绕过授权、计划、安全审查或确认。API
文档中的私有函数仅用于维护和安全评审，不是稳定扩展点。

## Stage 2A：安全文件操作 API

以下 API 首次修改用户文件。除纯模型/Preview 外，调用顺序必须是“本地编译 → 独立审查
→ 真实 Preview → 精确确认 → 持久化事务能力 → 注册工具 → 验证 → Undo/Audit”。私有方法
在此列出是为了安全评审，不代表允许外部调用绕过编排器。

### 操作、事务和回滚数据模型

| 函数 | 详细作用、输入/返回和失败语义 |
|---|---|
| `FileState.identity_matches(other)` | 比较对象类型、Volume Serial Number 和 File ID；路径可以因移动改变。返回布尔值，不读取文件；缺少稳定身份的对象不能构造有效 `FileState`。 |
| `FileState.unchanged_since(earlier)` | 在身份相同基础上比较大小、创建/修改时间和 Windows 属性，供 Preview/执行/回滚 TOCTOU 复验；路径不参与。 |
| `RenameRule.validate_arguments()` | Pydantic 后置校验；prefix/suffix/replace 必须有字面值，replace 还需 replacement。失败抛 `ValidationError`，绝不接受代码/正则。 |
| `FileSelectionRule.normalize_extensions(values)` | 去空白、小写化、补点、去重；拒绝通配符、分隔符、空值和超长扩展名，返回不可变元组。 |
| `FileSelectionRule.require_scope()` | 要求至少一个授权根 ID，且有扩展名或本地结果 ID；空选择拒绝。根 ID 尚不等于授权，稍后仍由服务解析。 |
| `FileOperationIntentDraft.validate_intent_shape()` | 限制有限意图组合：move/organize 需目标根，rename 需结构化规则；只验证意图形状，不授予路径权限。 |
| `PlannedFileOperation.validate_operation_shape()` | mkdir 必须无 source/identity 且固定 `file.mkdir`；move/rename 必须有 source/identity 并使用对应注册工具。矛盾计划拒绝。 |
| `FileOperationPlan.validate_plan()` | 强制 R1、确认、FULL 目标、非空授权/操作、连续顺序、唯一操作 ID 和唯一目标；阻止两个步骤争用同一路径。 |
| `FileOperationPlan.canonical_digest()` | 对完整 JSON（含身份、路径、顺序、规则结果）稳定排序并计算 SHA-256；返回 64 字符十六进制，供 Preview/确认绑定。 |
| `FileOperationPreview.validate_totals()` | 复算 READY/CONFLICT/BLOCKED 和 FULL 数量，任何展示汇总与项目不一致都拒绝。 |
| `FileOperationPreview.canonical_digest()` | 哈希实时 Preview 的路径、身份、状态、问题和数量；文件或规则变化会得到不同摘要。 |
| `UndoRecord.canonical_digest()` | 对 Undo 内容计算完整性 SHA-256，排除会在回滚后写入的结果文本；数据库读取时必须复验。 |
| `RollbackPlan.canonical_digest()` | 哈希逆序项目、当前/恢复路径、当前身份、冲突和时间，用于独立回滚确认。 |

`OperationType` 只包含 CREATE_DIRECTORY、MOVE_FILE、MOVE_DIRECTORY、RENAME_FILE、
RENAME_DIRECTORY。`TransactionState` 和 `OperationItemState` 是显式状态机；不存在可绕过的
`is_done` 布尔组合。

### Windows 平台接口和实现

| 函数 | 详细作用、输入/返回和安全约束 |
|---|---|
| `FileOperationPlatform.inspect(path)` | 平台协议：返回稳定 `FileState`；实现必须拒绝重解析、系统、离线和不支持对象。 |
| `FileOperationPlatform.move_same_volume(source, destination)` | 平台协议：同卷、失败即停、不得覆盖的单次移动；无返回，失败抛 `OSError`。 |
| `FileOperationPlatform.create_directory(destination)` | 平台协议：只创建一个叶目录，不隐式创建父级或覆盖。 |
| `FileOperationPlatform.remove_empty_directory(path)` | 平台协议：仅供经验证的事务创建空目录回滚；不是通用删除接口。 |
| `_extended_path(path)` | 把已验证本地绝对路径转为 `\\?\` Unicode 长路径；拒绝相对、UNC 和设备路径。 |
| `_raise_windows_error(action, path, error_code=None)` | 把 Win32 last-error 映射为 FileNotFound/FileExists/Permission 或 `WindowsFileOperationError`，消息只含当前操作路径。 |
| `WindowsFileOperationPlatform.__init__()` | 仅在 `os.name == "nt"` 时装载 `kernel32`；非 Windows 立即失败，不提供模拟生产写。 |
| `WindowsFileOperationPlatform.inspect(path)` | 用 `GetFileAttributesW` 和带 `OPEN_REPARSE_POINT` 的句柄读取 `FileIdInfo`，结合 `os.stat` 返回身份/元数据；拒绝 reparse/system/offline/非普通对象/零 ID。 |
| `WindowsFileOperationPlatform.move_same_volume(source, destination)` | 调用 `MoveFileExW`，只设置 WRITE_THROUGH；不设置 REPLACE_EXISTING、COPY_ALLOWED 或延迟重启。调用者必须先复验同卷和目标空闲。 |
| `WindowsFileOperationPlatform.create_directory(destination)` | 调用 `CreateDirectoryW` 创建一个叶目录；目标存在、父级缺失、权限等按 Win32 错误抛出。 |
| `WindowsFileOperationPlatform.remove_empty_directory(path)` | 调用 `RemoveDirectoryW`；只由 rollback 注册工具在身份/类型/空目录复验后使用。 |

### 工具能力和执行授权

| 函数 | 详细作用、输入/返回和安全约束 |
|---|---|
| `arguments_digest(arguments)` | 把 Pydantic JSON 参数稳定序列化并计算 SHA-256；事务预留和执行能力必须完全相同。 |
| `WriteExecutionGuard.require(authorization, tool_name, arguments)` | 抽象防线；无具体持久化证明时不得授权写，默认接口抛 `NotImplementedError`。 |
| `CreateDirectoryTool.__init__(path_policy, platform)` | 注入路径策略/平台并建立 `file.mkdir` R1、确认、Preview、FULL manifest；每次只允许一个叶目录。 |
| `CreateDirectoryTool.manifest` | 返回不可变清单，无副作用。 |
| `CreateDirectoryTool.execute(request, cancellation)` | 要求 `CreateDirectoryRequest`，写前检查取消、授权、目标不存在和父目录；创建后读取身份验证。类型/冲突/权限/验证失败不返回成功。 |
| `MoveTool.__init__(path_policy, platform)` | 建立 `file.move` 单对象同卷、无覆盖 R1 工具。 |
| `MoveTool.manifest` | 返回包含 source/destination 审计字段、前后条件和 FULL rollback 的清单。 |
| `MoveTool.execute(request, cancellation)` | 复验 source 身份/元数据、destination 授权且不存在、目标父卷；调用同卷 Win32 移动，再验证源消失、目标身份保持。取消仅在开始前生效。 |
| `RenameTool.__init__(path_policy, platform)` | 建立 `file.rename` 同父目录 R1 工具；不允许把 rename 当 move。 |
| `RenameTool.manifest` | 返回有限重命名的确认/Preview/FULL 清单。 |
| `RenameTool.execute(request, cancellation)` | 复验身份、同父和目标冲突；普通名称一步移动，大小写专用 rename 走 Preview 绑定的唯一临时名两步并在第二步失败时尽力恢复原名；最终验证身份。 |
| `RemoveCreatedDirectoryTool.__init__(path_policy, platform)` | 建立内部 `file.rollback.rmdir-empty`；能力仍需关联原事务 Undo，不能供 UI 直接删目录。 |
| `RemoveCreatedDirectoryTool.manifest` | 返回回滚专用 R1/FULL、单对象清单。 |
| `RemoveCreatedDirectoryTool.execute(request, cancellation)` | 立即复验授权、目录类型、身份/元数据和空状态，调用 checked remove 后确认路径消失；任何新内容或变化拒绝。 |
| `ToolManifest.__post_init__()` | 除原名称/超时/R0检查外，强制所有写工具 `requires_confirmation`、`supports_preview` 且声明非 NONE rollback。 |
| `ToolRegistry.__init__(write_guard=None)` | 建立空白名单和可选写能力验证器；未配置 guard 的注册写工具仍不能执行。 |
| `ToolRegistry.execute(name, arguments, cancellation=None, authorization=None)` | 先校验输入；写工具再要求 authorization + guard 和规范化参数摘要；执行注册实现并验证输出类型。未知/参数/授权/输出错误分别抛类型化异常。 |

### Preview、独立安全审查与确认

| 函数 | 详细作用、输入/返回和失败语义 |
|---|---|
| `FileOperationSafetyValidator.__init__(registry, path_policy, max_operations=500)` | 保存独立注册表/路径边界和正批量上限；无文件写入。 |
| `FileOperationSafetyValidator.review(plan)` | 检查批量、R1/确认/FULL、工具存在及 manifest、来源/目标授权、rename 同父、mkdir 父依赖、no-op、自移、重复/重叠 source；返回全部 `ReviewIssue`，不自动修复。 |
| `OperationPreviewEngine.__init__(path_policy, platform, max_objects=500, max_total_bytes=50GiB)` | 注入只读身份观察器和正数量/容量限制；无写副作用。 |
| `OperationPreviewEngine.generate(plan, transaction_id=None)` | 对每项实时复验并生成不可变 Preview；报告目标冲突、源变化、跨卷、不可访问、批量和回滚数量。即使冲突也保留项目，不执行写。 |
| `OperationPreviewEngine._impact(source, kind)` | 文件返回 1/大小；目录用 `scandir` 有界遍历，不跟随重解析，累计对象/字节，遇拒绝项整项 BLOCKED。 |
| `OperationPreviewEngine._inspect_nearest_parent(destination)` | 对计划中尚未创建的多级目录向上找最近现存父级并读取卷身份；无现存父级抛 `FileNotFoundError`。 |
| `PathPolicy.validate_operation_source(path)` | 写前要求绝对无穿越、已存在、授权、非保护/网络/重解析的普通文件或目录；严格 resolve 后返回 canonical Path，任何变化/缺失抛 `PathSecurityError`。 |
| `PathPolicy.validate_operation_destination(path)` | 对可缺失目标做词法/名称/授权检查，从父级向上寻找现存目录并拒绝重解析/网络/越界；不会解析缺失叶子，也不会把“目标已存在”当授权。 |
| `PathPolicy.validate_rename_destination(source, destination)` | 复用目标检查并要求两者父目录规范相等、名称确实变化；阻止 `..` 或 rename 跨目录移动。 |
| `PathPolicy.validate_windows_name(name)` | 拒绝空/点段、超过 255 字符、尾随空格/点、控制符、Windows 非法字符和 CON/PRN/AUX/NUL/COM1..9/LPT1..9（含扩展名）。 |
| `PathPolicy._validate_operation_syntax(path)` | 私有公共前置：要求绝对无 `..`、非 UNC/device、无歧义段且词法路径位于授权非保护范围；不跟随缺失目标。 |
| `OperationConfirmationService.__init__(ttl_seconds=300, now=None)` | 建立内存一次性 R1 确认库和可测试 UTC 时钟；非正 TTL 拒绝。重启不会恢复令牌。 |
| `OperationConfirmationService.request(plan, preview)` | 要求 plan/摘要/项目数一致且至少一个 READY，生成绑定 transaction/plan/preview/摘要/数量/时限的 PENDING 确认。 |
| `OperationConfirmationService.resolve(id, approved, plan, preview)` | 仅处理未过期 PENDING 且全部绑定仍一致的确认，变为 APPROVED/REJECTED；批准记录时间。未知、重复、过期、变化均抛 `OperationConfirmationError`。 |
| `OperationConfirmationService.consume(id, plan, preview)` | 执行开始时一次性把 APPROVED 变为 CONSUMED；拒绝重放、过期和任何摘要变化。 |
| `OperationConfirmationService._get_pending(id)` | 私有精确查找；未知或非 PENDING 失败。 |
| `OperationConfirmationService._require_matching(plan, preview)` | 校验 Preview plan ID/digest 和项目数，防止“确认 A 执行 B”。 |
| `OperationConfirmationService._require_current(request, plan, preview)` | 比较确认保存的七项绑定与当前对象；任一差异视为 stale。 |
| `RollbackConfirmationService.__init__(ttl_seconds=300, now=None)` | 建立与正向确认完全独立的回滚一次性确认库。 |
| `RollbackConfirmationService.request(plan)` | 只有回滚 Preview 至少一个 READY 才生成绑定事务、回滚计划/digest、数量和到期时间的确认；纯冲突 Preview 仍可展示但不可确认。 |
| `RollbackConfirmationService.resolve(id, approved, plan)` | 对当前精确回滚 Preview 批准/拒绝；重复、未知、过期或变化失败。 |
| `RollbackConfirmationService.consume(id, plan)` | 回滚开始时一次性消费批准；不允许复用正向确认或重放。 |
| `RollbackConfirmationService._get(id)` | 私有字典查找，未知抛类型化错误。 |
| `RollbackConfirmationService._require_current(request, plan)` | 比较事务、计划 ID、完整 digest 和 READY 数，任一差异拒绝。 |

### 意图解析、确定性编译和应用服务

| 函数 | 详细作用、输入/返回和失败语义 |
|---|---|
| `FileOperationSourceResolver.__init__(authorization, max_sources=500)` | 注入授权服务和正来源上限；不保存路径或扫描。 |
| `FileOperationSourceResolver.resolve(selection)` | 由 opaque root IDs 本地构造策略，使用 `scandir` 查找字面扩展名，不跟链接；去重排序并逐项路径复验。超限/record-only/IO/授权错误失败。 |
| `FileOperationPlanCompiler.__init__(authorization, platform, max_operations=500)` | 注入权限解析和真实身份观察器；限制最终操作总数。 |
| `FileOperationPlanCompiler.compile(user_goal, intent, sources)` | 把未可信 provider intent + 本地发现路径编译成具体 R1 plan；本地计算 modified year、目录和名称，模型不能指定工具外命令。无 source、缺目标或超限失败。 |
| `FileOperationPlanCompiler.compile_selected_move(user_goal, source_paths, destination_directory, root_ids)` | GUI 确定性入口；不用模型，复验所有选择/目标，补必要 mkdir，再生成最终 move。空选/超限/越界失败。 |
| `FileOperationPlanCompiler.compile_selected_rename(user_goal, source_paths, rule, root_ids)` | GUI 确定性入口；应用一个有限规则，保留文件扩展名，生成同父最终名称和 case-only 临时名。 |
| `FileOperationPlanCompiler._compile_moves(policy, sources, destination_base, group_by=None)` | 读取每个 source 身份，按规范路径排序；可用修改时间 UTC 年分组，先放 mkdir 再放 file/directory move。 |
| `FileOperationPlanCompiler._compile_renames(policy, sources, rule)` | 按规范路径稳定排序并逐项生成名称；根据真实对象类型选择 RENAME_FILE/DIRECTORY，大小写专用操作预留 UUID 临时路径。 |
| `FileOperationPlanCompiler._append_missing_directories(policy, destination, planned, operations)` | 从最近现存父级向下依序加入单层 mkdir；防止隐式 `parents=True`，并避免重复计划目录。 |
| `FileOperationPlanCompiler._apply_rename_rule(state, rule, index)` | 实现 prefix/suffix/sequence/lower/upper/literal replace/date prefix；保留文件扩展，日期来自 mtime；no-op 拒绝。 |
| `FileOperationPlanner.__init__(provider, authorization, registry, external_consent)` | 组合可替换 provider 与本地边界；不执行文件发现或写操作。 |
| `FileOperationPlanner.build_provider_request(user_goal)` | 构造无真实路径/文件名的请求：goal、label、UUID、有限 enum、注册工具；缺授权或必需工具失败。 |
| `FileOperationPlanner.request_external_consent(user_goal)` | 对上述精确 JSON 请求外发确认，摘要明确不会发送路径、名称、内容、结果或 Undo。 |
| `FileOperationPlanner.plan_intent(user_goal, confirmation_id)` | 异步复验外发确认，调用 provider，返回类型化 intent + trace；不解析具体路径，也不扩大授权。 |
| `LLMProvider.create_file_operation_intent(request)` | provider-neutral 异步扩展点；返回 `ProviderFileOperationIntentResult`。基类无实现，调用者仍须本地编译/审查。 |
| `OpenAILLMProvider.create_file_operation_intent(request)` | 用 Responses strict parse 请求 `FileOperationIntentDraft`；固定系统说明禁止路径/命令/删除/覆盖。API/Schema 错误包装为 `OpenAIProviderError`。 |
| `ApplicationRuntime.create_file_operation_services(progress_callback=None)` | 为当前所有授权根构造一次 Stage 2A 策略、带持久化 guard 的注册表、四工具、audit、validator、Preview、executor、compiler、resolver、可选 planner 和 rollback manager；无授权失败。 |
| `FileOperationService.__init__(validator, preview_engine, confirmations, repository, executor, audit)` | 注入完整安全链，不自行创建全局状态。 |
| `FileOperationService.prepare(plan)` | 独立审查→真实 Preview→构造全部精确参数→原子持久化→AWAITING→请求确认→审计。审查拒绝或无 READY 不创建可执行确认；不写文件。 |
| `FileOperationService.resolve_confirmation(prepared, approved)` | 对同一个 `PreparedFileOperation` 解析确认，持久化 CONFIRMED/CANCELLED 并审计；不能换计划/Preview。 |
| `FileOperationService.execute(prepared, cancellation=None)` | 委托事务执行器；只接受已经批准的同一个 prepared 对象，返回验证后的 terminal report。 |
| `FileOperationService.get_transaction(transaction_id)` | 只读返回一个持久化事务；未知/数据库错误失败。 |

### 事务执行、SQLite 和审计

| 函数 | 详细作用、输入/返回和一致性语义 |
|---|---|
| `build_operation_arguments(operation, preview_item)` | 用具体 operation + 实时 Preview source state 构造严格 mkdir/move/rename 请求 JSON；缺 move/rename 身份拒绝。 |
| `build_all_operation_arguments(plan, preview)` | 按 operation ID 为计划每项生成不可变参数预留映射；缺 Preview 项失败。 |
| `TransactionExecutor.__init__(repository, registry, confirmations, audit, progress_callback=None)` | 注入持久化、唯一执行入口、一次性确认、强制审计和可选线程安全进度。 |
| `TransactionExecutor.execute(plan, preview, confirmation_id, cancellation=None)` | 消费确认、复验持久 digest、转 RUNNING；对 READY 项逐一先写 PREPARED Undo/审计，再用持久 capability 调注册工具，验证后完成。取消停止未来项，异常 fail-safe 停止并返回准确终态。 |
| `TransactionExecutor._emit_progress(transaction, current_path)` | 有回调时发不可变进度；无回调短路，不虚构总量。 |
| `TransactionExecutor._report(transaction, preview)` | 从数据库重读所有 item，计算可回滚 COMPLETED 数和 Preview 字节，构造 terminal `OperationExecutionReport`。 |
| `OperationRepository.__init__(database_path)` | 创建独立 SQLAlchemy engine/session factory；尚未建表，任何业务调用需 initialize。 |
| `OperationRepository.initialize()` | 加法建表；开启恢复审计：RUNNING/ROLLING_BACK→INTERRUPTED 并返回 ID，失去内存确认的 PREVIEWED/AWAITING/CONFIRMED→CANCELLED。绝不自动续跑。 |
| `OperationRepository.create_from_preview(plan, preview, argument_payloads)` | 在一个 SQLite 事务中写父事务和全部 item/操作/Preview/参数摘要；先 flush 父以满足 FK。冲突/阻止项为 SKIPPED，重复目标/ID 原子失败。 |
| `OperationRepository.transition(transaction_id, new_state, confirmation_id=None, confirmed_at=None, error_message=None)` | 只允许 `_ALLOWED_TRANSITIONS`；比较当前状态后更新 UTC/确认/错误，再重读返回。非法/未知/DB错误失败。 |
| `OperationRepository.begin_operation(transaction_id, operation_id, undo_record)` | 要求事务 RUNNING、item PENDING 且 Undo ID/事务匹配；在同一数据库事务把 item 置 RUNNING 并插 PREPARED checksum Undo，之后才可写文件。 |
| `OperationRepository.complete_operation(operation_id, after_state)` | 要求 item RUNNING 和 PREPARED Undo；原子写 after state、AVAILABLE/checksum、item COMPLETED、事务成功计数，返回 item/Undo。 |
| `OperationRepository.fail_operation(operation_id, error_code, error_message)` | 仅 RUNNING item 可失败；原子写类型化错误、完成时间和事务失败计数，不声称 Undo AVAILABLE。 |
| `OperationRepository.mark_item_rolled_back(operation_id, success, result)` | 仅 ROLLING_BACK item；原子更新 item 和 Undo 为 ROLLED_BACK/ROLLBACK_FAILED、结果/checksum。 |
| `OperationRepository.begin_rollback_operation(operation_id, tool_name, argument_payload)` | 要求事务 ROLLING_BACK 且 item 可回滚；保存逆向注册工具和精确参数摘要，再置 ROLLING_BACK。 |
| `OperationRepository.get_transaction(transaction_id)` | 只读返回强类型事务；未知/DB错误抛 `OperationStoreError`。 |
| `OperationRepository.get_item(operation_id)` | 返回一个强类型 item；未知失败。 |
| `OperationRepository.get_operation(operation_id)` | 从不可变 `operation_data` 恢复原计划项；模型校验失败向上传播。 |
| `OperationRepository.list_items(transaction_id)` | 按正向 sequence 升序返回 item 元组，用于报告。 |
| `OperationRepository.list_recent(limit=100)` | 按创建时间倒序，limit 钳制 1..500；只读历史。 |
| `OperationRepository.list_undo(transaction_id)` | 按 sequence 降序返回并逐条 checksum 复验的 Undo，正好是回滚顺序。 |
| `OperationRepository.get_undo(operation_id)` | 返回单条 checksum 通过的 Undo；缺失/篡改/DB错误失败关闭。 |
| `OperationRepository.require_execution_authorization(authorization, tool_name, argument_payload)` | 同时要求事务/item 正处于 forward 或 rollback RUNNING，IDs/plan/preview/tool/digest 与行内预留完全匹配；任何 stale/cross-item/replay 拒绝。 |
| `OperationRepository.close()` | dispose engine 并把 repository 标为未初始化；后续调用失败。 |
| `OperationRepository._require_initialized()` | 私有 fail-closed guard，未初始化抛 `OperationStoreError`。 |
| `OperationRepository._transaction_to_row(value)` | 把不可变领域事务转换为 ORM row，包括 UTC、计数和确认字段。 |
| `OperationRepository._row_to_transaction(row)` | 把 SQLite row 恢复为强类型事务，并恢复 SQLite 丢失的 UTC tzinfo。 |
| `OperationRepository._row_to_item(row)` | 恢复 UUID、Path、enum、错误和时间的 `TransactionItem`。 |
| `OperationRepository._validate_undo_row(row)` | Pydantic 恢复 Undo 并比较 checksum；不一致抛完整性错误。 |
| `TransactionExecutionGuard.__init__(repository)` | 保存可信操作 journal；不缓存授权结果。 |
| `TransactionExecutionGuard.require(authorization, tool_name, arguments)` | 每次注册写调用实时委托 repository 精确验证，不允许 UI/模型 token 代替。 |
| `_as_utc(value)` | SQLite naive datetime 按 UTC 恢复；已有时区转换 UTC。 |
| `OperationAuditLogger.__init__(repository, app_version, git_commit)` | 注入独立审计库和版本追踪；不持有 Undo。 |
| `OperationAuditLogger.previewed(plan, preview)` | 记录原请求、完整计划、IDs/digests、各状态/字节和“未执行写”；审计不可用使 prepare 失败。 |
| `OperationAuditLogger.confirmation_resolved(plan, confirmation)` | 记录精确正向确认绑定、决定和时间。 |
| `OperationAuditLogger.operation_started(transaction, operation, confirmation_id, before_state)` | 在文件写前记录 transaction/operation/tool/source/target/R1/已消费确认和 before state；失败时执行器不继续写。 |
| `OperationAuditLogger.operation_completed(transaction, operation, after_state, undo)` | 只在工具验证和 Undo AVAILABLE 后记录成功、after state、Undo ID/等级和 verification。 |
| `OperationAuditLogger.operation_failed(transaction, operation, error)` | 记录脱敏类型/消息和 verified=false；不伪造 after/rollback。 |
| `OperationAuditLogger.rollback_result(transaction, undo, success, message)` | 对每个逆向项记录验证成功或明确失败，引用 Undo 而不复制文件内容。 |
| `OperationAuditLogger.rollback_previewed(transaction, plan)` | 记录逆向 digest 和 READY/CONFLICT/BLOCKED，明确 mutation=false。 |
| `OperationAuditLogger.rollback_confirmation_resolved(transaction, confirmation)` | 记录独立回滚批准/拒绝、ID/digest/时间。 |

### Rollback Manager

| 函数 | 详细作用、输入/返回和冲突语义 |
|---|---|
| `RollbackManager.__init__(repository, path_policy, platform, registry, confirmations, audit)` | 注入 Undo 来源、实时路径/身份、同一注册执行边界、独立确认和审计；不接收模型。 |
| `RollbackManager.prepare(transaction_id)` | 仅允许已停止/完成/中断/部分回滚事务；读取 Undo 降序，实时评估每项，并考虑更早逆向步骤会腾空的受管路径。返回 Preview；无 READY 时 confirmation 为 None。 |
| `RollbackManager.resolve_confirmation(prepared, approved)` | 要求 prepared 有可确认项，解析独立回滚确认并审计；不改变事务到 ROLLING_BACK。 |
| `RollbackManager.execute(prepared, cancellation=None)` | 消费一次性回滚确认，转 ROLLING_BACK，按降序对 READY 项重新验证/预留/注册执行/验证/审计；取消停止未来项，异常停止并产生 ROLLED_BACK/PARTIAL/FAILED。 |
| `RollbackManager._preview_undo(undo, paths_vacated_by_earlier_reverse_steps)` | 检查 Undo 状态、结果存在/身份/元数据、original 冲突和创建目录内容；仅当内容都将由更早 reverse 移走时允许目录 READY。 |
| `RollbackManager._build_reverse_arguments(undo, temporary_path)` | 执行前再次检查结果；mkdir 构造 rollback-rmdir，rename 构造反向同父请求，move 构造反向 move。原路径缺失或结果变化拒绝。 |
| `RollbackManager._verify_reverse_result(undo, result)` | mkdir 要求类型化 verified 且目录消失；move/rename 要求类型化 verified 且恢复身份匹配。失败不能标记 ROLLED_BACK。 |

### Stage 2A GUI

`FileOperationTab` 只呈现/收集状态并启动 worker。它不会直接调用 Win32、`Path.rename`、
工具 `execute` 或数据库状态迁移。

| 函数 | GUI 行为和委托边界 |
|---|---|
| `FileAnalysisTab._start_planning_from_button(_checked)` | 忽略 Qt checked 布尔参数，调用只读规划入口，防止布尔值被误作目标。 |
| `FileAnalysisTab._request_move_selected()` | 收集当前页显式勾选路径；空选提示，否则发 `move_selected_requested`，不移动。 |
| `FileAnalysisTab._request_rename_selected()` | 同上，发 rename 请求，不重命名。 |
| `FileAnalysisTab._checked_result_paths()` | 仅返回 checkState=Checked 行保存在 UserRole 的 Path 元组；不把选择行或模型建议视为勾选。 |
| `MainWindow._build_operation_tab()` | 创建 Stage 2A 页面、连接状态和 Stage 1 勾选信号并加标签。 |
| `MainWindow._open_move_for_paths(value)` | 收窄 tuple 中 Path，传给操作页并切换标签；只预填，不执行。 |
| `MainWindow._open_rename_for_paths(value)` | 同上，提示选择有限规则并生成 Preview。 |
| `FileOperationTab.__init__(runtime)` | 初始化所有不可变业务引用/worker 状态，构建 UI、连接跨线程进度并加载历史；显示启动发现的 INTERRUPTED。 |
| `FileOperationTab._build_ui()` | 构造选择、目标、有限 rename、Preview 明细、确认/执行/停止、事务历史和回滚控件；所有执行按钮初始禁用。 |
| `FileOperationTab.set_sources(paths)` | 替换待处理列表并保存 Path 到 UserRole；只改变 UI，明确“尚未执行”。 |
| `FileOperationTab.start_planning(goal)` | 创建 Stage 2A 服务；配置 provider 时请求精确外发确认并启动 intent worker。provider disabled 时提示使用本地控件，不发送。 |
| `FileOperationTab._add_files()` | 打开多文件选择器后调用 `set_sources`；取消无副作用。授权稍后由 compiler 复验。 |
| `FileOperationTab._add_directory()` | 选择一个目录并加入来源；不递归读取，Preview worker 后台计算影响。 |
| `FileOperationTab._choose_destination()` | 选择已有目标目录并填文本；不因此授权或创建。 |
| `FileOperationTab._prepare_move()` | 校验选择/目标，创建当前授权范围服务并确定性编译，随后启动只读 Preview worker。 |
| `FileOperationTab._prepare_rename()` | 从控件构造严格 `RenameRule`，确定性编译并启动 Preview；空/非法/no-op 只提示。 |
| `FileOperationTab._start_preview(services, plan)` | 清除旧确认，保存本次服务/计划，禁用规划按钮并在线程池启动安全审查+Preview。 |
| `FileOperationTab._natural_plan_completed(value)` | 收窄 worker 输出为 `FileOperationPlan`，再走同一 Preview；类型/服务错误安全失败。 |
| `FileOperationTab._preview_completed(value)` | 收窄 prepared，显示精确数量/字节/冲突/FULL/无覆盖声明，填表并只启用确认/拒绝。 |
| `FileOperationTab._populate_forward_preview(prepared)` | 每项显示状态、操作、源、最终目标和问题；无写副作用。 |
| `FileOperationTab._confirm_forward()` | 显示对象数/字节/冲突/回滚的具体对话框；Yes 后调用 service resolve，只启用单独“执行”。 |
| `FileOperationTab._reject_forward()` | 持久化 CANCELLED/拒绝审计并禁用执行；不写文件。 |
| `FileOperationTab._execute_forward()` | 为当前已确认 prepared 创建 worker，显示“停止仅阻止未来项”并启动线程池。 |
| `FileOperationTab._progress_changed(value)` | 只接受 `OperationProgress`，显示成功/失败/跳过/当前路径；不据 UI 计数判断成功。 |
| `FileOperationTab._execution_completed(value)` | 收窄数据库报告，显示 terminal、成功/失败/跳过/PENDING/Undo 数并刷新历史。 |
| `FileOperationTab.refresh_history()` | 只读加载最近 100 事务，保存 transaction ID 到 UserRole；数据库错误提示。 |
| `FileOperationTab._prepare_rollback()` | 从选中历史 ID 创建当前授权服务并调用 RollbackManager.prepare；展示真实逆序 Preview，无写。 |
| `FileOperationTab._populate_rollback_preview(prepared)` | 显示当前/恢复路径、状态和冲突；mkdir 显示“仅事务创建空目录”。 |
| `FileOperationTab._confirm_and_execute_rollback()` | 显示具体 READY/冲突/复验说明；独立确认后创建 rollback worker，不复用正向授权。 |
| `FileOperationTab._rollback_completed(value)` | 收窄持久化事务并显示回滚终态/刷新历史；不把 PARTIAL/FAILED 写成完成。 |
| `FileOperationTab.cancel()` | 对正向/回滚 worker 设置 cooperative token；不终止正在执行的 Win32 调用。 |
| `FileOperationTab.shutdown()` | 退出前调用 cancel；全局线程池有界等待由主窗口负责。 |
| `FileOperationTab._source_paths()` | 从列表 UserRole 返回 Path 元组，不从显示文字推断。 |
| `FileOperationTab._all_root_ids()` | 返回当前授权记录 UUID；无授权抛友好错误。 |
| `FileOperationTab._invalidate_prepared()` | 清除 prepared 并禁用确认/拒绝/执行，使旧 Preview 不可用。 |
| `FileOperationTab._set_planning_busy(busy, message)` | 控制规划按钮、忙碌/完成进度和文本；只是展示状态。 |
| `FileOperationTab._worker_failed(message)` | 清理全部 worker 引用/取消按钮，显示“未执行或安全停止”并刷新数据库历史；不伪造报告。 |
| `FileOperationTab._show_error(message)` | 统一“不执行”警告和状态信号。 |

### Stage 2A Qt workers

| 函数 | 跨线程作用、返回和错误边界 |
|---|---|
| `FileOperationPlanningWorker.__init__(services, user_goal, confirmation_id)` | 要求已配置 planner，保存已确认外发信息；不调用 provider。 |
| `FileOperationPlanningWorker.run()` | 在线程中请求受限 intent，本地 resolve sources 并 compile；成功发 plan，任何异常发类型+消息。 |
| `OperationPreviewWorker.__init__(services, plan)` | 保存不可变计划和服务；不 Preview。 |
| `OperationPreviewWorker.run()` | 在线程调用 service.prepare；成功发 `PreparedFileOperation`，审查/IO/DB/audit 失败发 failed。 |
| `OperationExecutionWorker.__init__(services, prepared)` | 创建独立取消令牌并保存精确 prepared；不消费确认。 |
| `OperationExecutionWorker.run()` | 在线程执行事务并发验证报告；异常不当成功发射。 |
| `OperationExecutionWorker.cancel()` | 设置 stop-future token，幂等。 |
| `RollbackExecutionWorker.__init__(manager, prepared)` | 保存独立回滚 manager/Preview 和取消令牌。 |
| `RollbackExecutionWorker.run()` | 在线程执行逆序回滚，成功只发强类型 terminal transaction。 |
| `RollbackExecutionWorker.cancel()` | 设置回滚 stop-future token。 |
| `require_operation_plan(value)` | `Signal(object)` 收窄；非 `FileOperationPlan` 抛 `TypeError`。 |
| `require_prepared_operation(value)` | 只接受 `PreparedFileOperation`。 |
| `require_operation_report(value)` | 只接受 `OperationExecutionReport`。 |
| `require_operation_transaction(value)` | 只接受 `OperationTransaction`。 |

## Stage 2A 典型调用顺序

```text
FileOperationPlanner.plan_intent (可选)
  -> FileOperationSourceResolver.resolve
  -> FileOperationPlanCompiler.compile / compile_selected_*
  -> FileOperationService.prepare
       -> FileOperationSafetyValidator.review
       -> OperationPreviewEngine.generate
       -> OperationRepository.create_from_preview
       -> OperationConfirmationService.request
  -> FileOperationService.resolve_confirmation
  -> TransactionExecutor.execute
       -> OperationRepository.begin_operation (PREPARED Undo)
       -> ToolRegistry.execute + TransactionExecutionGuard
       -> Win32 adapter + tool verify
       -> OperationRepository.complete_operation (AVAILABLE Undo)
  -> RollbackManager.prepare
  -> RollbackManager.resolve_confirmation
  -> RollbackManager.execute (reverse sequence, same registered boundary)
```

## Stage 4D2C2 MSIX / Store App API

### Domain models and validation

| 函数 | 作用、输入/输出、异常与安全副作用 |
|---|---|
| `MsixFamilyIdentity.canonical_digest()` | 对稳定 Family Name、Package Name、Publisher ID 生成 SHA-256 授权摘要；无 I/O。 |
| `MsixInstanceIdentity.canonical_digest()` | 对 Full Name、版本、架构、Resource ID 生成版本敏感摘要；Store 更新会改变结果。 |
| `MsixPackageIdentity.enforce_current_user_claim()` | Pydantic 后置校验；拒绝“当前用户范围但未注册”及任何 Provisioned 查询声明，失败抛 `ValueError`。 |
| `MsixPackageIdentity.canonical_digest()` | 绑定 Family、Instance、范围、类型、健康/结构标志和签名类别。 |
| `MsixTargetQuery.require_selector()` | 要求精确摘要/Family/Full Name 或有限展示查询之一；空查询抛 `ValueError`。 |
| `MsixDependencySnapshot.canonical_digest()` | 绑定查询完整性、直接依赖、反向依赖、孤立依赖风险及警告。 |
| `MsixPreflight.canonical_digest()` | 绑定进程/服务探测完整性、关联数量、并发状态、阻止项和警告。 |
| `MsixRemovalAssessment.allow_only_ordinary_r2()` | 只允许 `USER_APPLICATION + R2` 的 ALLOW；其他可执行组合抛 `ValueError`。 |
| `MsixRemovalAssessment.canonical_digest()` | 对最终安全分类、风险、决定和理由生成摘要。 |
| `MsixUninstallPlan.validate_contract()` | 强制工具名 `software.uninstall.msix`、R2、两级确认和 rollback NONE。 |
| `MsixUninstallPlan.canonical_digest()` | 对整个不可变计划生成授权摘要。 |
| `MsixUninstallPreview.bind_evidence()` | 校验有效期、目标依赖摘要、类型/范围/政策/预检的一致性和真实回滚级别。 |
| `MsixUninstallPreview.invariant_digest()` | 生成两次确认间必须不变的 Package、依赖、政策、预检和数据影响摘要。 |
| `MsixUninstallPreview.canonical_digest()` | 对包含有效期的完整 Preview 生成摘要。 |
| `ValidatedMsixRemovalAction.enforce_narrow_action()` | 只接受当前用户普通 App，并强制保留 Roamable 选项；范围/类型/选项扩大抛 `ValueError`。 |
| `MsixUninstallRequest.bind_transaction()` | 要求内部 Action 和持久事务 UUID 完全一致。 |

### Platform and WinRT adapter

| 函数 | 作用、输入/输出、异常与安全副作用 |
|---|---|
| `MsixPackagePlatform.inventory_current_user(max_items, cancellation)` | 平台协议：返回有界当前用户清单；不得查询其他用户或 Provisioned。 |
| `MsixPackagePlatform.remove_current_user(action, cancellation)` | 平台协议：只移除一个已验证当前用户实例。 |
| `MsixPackagePlatform.dependency_snapshot(identity)` | 平台协议：返回直接/反向 Package 关系及完整性。 |
| `WindowsMsixPackagePlatform.inventory_current_user(...)` | 使用 WinRT PackageManager 空 SID 查询当前用户；逐项复制结构化属性，取消/上限/不可访问元数据产生不完整清单，不打印身份。 |
| `WindowsMsixPackagePlatform.remove_current_user(...)` | 执行前按 Full Name 重查并比较完整身份，仅调用 `remove_package_with_options_async` 和固定 `PRESERVE_ROAMABLE_APPLICATION_DATA`；无 PowerShell/提权/重试。 |
| `WindowsMsixPackagePlatform.dependency_snapshot(identity)` | 对精确实例调用 `FindRelatedPackages` 的 DEPENDENCIES/DEPENDENTS，包含 framework/optional/resource/host-runtime；失败返回 UNAVAILABLE。 |
| `_await_deployment(operation, timeout_seconds)` | 在固定上限内观察一个 WinRT 异步操作；超时不重试，也不强杀系统部署工作。 |
| `_read_package(package)` | 将 documented WinRT 属性复制为 `RawMsixPackageRecord`；不读取 manifest/文件内容/用户数据。 |
| `_normalize_package(raw)` | 分离 Raw 与 Normalized，构造 Family/Instance/current-user 身份。 |
| `_classify_raw(raw)` | 按强结构标志优先分类 Framework/Resource/Bundle/Optional；stub/不健康/无 App entry 为 Unknown。 |
| `_related(target, options)` | 返回排序后的身份级关系，绝不保存 manifest 或数据路径。 |
| `_unavailable_dependencies(identity, reason)` | 构造 `UNAVAILABLE + orphan risk` 的失败关闭快照。 |
| `_optional_text(value)` | 将空 WinRT 字符串归一为 `None`。 |
| `_map_hresult(exc)` | 将有限 HRESULT 映射为访问拒绝、占用、依赖或部署错误；未知保持部署错误。 |
| `_map_hresult_code(code)` | 对 WinRT `extended_error_code` 使用同一有限映射；非零未知值保持部署失败。 |
| `WindowsMsixSoftwarePackageProvider.__init__(platform)` | 注入或创建只读 WinRT 平台；不执行 Package 写操作。 |
| `WindowsMsixSoftwarePackageProvider.collect(max_items, cancellation)` | 将当前用户 MSIX Family/Instance 结构化投影到 Stage 4D1 `RawInstalledSoftwareEntry`，保留精确 Package anchors 并传播不完整状态。 |
| `_software_architecture(value)` | 将 WinRT x86/x64/其他架构保守映射到现有 SoftwareArchitecture。 |

### Analysis, safety, preflight and verification

| 函数 | 作用、输入/输出、异常与安全副作用 |
|---|---|
| `MsixInventoryService.__init__(platform, max_items)` | 注入窄平台并校验正上限。 |
| `MsixInventoryService.collect(cancellation)` | 读取新当前用户清单；无模型和 GUI 依赖。 |
| `MsixTargetResolver.resolve(query, inventory)` | 精确摘要/Full Name/Family 只能匹配一个；展示文字只返回候选，清单不完整时不选择。 |
| `MsixPackageTypeClassifier.classify(package)` | 强标志优先，再保护 System 签名、Windows 核心 Family 和 Security 类；不会用名称降低保护。 |
| `MsixRemovalPolicy.assess(...)` | 合并范围、类型、现有 SoftwareSafetyClass、健康、依赖、预检和提权状态；任一不确定即 BLOCK。 |
| `MsixPreviewService.__init__(ttl_seconds, now)` | 配置正有效期和可测试时钟。 |
| `MsixPreviewService.build(...)` | 生成绑定所有证据的 Preview，固定写入 Roamable/LocalState/依赖/无额外删除的真实语义。 |
| `MsixExecutionValidator.__init__(platform, max_items)` | 组合执行前清单和关系重查服务。 |
| `MsixExecutionValidator.validate(preview, fresh_preflight, cancellation)` | 比较精确身份、依赖和预检摘要；Store 更新/关系变化/清单不完整抛 `RuntimeError`。 |
| `MsixUninstallVerifier.verify(original, inventory, result_category, ...)` | 新清单区分仍注册、同 Family 新实例、已移除、软件证据仍在、中断和无法验证；不把 API 返回当成功。 |
| `MsixResidualAnalyzer.inspect(installed_path)` | 只对一个已知路径调用 `lstat`；不枚举、不递归、不删除。 |
| `ready_msix_preflight(another_uninstall_active)` | 为测试/无关联对象适配器构造完整预检；并发存在时为 BLOCKED。 |
| `MsixExecutionPreflightService.__init__(platform, max_items)` | 注入只读系统诊断平台和有界数量。 |
| `MsixExecutionPreflightService.inspect(package, active)` | 将解析后的 Package 安装根与进程/服务可执行路径相关联；进程只警告，运行服务/不完整/并发阻止，不执行控制。 |
| `_inside(path, root)` | 严格解析并用 `relative_to` 判断路径属于关系；错误返回 False。 |

### Workflow, confirmations and persistence

| 函数 | 作用、输入/输出、异常与安全副作用 |
|---|---|
| `MsixUninstallService.__init__(...)` | 注入平台、持久层、确认、注册表、审计、预检和提权探针；无全局可变状态。 |
| `MsixUninstallService.prepare(goal, query, cancellation)` | Fresh inventory→resolve→classify→dependencies→preflight→policy→plan/Preview→durable transaction→audit→plan confirmation；BLOCK 时不创建执行权。 |
| `MsixUninstallService.resolve_confirmation(...)` | 解析任一级精确确认并立即审计；拒绝/过期/不匹配不继续。 |
| `MsixUninstallService.prepare_runtime_confirmation(...)` | 进入 VALIDATING，重查身份/依赖/预检后才发短期即时确认。 |
| `MsixUninstallService.execute(...)` | 再次重查、原子消费双确认、写入 pre-dispatch audit、通过 ToolRegistry 一次执行并持久化验证终态。 |
| `MsixUninstallService._validated_action(...)` | 从 Fresh 本地事实构造唯一内部 Action；版本/依赖/预检变化抛 `MsixUninstallExecutionError`。 |
| `MsixUninstallService._safety_class(type)` | 只把 User App 映射为 USER_APPLICATION；System/Security 映射为保护类，其余 Unknown。 |
| `MsixConfirmationStore.save_confirmation/get_confirmation/update_confirmation/consume_pair` | 持久协议：保存、读取、迁移和原子消费 Gate；实现必须单次使用。 |
| `MsixConfirmationService.__init__(store, plan_ttl_seconds, runtime_ttl_seconds, now)` | 校验 TTL 并注入可测试时钟。 |
| `MsixConfirmationService.request_plan(...)` | 只对未过期可执行 Preview 创建第一 Gate。 |
| `MsixConfirmationService.approve(...)` | 校验所有摘要并批准/拒绝一个 PENDING Gate；重放抛 `MsixConfirmationError`。 |
| `MsixConfirmationService.request_runtime(...)` | 要求当前 APPROVED plan Gate，再创建 parent-bound 短期 Gate。 |
| `MsixConfirmationService.consume_runtime(...)` | 校验并一次原子消费 plan/runtime Gate；再次调用失败。 |
| `MsixConfirmationService._create(...)` | 绑定 PFN/Family/version/scope/type/dependency/policy/preflight/data impact/risk 并生成具体对象摘要。 |
| `MsixConfirmationService._require_current/_require_binding/_require_not_expired` | 分别检查 Preview 可执行性、全字段相等和到期；到期会持久化 EXPIRED。 |
| `_impact_digest(preview)` | 对明确展示的数据影响生成确认摘要。 |
| `MsixUninstallRepository.__init__(database_path)` | 创建独立 SQLAlchemy engine/session factory；尚不建表。 |
| `MsixUninstallRepository.initialize()` | 建表、过期未决 Gate、把重启前活动事务标记 INTERRUPTED；绝不重新派发。 |
| `MsixUninstallRepository.create(plan, preview)` | 检查 MSI/Vendor/winget/MSIX 全局并发并保存 request digest；持久化失败阻止确认。 |
| `MsixUninstallRepository.has_active_uninstall(exclude)` | 跨四机制返回活动状态，可排除当前 MSIX 事务。 |
| `MsixUninstallRepository.state/transition` | 读取或持久化事务状态；终态不可改写。 |
| `MsixUninstallRepository.save_confirmation/get_confirmation/update_confirmation` | 持久化强类型 Gate 和相应事务状态。 |
| `MsixUninstallRepository.consume_pair(...)` | 同一 SQLite 事务中消费两个 APPROVED Gate 并标记 DISPATCHING。 |
| `MsixUninstallRepository.close()` | 释放数据库连接池。 |
| `MsixUninstallRepository._transaction/_require_initialized` | 内部精确行解析与初始化 Gate；缺失抛 `MsixUninstallStoreError`。 |
| `MsixUninstallExecutionGuard.__init__(repository)` | 注入持久授权源。 |
| `MsixUninstallExecutionGuard.require(authorization, tool_name, arguments)` | 比较 UUID、工具、两份 argument digest 和 consumed runtime Gate，并在同一事务标记 EXECUTING。 |
| `_request_for_preview(preview)` | 生成唯一可被预留的 typed request，固定 Roamable 选项。 |
| `_confirmation_row(confirmation)` | 转换为 digest-bound SQLite 行，不加入 manifest/路径/数据。 |
| `_any_active_uninstall(session, exclude)` | 用固定 SQL 读取四类 additive transaction 表，默认全局互斥。 |

### Tool, audit and Qt

| 函数 | 作用、输入/输出、异常与安全副作用 |
|---|---|
| `MsixUninstallTool.__init__(platform, max_inventory_items)` | 创建唯一 R2/不可逆/双确认/批量 1 的 `software.uninstall.msix` manifest。 |
| `MsixUninstallTool.manifest` | 返回固定 ToolManifest。 |
| `MsixUninstallTool.execute(request, cancellation)` | 只接受 typed request；调用窄移除、Fresh inventory、verifier 和 report-only residual analyzer。 |
| `_package_from_action(request)` | 为 verifier 重建最小 Normalized projection；无 I/O。 |
| `MsixUninstallAuditLogger.__init__(repository, app_version, git_commit)` | 注入追加式审计和版本元数据。 |
| `previewed/confirmation_resolved/started/completed` | 分别记录 Preview、用户决定、强制 pre-dispatch 和部署+验证事件；只存摘要与布尔影响，不存 manifest/用户数据。 |
| `MsixPrepareWorker.__init__/run/cancel` | 后台准备 Fresh Preview；成功发强类型 bundle，失败发净化文本，取消仅协作式。 |
| `MsixRuntimePrepareWorker.__init__/run` | 后台执行确认前 revalidation 并发即时 Gate；无写调用。 |
| `MsixExecuteWorker.__init__/run/cancel` | 后台单次执行；取消只在 dispatch 前有效，不终止 WinRT 操作。 |
| `require_prepared_msix/require_msix_runtime/require_msix_result` | 收窄 Qt `Signal(object)`；类型错误抛 `TypeError`。 |
| `MsixUninstallDialog.__init__/_build_ui/_start_prepare` | 建立默认取消、主按钮禁用的非模态双确认窗口，并在线程池开始只读准备。 |
| `_prepared/_runtime_prepared/_completed` | 收窄 worker 结果并推进 PLAN/RUNTIME/DONE，最终分开显示 deployment 与 inventory verification。 |
| `_primary_clicked` | PLAN 时批准并重查，RUNTIME 时批准并执行；其他状态只能关闭。 |
| `_show_preview` | 两次明确展示 PFN、Family、版本、范围、类型、LocalState/依赖/Roamable 和 NONE 回滚。 |
| `_busy/_failed/_cancel_clicked/_required/closeEvent` | 管理忙碌/失败/取消/状态完整性/窗口关闭；不把关闭解释为强杀。 |
| `_result_html(result)` | 输出净化的部署类别、验证状态、理由和无额外数据删除声明。 |

## Stage 4D3 卸载后残留分析 API

本节覆盖 Stage 4D3 新增的每个函数和方法。所有“扫描”都只读取文件系统元数据；除用户主动
选择的本地报告导出外，没有函数会创建、修改、移动、回收或删除候选对象。

### Domain、身份与模型脱敏

| 函数 | 作用、输入/输出、异常与安全副作用 |
|---|---|
| `ContextPathEvidence.validate_related_target()` | Pydantic 后置校验。仅允许 `SHORTCUT` 来源携带卸载前已记录的 target 路径；其他来源伪造 target 时抛 `ValueError`。无 I/O。 |
| `UninstallContext.validate_path_evidence()` | 拒绝重复的 path/source 证据和“没有完成时间却声称完成/已验证”的矛盾上下文。失败抛 `ValueError`，防止不可信上下文进入扫描。 |
| `UninstallContext.canonical_digest()` | 对软件身份、卸载机制、精确路径、验证状态和警告生成 SHA-256；用于计划/报告陈旧性绑定，不读取磁盘。 |
| `UninstallContext.eligible_for_analysis` | 只在事务完整且为 `verified_removed` 或精确 `completed_unverified` 时返回 `True`；失败/中断/草稿返回 `False`。 |
| `ResidualIdentity.canonical_digest()` | 对规范化路径、device/file ID、对象类型、大小和 mtime-ns 生成稳定摘要；不使用文件内容哈希。 |
| `ResidualReport.enforce_report_only()` | 强制 `deletion_performed=false`，校验所有 Candidate 属于本 Report 且摘要计数一致；破坏性/串报数据抛 `ValueError`。 |
| `ResidualReport.to_model_payload()` | 把本地完整报告转换为 provider-neutral、路径脱敏、metadata-only 的解释载荷；只保留分类、大小、confidence、evidence code、protection 和 risk flag。 |
| `ResidualModelPayload.enforce_read_only_payload()` | 防止模型载荷声称执行过删除；`deletion_performed=true` 抛 `ValueError`。 |
| `_redacted_model_path(path)` | 按最长已知环境根将路径转为 `%LOCALAPPDATA%`、`%APPDATA%`、`%USERPROFILE%`、`%PROGRAMDATA%` 或 `%PROGRAMFILES%`；无法映射时只保留 `<LOCAL_PATH>/<leaf>`。无磁盘 I/O。 |

主要不可变模型还包括 `UninstallMechanism`、`ResidualSource`、`ResidualClassification`、
`OwnershipConfidence`、`OwnershipEvidenceStrength`、`UserDataProtectionLevel`、
`ResidualObjectType`、`ResidualRecommendation`、`ResidualAnalysisStatus`、`OwnershipEvidence`、
`ResidualIssue`、`ResidualCandidate`、`ResidualReportSummary`、三组工具 Request/Result，以及只供模型
解释的 `ResidualModelCandidate`/`ResidualModelPayload`。这些模型本身不持有执行器。

### 卸载上下文记录

| 函数 | 作用、输入/输出、异常与安全副作用 |
|---|---|
| `UninstallContextRecorder.__init__(repository)` | 注入独立 SQLite 残留仓库；构造时不查询卸载器、不访问文件内容。 |
| `capture_msi(preview)` | 在 MSI 真正派发前复制 transaction、Software identity、ProductCode、Publisher、scope/architecture 和精确 InstallLocation；保存失败记录净化警告并返回 `None`，不会改变 MSI 授权。 |
| `capture_vendor(preview)` | 复制 Vendor 软件身份和精确 InstallLocation；明确不读取/存储 raw/quiet uninstall string 或 argv。失败返回 `None`。 |
| `capture_winget(preview)` | 复制官方 source Package ID、映射后的 Software identity/version/scope 和精确 InstallLocation；不存 `winget` 命令。 |
| `capture_msix(preview)` | 复制 Package Family/Full Name/版本/架构/current-user scope、InstalledPath，并从安全 leaf Family 构造精确 `%LOCALAPPDATA%/Packages/<family>` 强保护路径。 |
| `finalize(context_id, verification_state, verified_removed, completed_unverified, completed_at)` | 卸载验证后把草稿原子更新为真实终态；仓库不可用返回 `False`，不伪造残留可用性。 |
| `_store_draft(context)` | Best-effort 持久化一个预派发上下文并返回 UUID；只记录错误类型和 transaction UUID。 |
| `_install_location_evidence(path, publisher, display_name)` | 把一个非空精确安装路径变成 `INSTALL_LOCATION` 强证据；不会推测相邻目录。 |
| `_looks_like_shared_publisher_root(path, publisher, display_name)` | 规范化 leaf/publisher/product 文本；若精确路径只是 publisher 根而非产品根，标为 shared，降低 confidence。无 I/O。 |
| `_missing_path_warning(known)` | 没有精确路径时生成“不按名称搜索”的警告；不尝试补猜。 |
| `_msix_package_data_path(family_name)` | 仅接受不含分隔符、`.`/`..` 的单一 Family leaf；从本地环境构造当前用户 Package 路径，非法或缺环境返回 `None`。 |

MSI、Vendor、winget、MSIX 四个 `execute()` 现在仅多出 recorder hook：适配器调用前 capture，
验证/异常后 finalize。四个卸载对话框的 `_completed()` 只在合格终态显示入口；
`_open_residual_analysis()` 仅把真实 transaction UUID 交给 Stage 4D3 对话框。

### Scope、分类、Ownership 与保护

| 函数 | 作用、输入/输出、异常与安全副作用 |
|---|---|
| `ResidualScanScopePolicy.__init__(max_roots, network_path_detector, extra_forbidden)` | 配置 1–32 个精确根、网络路径探针和附加禁区；错误上限抛 `ValueError`。 |
| `validated_roots(context)` | 要求 eligible context、非空且有界的 exact evidence；规范化、去重，并拒绝 drive/profile/Program Files/ProgramData/AppData/用户库等宽根、traversal、UNC/network、敏感/禁止根。失败抛 `ResidualScopeError` 且扫描尚未开始。 |
| `validate_existing_root(root)` | 枚举前重新检查语法、禁区、网络、父链 reparse、`lstat` 可用性和 identity 合法性；任何不确定 fail-closed。 |
| `entry_rejection_reason(path, root)` | 对每个发现项返回 `unsafe-path-syntax`、`scope-escape`、`protected-path`、`network-path`、`reparse-point` 或 `None`；不跟随链接。 |
| `scope_digest(context)` | 对已验证根及 source/depth 约束生成 SHA-256，供 Plan Reviewer 比对。 |
| `_is_forbidden(path)` | 通过共享 `PathPolicy` 判断一个候选是否命中默认/自定义禁区。 |
| `_is_broad_root(path)` | 精确比较 drive、profile、Windows、Program Files、ProgramData、AppData、Desktop/Documents/Downloads 根；只允许更窄的 app-specific 证据。 |
| `_validate_syntax(path)` | 要求绝对、本地、无 `..`、无尾随空格/点的路径并做 lexical normalization；不调用 `resolve()` 跟随链接。 |
| `_reject_reparse_components(path)` | 从最接近的现存祖先向上检查每个组件；发现 symlink/junction/reparse 抛 `ResidualScopeError`。 |
| `ResidualClassifier.classify(path, source, expected)` | 只用有限 source/component/suffix 规则确定分类和 reason codes；Package/shortcut/database/user-data 规则优先，绝不返回删除建议。 |
| `ResidualOwnershipEvaluator.evaluate(root_evidence, shared_location)` | 从 exact pre-uninstall path、Package/shortcut target 等结构证据产生 confidence/evidence；shared location 降为 MEDIUM。 |
| `ResidualOwnershipEvaluator.weak_name_only()` | 显式构造 LOW + `name-similarity-only`，保证名称相似不能升级为 HIGH。 |
| `UserDataProtectionPolicy.__init__(user_profile)` | 建立 Documents/Desktop/Downloads/Pictures/Music/Videos/Saved Games、Roaming 等保护根；可注入合成 profile 测试。 |
| `UserDataProtectionPolicy.protect(path, classification)` | 独立于 Ownership 应用最高保护：用户数据/数据库/Package data/开发环境等为 STRONGLY_PROTECTED，配置/插件/Unknown 至少 PROTECTED，其余至少 CAUTION。 |

### 有界 Collector

| 函数 | 作用、输入/输出、异常与安全副作用 |
|---|---|
| `ResidualCollectionBudget.__post_init__()` | 校验 1–25,000 对象和 0–600 秒上限，记录单调起点。 |
| `ResidualCollectionBudget.consume()` | 在每个被报告对象前消费一个名额；取消/超时/达到上限时设置真实终态并返回 `False`。 |
| `ResidualCollectionBudget.can_continue()` | 动态检查协作取消、超时和既有 stop status；无阻塞等待。 |
| `ResidualCollector.supports(source)` | Collector 协议：声明唯一负责的 source。 |
| `ResidualCollector.collect(evidence, report_id, budget, uninstall_verified)` | Collector 协议：在共享预算内返回 Candidate/Issue，不得写系统。 |
| `SourceResidualCollector.__init__(sources, scope, classifier, ownership, protection)` | 注入固定 source 集和四个确定性安全组件。 |
| `SourceResidualCollector.supports(source)` | 对 source 做有限集合判断。 |
| `SourceResidualCollector.collect(...)` | 对 exact root 执行 `lstat`、root identity revalidation、bounded `scandir`；目录/文件只读元数据，AccessDenied 等记 Issue，reparse 只报告不遍历。 |
| `SourceResidualCollector._candidate(...)` | 从一份 `stat_result` 构造 `ResidualIdentity`、分类、evidence/confidence、protection、recent/shared/unverified flags 和非删除 recommendation。 |
| `SourceResidualCollector._record_issue(issues, issue)` | 把 fail-soft issue 限制在 5,000 条，防止错误风暴耗尽内存。 |
| `InstallLocationResidualCollector.__init__(...)` | 只绑定 classic/MSIX exact install location source。 |
| `KnownAppDataResidualCollector.__init__(...)` | 只绑定卸载前已知的 app-specific data source。 |
| `ShortcutResidualCollector.__init__(...)` | 只绑定已知 `.lnk` 路径；不执行、也不深读快捷方式。 |
| `MsixDataResidualCollector.__init__(...)` | 只绑定 Package Family 映射的 exact Package data，后续统一强保护。 |
| `KnownServiceArtifactCollector.__init__(...)` | 只绑定先前已知的 service artifact path，不查询/修改服务。 |
| `KnownConfigurationResidualCollector.__init__(...)` | 只绑定明确 configuration path，并让保护策略优先。 |

### Plan、审查、分析与应用服务

| 函数 | 作用、输入/输出、异常与安全副作用 |
|---|---|
| `ResidualAnalysisPlanCompiler.__init__(scope, max_objects, timeout_seconds)` | 注入本地 scope policy 和有界资源配置；非法配置由模型/预算校验拒绝。 |
| `compile(user_goal, context)` | 从 eligible context 构造固定 analyze→report→inspect R0 Plan；scope 只能来自 policy，预计修改/删除恒为 0。 |
| `ResidualSafetyReviewer.__init__(registry, scope)` | 注入当前 ToolRegistry 和独立 scope policy。 |
| `review(plan, context)` | 验证工具恰为三个固定名称、均已注册/R0/read-only/NONE、无 runtime confirmation，且 plan/context/scope/argument digest 与零影响一致；返回结构化 issues，不执行。 |
| `ResidualAnalyzer.__init__(repository, scope, collectors)` | 注入持久层、scope 和固定 collector 集；构造时无扫描。 |
| `analyze(request, cancellation)` | 重取 context/比对 digest，创建共享预算，逐 exact source 找唯一 Collector，聚合大小/保护/issue/reparse 计数，保存 `deletion_performed=false` Report。 |
| `ResidualAnalysisService.__init__(repository, compiler, reviewer, confirmation, registry, audit)` | 组合计划、审查、确认、执行、持久化和审计边界。 |
| `prepare(user_goal, transaction_id)` | 从真实 transaction 读取 context、编译计划、独立审查并记录 aggregate audit；返回 Plan/Context/Review。 |
| `request_plan_confirmation(plan, context)` | Fresh re-review 后创建短时 R0 plan confirmation，具体说明 exact path 数量、修改 0、删除 0。 |
| `resolve_plan_confirmation(confirmation_id, approved, plan, context)` | 解析 digest-bound decision 并审计；拒绝/过期/错误绑定由 ConfirmationService 抛错。 |
| `analyze(plan, context, cancellation)` | 再审查并要求 plan 已批准，只通过 Registry 调用固定 analyze tool，检查返回类型，记录 aggregate completion audit。 |
| `latest_report(plan, context)` | 要求仍为当前已批准 plan，再调用 report tool；返回最新 Report 或 `None`。 |
| `inspect_candidate(plan, context, candidate_id)` | 要求当前 plan，再通过 opaque UUID 调 inspect tool；跨 context 候选返回 `None`。 |
| `export_report(plan, context, report, target, format, exporter)` | 重新检查 plan/context/report digest，exclusive-create 本地报告并写路径摘要审计；stale/cross-plan 抛 `ResidualAnalysisError`。 |
| `_require_current_plan(plan, context)` | 供 read/export 操作复用 reviewer + approved confirmation Gate。 |
| `_normalized(path)` | 生成 `abspath + normcase` 的 lexical comparison 字符串；不解析链接。 |
| `_append_issue(issues, issue)` | 全局把 issue 聚合限制在 5,000 条。 |
| `_utc_from_timestamp(value)` | 把 POSIX timestamp 转为带 UTC timezone 的 `datetime`。 |

### SQLite persistence

| 函数 | 作用、输入/输出、异常与安全副作用 |
|---|---|
| `SoftwareResidualRepository.__init__(database_path)` | 为 additive Stage 4D3 tables 创建独立 engine/session factory；尚不建表。 |
| `initialize()` | 建立 context/report/candidate 表并启用仓库；SQLAlchemy 失败转换为 `SoftwareResidualStoreError`。 |
| `upsert_context(context)` | 按 context UUID 插入或更新完整 JSON；transaction UUID 唯一，不存命令/内容。 |
| `get_context(context_id)` | 返回强类型 Context；缺失、损坏或未初始化抛 `SoftwareResidualStoreError`。 |
| `get_context_for_transaction(transaction_id)` | 通过真实卸载 transaction 唯一查 context；不做 display-name fallback。 |
| `list_eligible_contexts(limit)` | 返回最多 500 个符合模型 eligibility 的最近 context；供未来 referent UI 使用，不执行扫描。 |
| `save_report(report)` | 在一个事务中先 flush parent Report 再写 Candidate rows，避免 FK 顺序问题；报告不可变替换，删除标志已由模型阻止。 |
| `latest_report(context_id)` | 按完成时间读取一个 context 的最新 Report 并重建 Candidates。 |
| `get_candidate(context_id, candidate_id)` | 同时绑定 context 和 opaque candidate UUID，防止跨报告枚举。 |
| `close()` | dispose engine 并重置 initialized 状态。 |
| `_report_from_row(session, row)` | 从 JSON parent 与排序 candidate rows 重建 Pydantic Report；损坏数据抛 store error。 |
| `_require_initialized()` | 每次读写前 fail-closed 检查；未初始化抛 `SoftwareResidualStoreError`。 |

### Tool、audit、export 与 Windows Explorer

| 函数 | 作用、输入/输出、异常与安全副作用 |
|---|---|
| `_manifest(name, description, input_model, output_model)` | 构造固定 Windows R0/read-only/NONE manifest，最大批量 1，不需要 runtime confirmation。 |
| `SoftwareResidualAnalyzeTool.__init__(analyzer)` | 注入唯一 analyzer 并创建 `software.residuals.analyze` manifest。 |
| `SoftwareResidualAnalyzeTool.manifest` | 返回固定 manifest。 |
| `SoftwareResidualAnalyzeTool.execute(request, cancellation)` | 强校验 `ResidualAnalyzeRequest` 后调用 analyzer；返回 typed result。 |
| `SoftwareResidualReportTool.__init__(repository)` / `manifest` / `execute(...)` | 创建 report tool；只按 context UUID读取最新持久报告，cancellation 不改变数据。 |
| `SoftwareResidualInspectTool.__init__(repository)` / `manifest` / `execute(...)` | 创建 inspect tool；只按 context + candidate UUID 读取单项元数据。 |
| `SoftwareResidualAuditLogger.__init__(repository, app_version, git_commit)` | 注入 append-only audit 与版本证据。 |
| `plan_reviewed(...)` | 记录 request、plan/tool/digest、context/mechanism、path count 和审查结果；不存 path。 |
| `confirmation_resolved(...)` | 记录 confirmation UUID、plan/context digest 和 APPROVED/REJECTED/EXPIRED。 |
| `analysis_completed(...)` | 记录分类/confidence/protection aggregate、大小、issue/reparse/skipped、policy version、时间和 deletion/content-read=false。 |
| `report_exported(...)` | 记录 report/context、format、target SHA-256 和 no-overwrite/no-delete；不存完整目标路径。 |
| `_path_digest(path)` | 对 `abspath + normcase` 目标做 SHA-256，用于审计关联而非授权。 |
| `ResidualReportExporter.export(report, target, format)` | 用户主动创建新 JSON/CSV；使用 `open('x')`，完整路径仅写入本地报告；现存/网络/错误 suffix/目录不可用抛 `ResidualExportError`。 |
| `ResidualReportExporter._validate_target(target, format)` | 要求绝对、本地、无 traversal/歧义、父目录存在、suffix 匹配且 target 不存在。 |
| `WindowsResidualExplorerService.select_candidate(candidate)` | 重新确认 path 在 scan root 内、存在且非 reparse，然后以固定 `explorer.exe /select,<path>`、参数数组和 `shell=False` 打开；不执行候选。 |
| `_explorer_path()` | 从 Windows 目录构造固定 `explorer.exe` 并要求普通文件；缺失抛 `ResidualExplorerError`。 |
| `ApplicationRuntime.create_residual_analysis_services()` | 组合六 Collector、三 Tool、Repository、Compiler/Reviewer/Confirmation/Audit、Exporter 与 Explorer；UI 不自行执行系统工具。 |

### Qt worker 与 ResidualAnalysisDialog

| 函数 | 作用、输入/输出、异常与安全副作用 |
|---|---|
| `ResidualPrepareWorker.__init__(runtime, user_goal, transaction_id)` | 保存依赖和真实 transaction UUID，构造 terminal signals；不在 UI thread 查询 DB。 |
| `ResidualPrepareWorker.run()` | 在线程池创建服务并 prepare；成功发 `PreparedResidualAnalysis`，异常发类型+消息。 |
| `ResidualAnalyzeWorker.__init__(services, plan, context)` | 保存审查后的对象并创建独立 `CancellationToken`。 |
| `ResidualAnalyzeWorker.run()` | 后台调用 service analyze，发 Report 或安全失败文本。 |
| `ResidualAnalyzeWorker.cancel()` | 只设置协作取消，不终止线程、不修改候选。 |
| `ResidualExportWorker.__init__(services, plan, context, report, target, format)` | 保存 exact export binding；不立即写文件。 |
| `ResidualExportWorker.run()` | 后台调用 service export + audit，发 `ResidualExportResult` 或失败。 |
| `require_prepared_residual(value)` / `require_residual_report(value)` / `require_residual_export(value)` | 收窄 Qt `Signal(object)`；类型错误抛 `TypeError`，防止错误 payload 推进 UI。 |
| `ResidualAnalysisDialog.__init__(runtime, transaction_id, user_goal, parent)` | 建立状态、默认取消和只读窗口，然后异步准备真实 transaction 计划。 |
| `_build_ui()` | 创建风险说明、搜索/分类/保护筛选、九列表格、详情、进度、JSON/CSV/Explorer/确认/取消；没有 delete/cleanup/trash 控件。 |
| `_start_prepare()` | 创建并提交 prepare worker。 |
| `_prepared(value)` | 收窄结果、拒绝 failed review、申请 plan confirmation，展示 exact scope、0 修改/删除和 NONE rollback。 |
| `_primary_clicked()` | PLAN 状态批准并启动 analyze；DONE/FAILED 只关闭，其他状态无派发。 |
| `_completed(value)` | 收窄并显示真实 status/count/size/protected size/issues，填表并开放本地导出。 |
| `_populate_table(candidates)` | 强保护优先排序，填充 path/type/size/classification/confidence/protection/evidence/mtime/recommendation，再应用筛选。 |
| `_apply_filters()` | 组合本地路径 substring、classification 和 protection 条件；不触发新扫描。 |
| `_show_selected_details()` | 显示完整本地 path、identity digest、source、reason/evidence/risk 和“未来必须 fresh flow”说明。 |
| `_selected_candidate()` | 从当前 row 的 opaque UUID 映射回当前 Report Candidate；不存在返回 `None`。 |
| `_export(format)` | 让用户选择目标，创建 background export worker；取消文件对话框即停止。 |
| `_export_completed(value)` / `_export_failed(message)` | 恢复按钮并显示成功/失败；不把失败伪装成导出完成。 |
| `_open_selected_location()` | 只把当前 Candidate 交给安全 Explorer 服务；失败显示友好警告。 |
| `_failed(message)` | 进入 FAILED、停止进度并明确“没有删除/移动/回收/修改”。 |
| `_cancel_clicked()` | 请求 worker 协作取消；若仍在 PLAN，尽力记录 REJECTED confirmation，再关闭。 |
| `_required_state()` | 要求 services/plan/context/confirmation 全部存在；缺失 fail-closed。 |
| `closeEvent(event)` | 关闭窗口前复用取消路径，避免遗留失控扫描。 |
| `_format_size(value)` | 把非负字节转为 B/KiB/MiB/GiB/TiB 文本；无 I/O。 |
