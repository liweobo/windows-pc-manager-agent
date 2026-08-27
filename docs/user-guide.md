# User guide

## 使用 Stage 4X2 管理需要管理员权限的安全服务

普通情况下无需配置此功能。真实 Broker 默认关闭，而且未经签名的开发构建不会被当作生产版本。
启用且通过完整性检查后，只有以下流程会显示 UAC：

1. 在“服务”页面选择一个第三方、用户专属、已签名、独立进程且未被安全策略保护的服务。
2. 选择“启动”或“停止”。Restart 不走管理员 Broker；如需改变方向，必须分别操作。
3. 应用先生成普通 Stage 4C1 Preview。如果安全、依赖、身份或查询证据失败，流程立即停止；管理员
   权限和点击确认都不能绕过这些阻止。
4. 仅当所有安全检查通过、但普通用户确实缺少该项 SCM 控制权限时，界面才会显示独立的 R3
   管理员计划。第一次确认仍不会弹出 UAC。
5. 应用重新读取服务后显示短时即时确认。核对服务、动作、风险 `R3`、恢复 `MANUAL`，再点击
   “即时确认并请求管理员权限”。默认按钮仍是取消。
6. Windows UAC 只用于启动一个一次性 Broker。取消 UAC 不会执行，也不会自动再次弹出。
7. 完成页只有在 Broker 结果和主程序独立读取的服务状态都一致时才显示验证成功。

如果显示 `NOT_READY`，通常表示 Broker 未配置、哈希变化、安装位置不可信、缺少生产签名，或主
程序意外以管理员身份运行。不要关闭安全检查，也不要以管理员方式长期运行主程序。开发者可按
手工测试指南验证自签/发布签名构建；普通用户应等待正式签名安装包。

失败或中断不会自动重试。若要反向操作，请刷新服务状态并创建新的计划、两次确认和新的 UAC。
这属于人工恢复，不是自动 Undo。

## Stage 4X1 开发者 Mock 权限协议

普通用户无需开启此功能。默认设置为 `disabled`，现有服务、启动项和软件功能继续遵循各自
安全边界，不会因为权限不足自动请求管理员权限。

开发者可以临时设置 `PC_MANAGER_PRIVILEGED_BROKER_MODE=mock` 来查看协议 Preview。界面必须
同时显示“Mock only”“不会触发 UAC”“不会修改 Windows”。即使完成计划确认和即时确认，
Broker 也只会在内存中的假服务上模拟 Start/Stop，然后返回带验证状态的结果。Restart、服务
启动类型、机器启动项和机器 MSI action 会显示为未加入 allow-list，无法执行。

看到以下结果时请按字面理解：

- `MOCK_VALIDATED`：协议和假状态验证通过，不表示 Windows 操作成功。
- `REPLAY_REJECTED`：同一请求已使用或并发请求已输掉原子竞争；不能再次点击重试。
- `PERSISTENCE_UNAVAILABLE`：本地授权或审计存储不可信；安全默认是不执行。
- `INTERRUPTED`：应用曾在协议进行中退出；不会自动恢复或重发。

不要以管理员身份启动整个应用。Stage 4X1 没有真实提权能力。

## 使用 Stage 4D4 安全清理少量卸载残留

1. 先完成一次由 Agent 控制的软件卸载，再运行 Stage 4D3“分析可能残留”。
2. 在报告第一列勾选候选，然后点击“重新验证所选项”。这一步只是申请安全复核，不是删除确认。
3. 等待 Fresh Revalidation。窗口会逐项显示分类、Ownership、保护等级、文件/目录数、大小、
   回收能力和允许/阻止原因。任一项被阻止时，整批不执行；请关闭后重新选择。
4. 全部符合条件时阅读第一次“计划确认”。它说明 exact item set、R2/R2_HIGH_IMPACT、影响和
   `MANUAL` 恢复。确认后应用会再次扫描，但仍不会立即移动文件。
