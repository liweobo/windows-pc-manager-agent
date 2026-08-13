# API reference

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