5. 阅读第二次“立即确认”。只有再次点击“移入 Windows 回收站”才会执行。默认按钮始终是取消。
6. 查看结果中的成功、失败和未执行数量。成功只表示 Windows 回收证据与原 identity 消失均已
   验证；失败/变化会停止后续项。

目前可能通过的类别只有 HIGH-confidence Program Residual、明确 app-specific Cache/Log，以及
卸载前已记录精确路径和 target、且 target 已不存在的 Shortcut。Owned 不等于 Eligible：配置、
数据库、用户数据、插件、License/Application State、MSIX Package User Data、Unknown、共享目录、
近期修改、link/junction/reparse、网络/不可靠卷等都会被阻止；没有“仍然强制”按钮。

所有真实处理都使用 Windows 回收站，没有永久删除后备方式。恢复等级为 MANUAL：打开 Windows
回收站，找到对象并选择“还原”。如果原位置已被新对象占用，Agent 不会覆盖它，请先人工判断。
取消只停止未来项；已完成项仍在回收站。应用异常退出后不会自动继续，必须查看审计/回收站并
重新生成 Fresh 计划。

默认最多选择 20 项、合计最多包含 10,000 个对象且不超过 50 GiB，超过即整批阻止。最多 5 项、
100 个对象、总量 1 GiB 且每项不超过 512 MiB 的批次为 R2；再大但仍在硬上限内时显示为
R2_HIGH_IMPACT。请以确认窗口显示的当前阈值和实际统计为准。

## 使用 Stage 4D3 卸载后残留分析

当 Agent 完成一次 MSI、Vendor、winget 或 MSIX 卸载，并且保存了可用的卸载上下文时，结果页会
显示“分析可能残留”。按钮只会打开一个 R0 只读流程：先展示软件、精确扫描范围、修改 0、删除 0
和回滚 NONE；默认按钮仍是取消。确认计划后，扫描在线程中进行，可以取消。

页面展示路径、对象类型、大小、分类、Ownership Confidence、保护等级、证据摘要、修改时间和
建议。默认先显示强保护数据；可按路径搜索，按分类和保护等级筛选，查看稳定身份/理由/风险，或
选择一个对象让 Explorer 打开所在位置。JSON/CSV 导出需要用户主动选择一个不存在的本地文件；
不会覆盖文件，也不会上传。

请这样理解结果：HIGH Confidence 只表示“证据较强地关联到原软件”，不表示“可以删除”。
Configuration、Documents/Downloads/Saved Games、Projects、数据库、插件、Roaming、MSIX
LocalState/Package Data 和 Unknown 项会被保护。`completed_unverified` 报告还会提醒：卸载本身
未被完全验证，因此可信度更低。

本阶段不会扫描整个 C 盘，不读取文件/数据库/配置/日志内容，不跟随链接，也没有删除、清理、
移动或回收站按钮。即使你提出删除请求，Stage 4D3 也只能生成报告。若将来实现 Stage 4D4，仍
必须重新扫描、重新验证身份、重新生成 Preview，并重新确认。

## 使用 Stage 4D2C1 受控 winget 卸载

1. 请以普通用户启动应用。在“系统诊断”刷新已安装软件，选择一行并启动“受控卸载”；路由器
   只有在本机元数据明确包含 `winget` 管理器和 Package ID 时才会进入本流程。
2. 应用在后台核对 Package ID、已安装版本、官方 `winget` Source、current-user 范围、软件
   身份和 Desktop App Installer alias。只凭名称、近似匹配或“winget 能看到”都不够。
3. 阅读第一次计划确认：核对软件、Package ID、版本、源、范围、R2/R2_HIGH_IMPACT、相关
   进程/服务、固定执行方式、Rollback NONE 与恢复说明。默认按钮是取消。
4. 确认计划后，应用会完整重新读取上述信息以及 winget busy/全局事务状态。任何 Package
   更新、Source/mapping/alias/风险变化会使第一次确认失效。
5. 阅读短时“即时确认”。它只授权这一个 Package、这个版本、官方源和当前用户范围一次。
   用户和模型不能添加 `silent`、`force`、`override`、`purge` 或其他参数。
6. 启动后可能显示 winget 或底层厂商安装器界面。Agent 不会请求 UAC、自动关闭程序、停止
   服务、点击界面、自动重启或重试。若安装器要求管理员权限，本次流程不会替你提升。
7. 执行中点击取消只会请求停止观察，不会强制结束 winget 或底层安装器。先处理可见窗口，
   再刷新清单；不要马上重复卸载。
8. 查看“进程结果”和“双重验证”两个独立结论。退出码 0 不是成功；只有 Package 清单与已
   安装软件清单都完整刷新且原精确 identity 同时消失，才是 `VERIFIED_REMOVED`。
9. Package 消失但软件仍在、软件消失但 Package 清单失败、Package 仍在或应用中断都不是已
   验证成功。请人工检查并稍后刷新，不要复用旧确认。
10. 残留报告只检查原已知安装目录本身，不会遍历或删除程序目录、AppData、配置、数据库、
    注册表或用户文件。

Rollback 是 `NONE`。需要恢复时，从可信发布者或官方源人工重新安装；重新安装不是 Undo，且
不保证恢复设置、许可证、插件和用户数据。本阶段不支持 Microsoft Store、MSIX/AppX、Custom
Source、machine-wide Package 或其他 Package Manager。

## 使用 Stage 4D2B 受控 Vendor 卸载

1. 在“系统诊断”刷新软件清单并选中一行，点击“受控审查选中 Vendor”；也可以在聊天输入
   “卸载软件 Example App”。聊天会先只读判断应进入 MSI 还是 Vendor 流程。名称有歧义时，
   必须自己选择一个精确候选。
2. 应用重新读取本机软件条目，但不会直接运行注册表里的 UninstallString。它会依次检查：
   current-user 范围、本地绝对 `.exe`、安装目录关系、文件身份与 SHA-256、离线数字签名、
   Publisher 匹配、有限参数策略，以及相关进程和服务。
3. CMD/PowerShell/pwsh、脚本宿主、Rundll32、`.bat/.cmd/.ps1/.vbs/.js/.wsf`、UNC/网络路径、
   相对路径、临时/下载/缓存目录、QuietUninstallString、machine-wide 软件或证据不足都会直接
   显示“不支持/已阻止”，没有“强制执行”按钮。
4. 阅读第一次计划确认：核对软件名称、版本、Publisher、范围、卸载器身份状态、签名、参数
   策略、R2/R2_HIGH_IMPACT、相关进程/服务、Rollback NONE 和“不自动进行”的操作。默认是取消。
5. 确认计划后，应用会完整重读所有信息。文件、哈希、参数、软件身份、风险或运行状态变化会
   使旧确认失效。第二次即时确认只授权这一份当前对象一次。
6. 启动后由厂商自己的窗口显示选项。请自己阅读并操作；Agent 不会自动点击“下一步”、选择
   “删除数据”、关闭其他程序、停止服务、请求管理员权限或重启电脑。
7. “停止监控”只表示 Agent 不再观察，不会终止厂商卸载器。长时间运行时可以继续使用厂商
   窗口；不要重复提交同一卸载。应用重启后旧任务会标记 `INTERRUPTED`，不会自动重启。
8. 结束后查看两种独立事实：“厂商进程结果”和“刷新后的软件清单验证”。只有原精确软件身份
   在完整 fresh inventory 中消失，才是 `VERIFIED_REMOVED`。退出码 0 但软件仍存在不算成功。
9. 残留报告只说明原已知安装目录是否存在；应用不会打开遍历、删除 AppData/ProgramData、
   用户文档、配置、数据库、注册表或任何残留。

Vendor 卸载的 Rollback 为 `NONE`。需要恢复时通常只能从可信来源人工重新安装，但重新安装不是
Undo，也不保证恢复原设置、许可证、插件或数据。第一版不支持 QuietUninstallString、复杂
bootstrapper family、脚本/Rundll32、machine-wide 提权、vendor-specific exit code、winget 或
MSIX。

## 使用 Stage 4D2A 受控 MSI 卸载

1. 在“系统诊断”先获取软件清单，选中一行后点击“受控卸载选中 MSI”；也可以在聊天输入
   “卸载软件 Example App”。请写清软件名称。名称有多个匹配时必须自己选择一行。
2. 应用在后台刷新本机清单和 Windows Installer 注册，并只读检查相关进程和服务。此时没有
   执行授权。非 MSI、低可信度、machine-wide、受保护类型或证据不完整会直接停止。
3. 阅读第一次“计划确认”：核对名称、版本、Publisher、current-user 范围、架构、ProductCode、
   软件分类、R2/R2_HIGH_IMPACT、相关进程/服务、Rollback NONE 和恢复说明。默认按钮是取消。
4. 确认计划后，应用会再次读取所有证据。任何升级、版本/ProductCode/Publisher/范围/风险
   变化都会使旧确认失效。
5. 阅读第二次“执行前即时确认”。只有这个短时确认才授权一次固定 MSI 调用。点击取消不会
   卸载。不要把开发运行时的 R2_HIGH_IMPACT 当成普通清理建议。
6. 确认后可能出现 Windows Installer 自己的窗口。请按其提示操作。Agent 不会替你关闭程序、
   停止服务、请求管理员权限或重启电脑。
7. 查看两个独立结果：“安装器返回类别”和“刷新后的最终验证”。只有
   `VERIFIED_REMOVED` 才表示两个本地清单都已确认原目标消失。`COMPLETED_UNVERIFIED` 表示
   不能确认，不能简单重复卸载。
8. `REBOOT_REQUIRED` 只是一条提醒，由你决定何时重启。`WAITING`/`INTERRUPTED` 表示安装器
   可能仍在运行或应用曾中断；先检查可见安装器和刷新后的软件清单，旧确认不能重用。
9. 残留报告只说明原已知安装目录是否仍存在。应用不会删除目录、AppData、Documents、
   ProgramData、设置、注册表残留或用户数据。

此 MSI 流程不能执行 Vendor Uninstaller、winget、MSIX、Portable App、驱动、Windows 组件、
安全软件、共享运行库或未知软件的卸载；合格 Vendor 目标必须进入独立 Stage 4D2B 流程。很多 MSI 需要管理员权限；当前版本会返回
`PRIVILEGE_REQUIRED` 或在计划阶段阻止，不会弹出 UAC。卸载没有自动 Undo，恢复通常需要从
可信来源重新安装，而重新安装不保证恢复原设置和数据。

## 使用 Stage 4D1 软件卸载分析

1. 先在“系统诊断”获取软件清单，选择一行并点击“分析选中软件的卸载影响”；也可以在聊天输入
   “分析卸载 Example App”。仅输入相似名称可能出现候选列表，应用不会替你选择。
2. 阅读 R0 计划：目标字段、五个只读工具、零个预计系统变更、无需管理员权限、回滚 NONE。
   点击计划确认只允许重新读取和分析，不允许卸载。
3. 若出现多个候选，核对名称、版本、发布者、范围、架构和来源后选中一个。选择会生成新计划，
   旧确认失效。
4. 阅读 Preview：稳定身份摘要、能力类型、元数据是否足够、安全分类、已知/启发式影响证据、
   未知项、风险理由和停止说明。它不是“可以安全卸载”的保证。
5. “我已理解目标”只记录你看过当前目标和 Preview。界面随后停止，并明确显示没有运行卸载器、
   没有修改系统，也没有删除程序文件或用户数据。

原始卸载字符串不会在界面或审计中显示。当前功能不运行 MSI、厂商卸载器、winget、MSIX、
PowerShell/CMD，也不请求 UAC。若看到“目标已变化”“证据不足”或“候选不唯一”，刷新软件清单
并创建新计划；不要把旧 Preview 当作当前状态。

## 使用 Stage 4C2 服务启动类型管理

1. 请以普通用户启动应用并打开“服务管理”。不要“以管理员身份运行”；本功能会主动阻止
   提权进程，且不会弹出 UAC。
2. 刷新并选择一个服务。表格会分别显示当前启动类型、是否 Delayed 和启动类型管理结论。
   只有 Stage 4C1 认定安全、没有依赖/被依赖项、当前为 Manual 或非延迟 Automatic 的单个
   第三方服务，才可能启用相反方向的 Preview 按钮。
3. 点击“Automatic Preview”或“Manual Preview”。如果普通用户没有该服务现有 DACL 授予的
   `SERVICE_CHANGE_CONFIG` 权限，流程会停止；不要用管理员模式绕过。
4. 阅读第一份 Preview：精确 ServiceName、稳定身份摘要、原/目标启动配置、当前运行状态、
   依赖影响、权限、加密备份验证、R2 风险和“有条件 FULL”回滚。此时服务没有被修改。
5. 确认计划后，程序会重新读取所有证据并显示短时即时确认。任何身份、配置、状态、依赖、
   权限、计划或备份变化都会使旧确认失效。
6. 即时确认后只会更改启动类型字段，并回读验证目标值和“当前运行状态未变化”。结果未明确
   显示 verified 时，请先刷新和人工检查，不要重复点击。
7. 要恢复时点击“查看可恢复记录”，选择由本 Agent 创建且尚未恢复的记录。恢复会确认当前
   配置仍等于 Agent 上次写入值，然后创建新备份并再次要求两次确认；出现冲突时不会覆盖。

Delayed Automatic 和 Disabled 只能查看。程序不会修改延迟标记、服务账户/密码、二进制路径、
依赖、恢复策略、安全权限，也不会启动/停止服务。`FULL` 仅指在无冲突且备份仍可验证时恢复
原启动类型，不代表恢复服务运行状态或内部会话。应用异常退出后，未完成事务不会自动继续。

## 使用 Stage 4C1 服务管理

1. 以普通用户启动应用，打开“服务管理”页并等待只读刷新。不要“以管理员身份运行”。
2. 用 ServiceName、显示名称、状态或发布者筛选并选择一行。只有“普通当前用户第三方服务”
   且相应操作显示可用时，才可以继续；其他行会说明只读或阻止原因。
3. 选择“检查启动”“检查停止”或“检查重启”。聊天也可输入“启动/停止/重启 服务名”，
   但聊天文字只是目标提示，程序仍从本机重新解析唯一 ServiceName。
4. 阅读第一份 Preview：精确服务身份、当前状态、安全分类、权限、依赖/被依赖服务、步骤、
   R2 或 R2_HIGH_IMPACT 风险和 `MANUAL` 回滚。此时尚未修改服务。
5. 确认计划后，程序重新读取全部证据并显示短时即时确认。状态、配置、依赖或权限变化会
   使旧确认失效，必须刷新并重新开始。
6. 执行后查看每一步和最终实际状态。Restart 始终是先 Stop、确认 STOPPED、再 Start；
   Stop 成功但 Start 失败会显示“部分完成”，不会自动重试。
7. “取消”只阻止尚未发送的下一步。已经发送给 SCM 的当前步骤会在有限超时内完成验证，
   它不能被撤回。需要反向操作时必须创建新 Preview 并重新确认。

程序不会级联启停依赖服务、终止服务进程、修改启动类型/账号/二进制路径、删除服务、执行
shell/WMI/`sc.exe`、请求管理员权限或自动恢复。`MANUAL` 表示重新启动/停止不能恢复服务的
内存会话。如果看到 `PARTIALLY_COMPLETED`、`INTERRUPTED`、`START_PENDING` 或
`STOP_PENDING`，先刷新查看当前状态，不要重复快速点击；确认状态稳定后再创建新计划。

## 使用 Stage 4B 启动项管理

1. 打开“启动项管理”页，等待只读刷新完成。上表是 Windows 当前配置，下表只显示本
   Agent 以前禁用且仍有可验证备份的条目。
2. 用名称、发布者、来源、状态或目标程序筛选。只有“可禁用”的普通当前用户第三方条目
   可以操作；其余条目显示只读或阻止原因。
3. 选中一行并点击“审查禁用”。阅读具体名称、来源、程序路径、状态、安全分类、R2、
   普通用户权限、备份摘要和 FULL 回滚的有效条件。
4. 完成计划确认后，程序会重新读取全部身份和备份。再次核对即时 Preview，再确认执行。
5. 查看验证结果。它只说明该配置当前已移出或已恢复，不保证程序以后一定/一定不启动。
6. 恢复时只能在下方 Agent 禁用列表选中记录并重复两次确认。若原位置已出现新对象、备份
   损坏或禁用材料变化，恢复会停止，绝不覆盖。

本功能不编辑 HKLM/RunOnce/公共 Startup，不禁用 Microsoft、Windows、安全软件、驱动、
企业管理或 Agent 自身条目，不写 StartupApproved，不批量处理，也不会请求管理员权限。

## 使用 Stage 4A 受控进程关闭

1. 在“系统诊断”页运行“进程”或性能诊断，选中一个明确进程，再点击“审查选中进程的
   关闭选项”；也可在聊天输入“关闭 demo”。
2. 等待后台实时查询。旧的 Stage 3 表格行只是线索，程序会重新读取 PID、进程名、路径、
   用户、启动时间、会话、窗口、服务和保护状态。
3. 阅读计划 Preview：具体应用组/成员、资源影响、安全分类、R2、普通用户权限和回滚
   `NONE`。系统、服务、安全软件、其他用户、Agent 等阻止项不会出现可执行确认按钮。
4. 点击“确认计划并重新验证”。此时仍未关闭进程；程序会再次读取身份和安全状态。
5. 阅读即时 Preview，在短时有效期内点击“请求正常退出”。应用可能自己弹出保存提示。
6. 查看逐 PID 验证结果。`EXITED`/`ALREADY_EXITED` 表示原身份已不存在；超时不等于成功。
7. 只有正常退出不支持或超时后，才可主动打开“查看强制终止选项”。它会创建全新的
   R2_HIGH_IMPACT 计划并再次要求两次确认，绝不会自动执行。

强制终止可能丢失未保存数据或损坏应用状态，回滚等级为 `NONE`。重新启动应用不是 Undo。
“停止等待/后续对象”也不是 Undo。程序不会请求管理员权限或在权限不足时重试提权。

聊天中的“关掉它”只有在最近一次性能结果给出了一个明确第一名，或你明确选择了一行时才
绑定目标；否则会要求选择，不会猜测。名称对应多个不同安装路径时也会拒绝并要求选行。

## 使用“系统诊断”页

1. 输入目标，或选择“系统概览”“性能诊断”“进程”“启动项”“服务”“软件”。
2. 阅读收集器、采样次数/间隔、对象上限、`R0`、修改数 `0` 和无需管理员权限声明。
3. 点击“确认 R0 计划”；计划变化后旧确认失效。
4. 点击“开始只读诊断”。任务在后台运行，“取消”会安全结束采样并跳过后续项。
5. 在概览、磁盘、进程、启动项、服务和软件分页查看结果。单项失败不删除其他结果。
6. 阅读确定性观察和实际阈值；它们不是故障根因或恶意软件判定。

当前版本不缓存启动项、服务或软件清单；每次执行都会重新读取，界面显示统一采集时间。

常用输入：`诊断电脑为什么卡顿`、`查看 CPU 和内存`、`查看进程资源占用`、
`查看开机启动项`、`查看 Windows 服务列表`、`查看已安装软件清单`。

Stage 3 不终止进程、禁用启动项/服务、卸载软件、修改注册表或请求管理员权限。

## Move selected objects to Windows Recycle Bin

1. Add an authorized ordinary personal directory and run file analysis, or open the
   **Windows Recycle Bin** tab and add paths manually.
2. Check the exact files you want. The Agent never chooses them for you.
3. Choose **Move to Recycle Bin**, then generate the R2 Preview.
4. Review every path, contained-object count, total bytes, hidden/system indicators,
   blocked reasons, and the MANUAL recovery warning.
5. Complete the first plan confirmation. Nothing is moved at this point.
6. The application rechecks all identities and directory contents. Complete the second
   immediate confirmation only if the displayed objects are still correct.
7. Review the result and recovery records. To restore, open Windows Recycle Bin, locate
   the object by original name and deletion time, right-click it, and choose **Restore**.

The operation is unavailable for protected/system/application-data paths, links/junctions,
network/removable/unknown/non-system volumes, or when Windows cannot prove Recycle Bin
capability. The app cannot restore an item after the Recycle Bin was emptied. It never
offers permanent deletion or clearing the Recycle Bin.

## First run

1. In PowerShell, run `uv sync --all-groups`, then `uv run pc-manager-agent`.
2. Keep the app running as your normal Windows account; do not use “Run as administrator”.
3. Open **文件分析** and choose **添加授权目录**. Read the exact path and confirm.
4. Optionally mark it as a common directory or add a protected child under
   **自定义禁止目录**. Authorization never includes the selected directory's parent.
5. Enter a goal, select one or more authorized roots, and adjust:
   - minimum size (default 1 GB);
   - suspected inactivity (default 90 days);
   - large/inactive/duplicate analyses;
   - “all conditions” or “any condition”.
6. Select **生成只读分析计划**, inspect scope, exclusions, thresholds, R0 risk, zero
   modifications/deletions, then explicitly confirm and start.

With no model configured, controls create the same deterministic plan locally. If a model
is configured, the app first asks whether it may send the goal, root labels/opaque IDs,
allowed analyses, and tool names. It does not send real paths or file details.

## Results

The progress area shows discovered files/directories, bytes, issues, and the current
analysis phase. **取消** requests a cooperative stop; completed safe batches remain visible
and the result is marked `CANCELLED`.

The table includes path, size, category, timestamps, candidate status, inactivity
confidence, and duplicate group. Use search, category, minimum size, sort field, and sort
direction; pages are loaded from SQLite rather than all held in memory.

- **大文件** means size is equal to or above the displayed threshold.
- **疑似长期未使用** means access and modification evidence pass the rule. NTFS atime may
  be disabled or deferred, so confidence is HIGH/MEDIUM/LOW/UNKNOWN and never proves value.
- **内容重复** means full SHA-256 matches; the default plan also compares bytes. The app
  does not choose which copy is original and offers no delete button.

**打开所在目录** asks Windows Explorer to select an existing authorized file. **导出**
creates a new local `.csv` or `.json`; existing files and network destinations are refused.
If export fails after creation, a partial file can remain and must be inspected/removed
manually. Stage 1 never deletes it automatically.

**生成模型说明** requires another confirmation and sends aggregate totals only. Every
number displayed in the explanation comes from local deterministic results.

## Safe file operations and Undo

In the analysis table, check only the rows you want and select **移动勾选项** or
**重命名勾选项**. The **安全文件操作** page also accepts explicit files/directories.
For a move, choose a destination that is itself inside an authorized root. For rename,
choose one finite rule: prefix, suffix, sequence, upper/lower case, literal replacement,
or modification-date prefix. The app never evaluates code or a regular expression.

Generating Preview is read-only. Review every final path and the totals for READY,
CONFLICT, BLOCKED, bytes, directory creation, R1, and FULL rollback. Existing target names,
unsafe/redirected paths, changed sources, permission errors, cross-volume moves, and batch
limits are displayed and are not silently fixed. Confirmation shows the concrete item and
byte impact. You must then separately start execution; changing/restarting invalidates it.

During execution, **停止后续操作** means the currently active Windows operation finishes
and is verified, then later items do not start. Completed items remain real and have Undo;
the result accurately shows success, failure, skipped, and pending counts.

To undo, select the exact transaction in history and generate **回滚 Preview**. The manager
uses stored Undo—not model guesses—and displays changed results, occupied original paths,
revoked scope, or non-empty created directories. Approve the separate rollback confirmation
only after checking it. A conflict is never overwritten; it requires manual resolution and
a fresh Preview.

Natural-language move/rename/organize requests require a configured provider and external
data confirmation. Only goal text, labels, opaque root IDs, enums, and tool names leave the
computer. Source discovery, metadata, years, real paths, Preview, safety and execution stay
local. With the provider disabled, the explicit selection/rule controls remain fully usable.

## Audit and local data

Open **审计** and refresh to view authorization, external consent, plan review, confirmation,
tool start/completion/failure, result, and export events. Local SQLite data is under the
directory shown in **设置**. Do not edit it while the app runs. Secrets and file contents
are not intentionally stored in audit events.

## Common errors

- **尚未授权目录**: add and select a root before planning.
- **outside approved scope / protected**: choose the exact intended local root; do not use
  its parent to bypass protection.
- **reparse point**: select the physical directory, not a shortcut, symlink, or junction.
- **Network-backed paths unavailable**: Stage 1 accepts local drives only.
- **NAME_CONFLICT**: another object owns the target; Stage 2A never overwrites or auto-renames.
- **CROSS_VOLUME_MOVE**: choose a destination on the same volume; copy-plus-delete is absent.
- **SOURCE_CHANGED / RESULT_CHANGED**: regenerate Preview after inspecting the current file.
- **INTERRUPTED**: the app stopped during a transaction and did not resume; inspect history
  and generate a rollback Preview.
- **old confirmation invalid**: regenerate and reconfirm after any scope/threshold change.
- **OpenAI configuration incomplete**: either disable the provider or set provider, model,
  and API key in the same PowerShell environment. Never paste a key into chat.
- **audit database failure**: verify the local data directory is writable and not locked;
  do not continue by deleting safety code or using administrator mode.
- **TARGET_AMBIGUOUS**：同名进程来自不同路径；回到进程表选择具体一行。
- **PROCESS_IDENTITY_CHANGED / TARGET_GROUP_CHANGED**：进程或浏览器帮助进程已经变化；
  生成新 Preview，不要复用旧确认。
- **UNSUPPORTED_GRACEFUL_EXIT**：没有可接收 `WM_CLOSE` 的顶层窗口；若确实需要，阅读并
  主动进入独立强制终止流程。
- **PROCESS_ACCESS_DENIED**：普通用户权限不足；Stage 4A 不会提权，请不要以管理员方式绕过。

Closing the window normally hides it to the tray. Use the tray's safe exit action to cancel
work, wait for workers, hide the icon, and close the application.
## 安全卸载当前用户 MSIX / Store App

只从软件表选择一个具体应用。应用会重新读取 Windows Package 身份；名称相似时必须重新
选择，不会批量卸载。Framework、Resource、系统、安全、依赖、Provisioned 和无法确定
类型的 Package 会直接阻止。

第一次确认是计划确认。第二次是卸载前即时确认，会显示 Package Full Name、Family、版本、
架构、当前用户范围、Package 类型、依赖状态和真实数据影响。Windows 可能移除该 Package
管理的 LocalState，也可能移除无人使用的依赖包；Agent 请求保留 Roamable 数据，并在发现
已知依赖风险时不执行。Agent 不额外删除用户文件。

卸载后以新清单为准。看到 `PACKAGE_INSTANCE_REPLACED` 表示同一 Family 出现了另一个版本，
不等于卸载完成。回滚为 `NONE`；需要恢复时应从 Microsoft Store 手动重新安装并检查数据。
