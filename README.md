# Windows PC Manager Agent

## Stage 5B：按住说话，检查文字，再进入原业务审查

新增共享语音面板：按住说话 → 松开停录 → 单独确认本次音频上传 → 编辑识别文字 → 提交普通请求。
默认不录音、不上传、不播报；在“语音设置”显式选择供应商，密钥仍只从环境变量读取。
所有业务确认都保留在原页面，语音“确认”不会批准任何计划、即时确认或 UAC。
文本和语音共用同一个请求分流入口；具体文件、进程、服务或软件仍由原模块重新核对。

Windows 默认麦克风须支持 24kHz/单声道/16-bit PCM，否则明确拒绝，不调用外部转换器。
默认单次 60 秒、硬上限 120 秒；隐藏窗口、切换到其他应用、取消和退出均停止语音。
识别文字与音频不写入数据库，只有状态、数量、标识和摘要。云端处理可能收费且可能有保留政策。
“播报安全提示”仅发送独立确认过的有限摘要，播放的是 AI 合成语音；默认关闭。

详见 [语音使用及安全模型](docs/voice-interaction-model.md)、[逐函数 API](docs/api-voice-interaction.md)、
[真实设备手工验收](docs/voice-manual-tests.md)。自动测试使用模拟音频，没有替用户开启麦克风。
实际测试结果、跳过项和复现命令见 [Stage 5B 验证记录](docs/stage5b-validation.md)。

## Stage 5A：可预览、备份和恢复的办公文档操作

新增“办公文档”页面：明确选择文件后只读解析，支持 TXT/Markdown/JSON/CSV，以及保守的
DOCX/XLSX 文件级创建和编辑；PDF 仅提取文本或生成派生报告。默认另存为、不覆盖已有目标。
原地修改必须通过已验证备份、精确 Preview、计划确认和即时确认，再重新验证最终文件。
恢复只处理未被用户再次修改的 Agent 结果；中断不自动继续。没有宏、COM、Shell 或鼠标键盘控制。

可选 OpenAI 仅接收单独确认的最小片段，返回没有执行权限的结构化建议/原文摘录。
复杂 Word、受保护或含外部内容的 Office 包只读，公式不自动计算，OCR/PPTX 编辑/网络写入未实现。
详见 [实际能力与安全模型](docs/office-automation-model.md)、[逐函数 API](docs/api-office-automation.md)。
本地测试、性能、跳过项及复现方法见 [Stage 5A 验证记录](docs/stage5a-validation.md)。

## Stage 4E3：建议逐项复查与业务结果核验

“系统优化分析”的建议现在可以默认不勾选地加入复查清单，再逐项进入既有启动项、进程、
软件、残留、个人文件或系统清理模块。进入页面不等于执行：每次都重新选择当前对象，经过
原业务 Fresh 检查、独立 Preview 和确认。服务建议 V1 仅查看。没有“一键优化”、全局授权、
自动卸载后清理、通用管理员执行或全局 Undo。

清单分别记录未执行、取消、阻止、失败、结果未验证和业务已验证。关闭窗口不是成功，
卸载器退出也不是成功。新增只读结果读取器检查业务事务、确认关联和验证证据；已有 Stage 4X
特权结果仍以原业务页面为准，不伪装成 E3 已验证结果。取消停止后续复查，不终止外部卸载器。

用户可以另行确认一个最小只读刷新计划，对比相应领域的观测值；不承诺电脑更快，
不把移入回收站说成已释放空间。详见 [路由与安全边界](docs/optimization-action-routing.md)、
[逐函数 API 文档](docs/api-optimization-actions.md) 和 [使用指南](docs/user-guide.md)。

## Stage 4E2：受控系统清理与独立回收站清空

Stage 4E2 在 Stage 4E1 只读报告之后增加一个新的安全边界。旧报告、旧候选和第一次勾选只表达
意向：应用会重新枚举精确对象，重新验证文件身份、完整元数据树、分类、保护信号、近期活动、
普通删除访问权、安装器活动和目标卷回收站能力。Fresh 评估结果默认全部不勾选，用户必须再次
逐项选择；被阻止或转交其他流程的行不能混入直接清理计划。

V1 直接清理只允许当前用户 Temp、DirectX Shader Cache 和当前用户 CrashDumps 三个已知根下的
精确子对象，并且必须足够旧、无配置/数据库/用户数据等保护信号、无重解析点、未锁定且可使用
Windows 回收站。Stage 1 个人大文件/闲置/重复候选转交 Stage 2B；Stage 4D3 软件残留转交
Stage 4D4；浏览器缓存、系统 Temp、Windows Update、Delivery Optimization、Installer Cache、
WinSxS 和回收站内容不进入普通批次。

普通清理为 R2 或 R2_HIGH_IMPACT，要求计划确认和短时即时确认，执行前后重复 Fresh/TOCTOU
验证。写工具只接受 SQLite 中的对象引用，不接受路径、force 或任意命令；真实操作逐项复用
Windows Recycle Bin primitive，没有永久删除、shell、Broker、UAC、服务停止或进程终止后备
方式。成功项恢复等级为 `MANUAL`，必须从 Windows 回收站手动还原；移入回收站只表示对象离开
原位置，不代表磁盘空间已经释放。

“清空回收站”是完全独立的 R2_HIGH_IMPACT 流程：只针对当前用户的系统盘回收站，使用
`SHQueryRecycleBinW` 与 Shell namespace 得到完整数量、大小和删除时间范围，经过独立计划与即时
确认后才调用一次指定卷 `SHEmptyRecycleBinW`。任何内容变化都会使确认失效。恢复等级为 `NONE`，
Agent 无法还原清空后的对象。自动化测试全部使用合成适配器，从不清空真实回收站。

## Stage 4E1：只读系统清理与性能优化分析

Stage 4E1 新增独立的“系统优化分析”页面，但不会执行清理或调优。用户先查看并确认一个绑定摘要的
R0 计划。工具注册表固定为五个只读能力，但每个计划只选择目标所需的最小子集：空间问题不会额外
采集 CPU/服务，卡顿问题不会扫描缓存。应用生成证据关联建议，注册表不包含 Clean、Fix、Boost、
Apply、回收站写入、进程/服务/启动项
控制、卸载或 Elevated Broker。

个人文件只会在 Stage 1 已授权目录内读取元数据。临时目录、已知应用/浏览器缓存、日志和崩溃转储
只走有限位置清单，不打开文件内容，不跟随 symlink/junction/reparse point。浏览器 Cookie、密码、
会话、历史和完整 Profile、Windows Installer Cache、WinSxS、安全数据库和其他用户目录均受保护。
Windows Update 或 Delivery Optimization 没有可靠普通用户只读来源时会显示 `UNAVAILABLE`，不会通过
目录大小猜测。

报告分别展示实际观察空间、保守的潜在空间、受保护空间和未知空间。`CleanupCandidate`、性能发现、
建议与报告均固定为不可执行；旧报告、勾选和候选 UUID 不能授权未来 Stage 4E2。可显式导出新的
JSON/CSV 文件，目标使用独占创建且不会覆盖现有文件。

运行：

```powershell
uv sync --all-groups
uv run python -m pc_manager_agent
```

在“系统优化分析”页输入目标、选择可选的已授权个人目录、生成并确认只读计划，然后开始分析。

## Stage 4X3：专用管理员能力接入

Stage 4X3 延续一次性 Elevated Broker，但把权限边界扩展到三个已经完成独立安全设计的业务域：

- 服务启动类型：仅一个合格第三方服务的非延迟 `Automatic` ↔ `Manual`，以及基于 Agent 加密备份的冲突检查恢复；运行状态不得改变，回滚为条件式 `FULL`。
- 机器启动项：仅显式 32/64 位视图中的一个普通第三方 `HKLM\...\Run` 值；停用和恢复都绑定原始值、注册表视图和加密备份，冲突时停止，回滚为 `FULL`。
- 机器范围 MSI：仅一个精确、高可信、机器范围 MSI，在软件分类、Windows Installer 注册、进程/服务和全局卸载互斥检查全部通过后，使用固定 `msiexec /x {ProductCode} /norestart`；回滚为 `NONE`。

每种能力都有独立 Action、严格 Payload、清单版本、Broker 处理器和结果证据。主程序先执行既有业务安全策略，再创建新的 R3 计划；计划确认后重新验证，用户即时确认后才显示一次 UAC。Broker 在原子消费两次确认前后各做一次 Fresh 检查，操作后自行验证；普通用户主程序还会独立回读。任何安全阻止、版本漂移、身份变化、备份错误、UAC 取消或验证不确定都不会自动重试或切换执行方式。

Broker 仍不是管理员命令执行器：没有 PowerShell、CMD、任意可执行文件/参数、通用注册表写入、通用 SCM 配置、通用卸载命令、SYSTEM/TrustedInstaller 或安全策略绕过。机器范围 Vendor Uninstaller 明确延后。开发构建仍需按 Stage 4X2 的可信路径/哈希配置；生产可用仍取决于发布签名和安装信任。

## Stage 4X2：独立的一次性 Windows Elevated Broker

Stage 4X2 在 Stage 4X1 协议之上增加了真实但默认关闭的 Windows 提权边界。主程序始终以普通
用户运行；只有一个已通过 Stage 4C1 安全策略、且唯一阻塞原因是普通 SCM 权限不足的精确
`SERVICE_START` 或 `SERVICE_STOP`，才可创建新的 R3 计划。计划确认和短时即时确认均通过后，
应用才会使用 Windows `runas` 显示一次 UAC，并启动独立的一次性 Broker。

- Broker 命令行只有随机 rendezvous、Broker/Agent UUID、协议版本和预期调用进程 ID；没有服务
  名、命令、脚本、可执行路径或通用参数。
- Named Pipe 使用仅当前用户可访问的显式 ACL、拒绝远程客户端和首实例保护；双方再校验用户
  SID、登录会话、进程 ID/创建时间、镜像哈希、Agent 实例和 Broker 实例。
- 固定六帧握手建立仅本次会话使用的 HMAC；请求和结果均绑定固定序号、路由字段、摘要和时限。
- Broker 再次执行 allow-list、持久化、确认、Fresh 服务身份/状态/配置/依赖/安全检查，原子消费
  权限后只调用一个强类型 SCM Start 或 Stop 适配器；完成后退出。
- Broker 的返回不是最终成功。普通用户主程序还会独立读取 SCM 后置状态，不一致时报告验证失败。
- UAC 取消、IPC 超时/断开、重放、状态漂移、数据库或审计异常都不会自动重试。反向操作必须重新
  建立计划、确认并再次请求 UAC；回滚等级为 `MANUAL`。

默认仍为 `disabled`。仓库构建脚本可生成开发用固定哈希 Broker，但未经可信安装和 Authenticode
签名的构建在 `production` 信任模式下会保持 `NOT_READY`；本项目不会把开发构建冒充生产可用。
详见 `docs/privileged-action-protocol.md` 和 `docs/manual-testing/privileged-broker-uac.md`。

## Stage 4X1：Privileged Action Protocol（仅 Mock）

Stage 4X1 建立了未来独立提权 Broker 所需的协议边界，但**没有实现真实提权**。主 Agent
继续以普通用户运行，不触发 UAC，不启动管理员子进程，也不调用任何真实管理员 API。

- 权限解析始终晚于确定性安全审查；`AccessDenied` 本身不能授权提权。
- 协议只接受七种强类型 action；没有 command、script、executable、args 或通用参数字典。
- 本阶段仅把合成环境中的 `SERVICE_START` / `SERVICE_STOP` 加入 Mock allow-list；其余 action
  只有协议定义，不能执行。
- Request 绑定 Plan、Preview、两级确认、target/payload/risk/privilege digest、调用方上下文、
  32-byte nonce、UTC 有效期和协议版本，并使用 canonical JSON + HMAC-SHA-256 验证完整性。
- SQLite 原子消费 Request 与两级确认；重放、并发、过期、重启中断、数据库或审计不可用均
  fail closed，且不会自动重试。
- Broker 在消费前和 Mock 执行前分别 Fresh revalidate；Mock 只修改注入的内存假状态。

默认 `PC_MANAGER_PRIVILEGED_BROKER_MODE=disabled`。开发测试可显式设为 `mock`，界面会持续
显示“仅 Mock、没有真实系统操作”。完整协议见 `docs/privileged-action-protocol.md`。

## Stage 4D4：安全残留清理（Windows 回收站限定）

Stage 4D4 允许用户从 Stage 4D3 报告中勾选少量候选，但旧报告、旧勾选和旧 R0 确认都没有
执行权限。应用只按候选 UUID 在本地重新解析精确路径，完整重验对象身份、目录内容摘要、
Ownership、分类、用户数据保护、共享/近期活动、路径边界和目标卷回收站能力；原选择中只要有一项
被阻止，整批都不会执行，也不会静默只处理“看起来安全”的子集。

V1 仅允许 HIGH ownership 的 `PROGRAM_RESIDUAL`、app-specific `CACHE`、app-specific `LOG` 和
有卸载前精确 target 证据的 obsolete `SHORTCUT`。Configuration、User Data、Database、Plugin/
Extension、License/Application State、MSIX Package User Data、Unknown、共享路径、近期修改、
reparse/junction/symlink、网络/不可靠卷和证据不足对象始终阻止。Owned 不等于 Eligible，用户确认
不能覆盖确定性安全策略。

可执行批次会显示 exact item set、文件/目录数、总大小、隐藏/重解析统计、R2 或
R2_HIGH_IMPACT、`MOVE_TO_RECYCLE_BIN` 和 `MANUAL` 恢复。计划确认后再次完整扫描，临执行即时确认
后还要做最终 TOCTOU 重验；两级确认绑定 identity、material、classification、protection、
eligibility、risk 和 recovery capability，并且持久化、过期、只能消费一次。

默认硬上限为 20 个所选项、10,000 个包含对象和 50 GiB；超过任一上限直接阻止。批次不超过
5 个所选项、100 个包含对象、1 GiB 总量且单项不超过 512 MiB 时为 R2，超过普通阈值但仍在
硬上限内时提升为 R2_HIGH_IMPACT。阈值可由受校验的本地设置收紧或调整，确认页始终显示实值。

真实执行只调用复用的 Windows `IFileOperation` 回收站层。Shell 返回成功不等于最终成功；应用还
要确认原文件系统 identity 已消失，并为成功项保存 RecoveryRecord。取消只停止未来项，崩溃后的
active transaction 标记 `INTERRUPTED` 且绝不自动继续。没有永久删除 fallback、注册表清理、
自动 Restore、配置/数据库/用户数据/MSIX LocalState/RoamingState 清理、管理员提权或 Shell 命令。

## Stage 4D3：卸载后可能残留的只读分析

Stage 4D3 在 MSI、Vendor、winget 和 MSIX 受控卸载真正派发前，保存一份不含命令和文件内容的
`UninstallContext`。卸载结束后，用户可点击“分析可能残留”，确认一个 R0 计划，并只检查该事务
已经可靠记录的精确安装目录、应用数据、快捷方式、服务工件或 MSIX Package Family 路径。它不
会扫描整个磁盘，也不会根据软件名搜索 Documents、Desktop、Downloads、ProgramData 或 AppData。

报告按 Program Residual、Cache、Log、Configuration、User Data、Database、Plugin、Shortcut、
Package User Data、Unknown 等确定性类别展示大小、稳定身份、Ownership Evidence、Confidence、
保护等级、风险标志与修改时间。名称相似永远不是强归属证据；即使 Ownership 为 HIGH，也不表示
数据可以删除。Documents、项目/虚拟环境、数据库、配置、插件、Roaming 和 MSIX LocalState 默认
受保护或强保护。

本阶段只注册三个 R0 工具：`software.residuals.analyze`、`software.residuals.report` 和
`software.residuals.inspect`。扫描只读元数据，不打开文件/数据库/配置/日志内容；遇到 symlink、
junction 或 reparse point 会记录并跳过。用户可在本地导出新的 JSON/CSV 报告或安全打开 Explorer
定位对象，但没有删除、清理、移动、回收站或注册表写入口。Stage 4D3 的确认和报告不能在未来
复用为清理授权。

## Stage 4D2C1：受控 winget Package 卸载

Stage 4D2C1 新增且只新增一个写工具：`software.uninstall.winget`。它只处理一个精确的
current-user Package，并要求 Package ID、已安装版本、官方 `winget` Source、Installed
Software 身份和 Microsoft Desktop App Installer alias 都有当前、唯一且高可信的结构化证据。
Package Name、用户输入、模型输出、Source URL 和自由参数都不能进入执行器。
只读清单固定使用 `winget export --source winget`，不会让默认 Store 或自定义源扩大目标集合。

Windows 适配器不会搜索 PATH。它只检查当前用户 WindowsApps 中的 `winget.exe` App Execution
Alias，直接读取 `IO_REPARSE_TAG_APPEXECLINK` 并绑定 Desktop App Installer package family。
执行参数由代码固定为 `uninstall --id <ID> --exact --source winget --version <version>
--scope user --interactive --disable-interactivity`，并使用 absolute executable、显式 cwd、
DEVNULL、脱敏环境和 `shell=False`。没有 override/silent/force/all/purge、自定义源、UAC、自动
重启、进程终止、服务停止、MSIX/AppX/Store fallback 或残留删除。

普通用户应用/开发工具是 R2；开发运行时、数据库/后台平台是 R2_HIGH_IMPACT。共享运行库、
驱动/硬件、Windows、安全/网络、Agent、企业、Package Manager 和未知软件全部阻止。相关进程
只显示警告，相关运行服务、winget busy、清单不完整或另一 MSI/Vendor/winget 事务会阻止执行。

计划确认后会完整重验并产生短时即时确认。两个确认绑定 Package、Software、mapping、source、
version、alias、固定参数策略、安全/preflight/risk 摘要，持久化、过期且只能消费一次。退出码
只是一条证据；最终只有 fresh Package 清单和 Installed Software 清单都完整且原 identity 同时
消失，才报告 `VERIFIED_REMOVED`。Rollback 为 `NONE`，重新安装只是人工恢复，不是 Undo。

## Stage 4D2B：受控交互式 Vendor Uninstaller

Stage 4D2B 新增且只新增一个厂商卸载写工具：`software.uninstall.vendor`。它不接受原始
`UninstallString`，而是先用 Windows `CommandLineToArgvW` 只做解析，再要求一个明确的本地
绝对 `.exe`、current-user 软件身份、安装目录关系、稳定 File ID/大小/时间/SHA-256、有效的
离线 Authenticode 签名、保守的 Publisher 匹配，以及有限的交互式参数策略全部通过。

支持边界刻意很窄：普通 current-user 应用为 R2，明确的开发工具/运行时为
R2_HIGH_IMPACT。CMD、PowerShell、pwsh、WScript、CScript、MSHTA、Rundll32、脚本、UNC、
相对路径、PATH 搜索、临时/下载/缓存目录、QuietUninstallString、machine-wide 安装和受保护
软件全部阻止。参数只来自本机当前注册元数据，模型和用户都不能增加、删除或“修复”参数。

执行前有计划确认和短时即时确认；两者绑定软件、能力、卸载器文件、哈希、参数、策略、
preflight、风险与 Preview 摘要，并且只能消费一次。Windows 适配器使用绝对 executable 与参数
数组、固定安全 cwd、最小脱敏环境、`shell=False` 和 DEVNULL 标准流；不调用 runas，不自动
关闭进程、停止服务、点击厂商界面、重启、重试或删除残留。厂商界面由用户亲自操作。

进程退出码只是一条证据。退出后必须刷新 Installed Software 清单；只有原精确身份消失且清单
完整时才报告已验证移除。长时间运行或用户停止监控不会强杀卸载器，事务保持活动；应用重启会
标记 `INTERRUPTED` 并禁止自动重启卸载器。残留只做单路径 `lstat` 报告，Rollback 为 `NONE`；
重新安装是人工恢复建议，不是 Undo。

## Stage 4D2A：受控 MSI 软件卸载

Stage 4D2A 第一次开放真实软件卸载，但执行面只有一个工具：
`software.uninstall.msi`。它只接受由本机清单和 Windows Installer API 共同验证的
`ValidatedMsiProduct`，只处理 `current_user`、`USER_UNMANAGED`、高可信度 MSI。任何
Vendor UninstallString、winget、MSIX、Portable、PowerShell、CMD、WMI `Win32_Product`
或任意参数均不能进入执行器。

每次请求都会刷新软件清单，精确解析目标，再校验 ProductCode、名称、版本、发布者、范围、
架构和注册来源。安全策略只允许普通用户应用（R2）以及明确的开发工具/运行时
（R2_HIGH_IMPACT）；Visual C++ 等共享运行库、驱动、Windows/安全/网络/企业/Agent 组件和
未知类型全部阻止。名称有歧义时必须由用户明确选择，模型不能选择 ProductCode。

执行前先以只读方式检查安装目录内的相关进程和服务；发现运行对象或检查不完整就停止，绝不
自动关闭进程或停止服务。计划确认之后还会完整重新验证，并生成有效期很短的对象级即时确认。
两次确认都绑定 plan、transaction、Preview、软件身份、ProductCode、能力、安全决定、
preflight 和风险，且只能消费一次。

Windows 适配器只生成固定参数数组：系统目录中的 `msiexec.exe`、`/x`、严格 ProductCode、
`/norestart`，并始终使用 `shell=False`。它不自动提权、不自动重启、不强杀长时间运行的 MSI。
监控窗口耗尽时事务保持 `WAITING`，不做过早验证；应用重启后标记 `INTERRUPTED` 且绝不重试。
正常退出后同时刷新卸载注册表清单和 Windows Installer 注册；成功返回码不等于最终成功。
残留分析只检查已知安装目录是否仍存在，不枚举或删除任何文件。

软件卸载的回滚等级为 `NONE`。通常只能重新安装，而重新安装不等于 Undo，也不保证恢复设置或
用户数据。许多 machine-wide MSI 会因权限边界被阻止；本阶段不会请求 UAC。

## Stage 4D1：软件身份、卸载能力与影响 Preview（零执行）

Stage 4D1 只回答“这个软件是谁、Windows 目前暴露了哪类卸载元数据、可能影响什么”。它注册
且只注册五个 R0 工具：`software.inventory`、`software.resolve`、`software.inspect`、
`software.uninstall_capability`、`software.uninstall_preview`。所有结果都必须携带
`execution_performed=false`；独立守卫会拒绝工具集合扩大、风险升高或出现回滚/即时执行确认。

应用从 HKCU/HKLM 的 32/64 位卸载注册表视图读取元数据，保守区分 MSI、厂商卸载器、包管理器、
MSIX、Portable、Windows Feature、Driver 与 Unknown。原始卸载字符串仅在本地短暂解析，绝不
执行、不写日志、不发送给模型。名称相似不会自动选择目标；身份、版本、发布者、范围或架构
不唯一时，用户必须从候选项重新选择并生成新计划。

最终 Preview 显示安全分类、能力证据、相关进程/启动项/服务的只读影响线索、未知项、风险和
明确停止原因。按钮“我已理解目标”只记录与 plan/Preview 摘要及有效期绑定的 acknowledgement，
不会创建卸载授权。只有全新的 Stage 4D2A MSI 或 Stage 4D2B Vendor 流程能在各自更窄边界内
调用单个工具；Stage 4D1 acknowledgement 不能复用。winget、MSIX 和其他机制仍不存在。

## Stage 4C2：Windows 服务启动类型安全管理

Stage 4C2 在 Stage 4C1 的精确 ServiceName、身份复验和保护服务策略之上，增加三个且只有
三个配置工具：`system.service.startup.set_automatic`、
`system.service.startup.set_manual` 与 `system.service.startup.restore`。经用户明确批准的
安全边界只允许单个合格第三方服务在 **Automatic（非延迟）** 与 **Manual** 之间转换。
Delayed Automatic、Disabled、Boot/System、驱动、系统/Microsoft/安全/网络/登录/存储/
更新/企业/Agent 服务，以及存在任一依赖或被依赖服务的对象，仍为只读或阻止。

每次变更先用当前 Windows 用户的 DPAPI 加密并立即回读验证原配置，再展示 Preview、完成
计划确认、重新读取身份/配置/运行状态/依赖/权限并进行短时即时确认。执行只调用
`ChangeServiceConfig` 的启动类型字段，不修改延迟标记、二进制路径、账户、密码、依赖、
恢复策略或安全描述符，也不启动或停止服务。普通用户若本来没有
`SERVICE_CHANGE_CONFIG` 权限，操作会以 `PRIVILEGE_REQUIRED`/`ACCESS_DENIED` 停止；应用不会
请求 UAC 或以管理员模式重试。

成功变更会保存可验证的 Agent-owned 记录。`FULL` 仅表示在稳定身份未变、当前配置仍精确
等于 Agent 上次写入值、加密备份可解密验证且没有冲突时，能够通过一套全新的双确认 RESTORE
事务恢复原启动类型；它不恢复服务运行状态或内部会话。应用重启后未完成事务标记为
`INTERRUPTED`，绝不自动继续。

## Stage 4C1：Windows 服务安全启停与重启

Stage 4C1 新增“服务管理”页，但服务修改仍采用默认拒绝。程序只会为当前普通用户账户
运行的、独立进程型、签名验证通过、位于 Windows 与 Agent 目录之外、且未命中系统/登录/
网络/存储/更新/安全/企业保护规则的第三方服务显示操作入口。驱动、共享进程、LocalSystem、
LocalService、NetworkService、Microsoft/Windows、未知发布者和权限不明确的服务全部只读。

唯一注册的服务写工具是 `system.service.start` 与 `system.service.stop`。`START`、`STOP` 为
R2；`RESTART` 是 R2_HIGH_IMPACT，并被明确编排为 `STOP → 验证 STOPPED → 重新核对身份 →
START → 验证 RUNNING`，不存在单步 Restart、自动重试、依赖级联或失败后强制杀进程。
每次操作只针对一个精确 ServiceName，先展示 Preview，再进行计划确认和短时即时确认；
状态、配置身份、依赖图或权限证据发生变化时，旧确认立即失效。程序只调用 SCM/Win32 API，
不使用 PowerShell、CMD、`sc.exe`、WMI、shell 或管理员提权。

服务状态变化无法恢复服务内部会话，因此回滚等级如实标为 `MANUAL`。Restart 若 Stop 成功而
Start 失败或用户取消，结果会明确显示 `PARTIALLY_COMPLETED` 与当前实际状态，不会伪装成成功，
也不会后台自动重启。所有真实写操作前都有 SQLite 写前事务和隐私最小化审计；应用重启后
未完成事务标为 `INTERRUPTED`，不会自动继续。

## Stage 4B：启动项安全管理

Stage 4B 在现有项目中增加“启动项管理”页，但只开放两个窄工具：

- `startup.disable`：禁用一个经过重新读取、分类、完整备份和双重确认的当前用户启动项；
- `startup.restore`：只从 Agent 自己创建且验证通过的备份恢复一个启动项。

可管理范围限于 `HKCU\\...\\Run` 和当前用户 Startup Folder 中可可靠解析的 `.lnk`。
`RunOnce`、HKLM、公共 Startup Folder、Microsoft/Windows 组件、安全软件、驱动相关项、
企业管理项、Agent 自身以及身份不明的条目均保持只读或直接阻止。注册表值采用固定位置的
Win32 事务 API；快捷方式使用同卷移动且禁止覆盖。精确恢复材料先由当前 Windows 用户的
DPAPI 加密，再写入本地 SQLite；审计日志只保存摘要，不保存命令或备份字节。

禁用和恢复都保守定为 R2，必须依次完成计划确认和临执行即时确认。任一发布者、程序路径、
注册表值、快捷方式、Windows StartupApproved 证据、计划或备份摘要发生变化，旧确认立即
失效。`FULL` 表示在原位置仍无冲突且精确备份仍可解密验证时可以自动回滚，并不保证程序下一次
一定启动。应用不提供通用注册表编辑器、启动项删除、批量禁用、管理员提权或 Shell 退路。

## Stage 4A：受控进程关闭与终止

Stage 4A 在 Stage 3 只读进程清单上增加两个、也只有两个写工具：

- `system.process.request_exit`：R2，向目标应用自己的顶层窗口发送 `WM_CLOSE`；
- `system.process.force_terminate`：R2_HIGH_IMPACT，仅在独立新计划和新双重确认后调用
  `TerminateProcess`，绝不从正常退出自动升级。

在“系统诊断”中查询进程、选中一行并点击“审查选中进程的关闭选项”，或在聊天输入
“关闭 demo”。应用会重新解析当前目标，展示 PID、进程名、路径、用户、启动时间、应用组、
资源影响、安全分类、风险、权限和 `RollbackLevel.NONE`，然后依次要求计划确认和短时即时
确认。执行前会再次核对 PID + 创建时间 + 路径 + owner SID + session，防止 PID 复用。

系统/关键/受保护/安全软件/服务/其他用户/其他会话/Agent 自身进程始终阻止。无窗口后台
进程不会伪装成支持正常退出，只能由用户主动进入全新的强制终止 Preview。全程不收集命令
行、不使用管理员权限、不调用 shell/taskkill、不修改服务、启动项或注册表，也不承诺 Undo。

## Stage 3：Windows 系统状态只读诊断

当前分支在已有 Stage 0–2B 基础上加入 Stage 3。应用可以在普通用户权限下查看：

- Windows 版本、构建号、架构、处理器型号和启动时间；
- 多次采样的 CPU、物理内存与页面文件状态；
- 本地固定磁盘容量；
- 进程名称、PID、资源使用和有限元数据（明确不采集完整命令行）；
- HKCU/HKLM Run 项与用户/公共 Startup 文件夹；
- Windows 服务的只读状态和查询型配置；
- HKCU/HKLM 卸载注册表中的已安装软件清单（不读取或执行卸载命令）；
- 基于公开阈值的保守观察、可信度、证据和非执行型建议。

在“系统诊断”页输入“诊断电脑为什么卡顿”“查看启动项”或“查看已安装软件”，先检查
结构化 R0 计划，再点击确认和执行。查询在后台线程运行，可以取消；单个采集器失败时其余
结果仍会显示。Stage 3 没有终止进程、修改服务/启动项、卸载软件、写注册表、管理员提权、
PowerShell、CMD、WMI 或 `Win32_Product` 能力。

默认诊断无需配置模型。若启用 OpenAI，规划只可发送用户目标和固定收集器名称，解释只可
发送不含数值和本地对象身份的发现元数据；每次外发前仍须单独确认。

## Stage 2B Windows Recycle Bin

Stage 2B can move only files or directories that the user explicitly selects into the
Windows Recycle Bin. It uses a separate R2 workflow:

1. local deterministic plan from checked paths;
2. protected-path, volume-capability, identity, and full directory-tree Preview;
3. first plan confirmation;
4. fresh revalidation and a second short-lived immediate confirmation;
5. write-ahead MANUAL recovery record;
6. one-item Windows `IFileOperation` call and callback verification;
7. transaction/audit result plus manual Restore instructions.

The application has no permanent-delete or empty-Recycle-Bin tool. It blocks system and
application-data roots, an authorized root itself, reparse/system/offline objects,
network/removable/unknown volumes, and stale or overlapping selections. The initial
release accepts only the Windows system volume when it is writable fixed NTFS and its
Recycle Bin is queryable. Recovery is truthfully marked MANUAL; there is no automatic
restore claim.

面向个人用户的 Windows 11 电脑管理 Agent。项目采用“先计划、再审查、再确认、
后执行”的安全边界；大模型只能生成结构化计划，不能直接操作电脑。

## 当前版本

Stage 4D3 / `0.1.0` 开发版本在此前阶段基础上包含：

- PySide6 主窗口和系统托盘；
- 基础聊天、计划、风险提示与确认界面；
- 可替换的 `LLMProvider` 与 OpenAI 开发适配器；
- Pydantic 结构化任务计划；
- R0–R4 风险等级、工具注册表和安全审查；
- 与计划摘要和有效期绑定的确认状态机；
- SQLite 结构化审计日志及敏感字段脱敏；
- 软件身份归一化、保守目标解析、卸载能力分析与零执行影响 Preview；
- 与计划和 Preview 摘要绑定、但绝不授予卸载权限的目标理解确认；
- 单个 current-user 高可信度 MSI 的 ProductCode/API 交叉验证和默认拒绝执行策略；
- 双重一次性确认、SQLite 卸载事务、固定 `msiexec` 参数适配器和退出码分类；
- 对高可信 current-user Vendor `.exe` 的严格解析、身份/签名/参数验证和 shell-free 启动；
- MSI/Vendor 互斥事务、机制路由、厂商 UI 监控、fresh inventory 验证和只报告残留；
- 官方源 current-user winget 与 current-user ordinary MSIX/Store App 的窄受控卸载；
- 四种卸载机制的统一 `UninstallContext`、精确路径残留元数据报告、Ownership Evidence、
  Confidence 与独立用户数据保护；
- 无进程终止/服务停止/提权/重启/残留删除的 preflight、监控和后置验证；
- 用户管理的授权目录、常用目录与自定义禁止目录；
- 不跟随符号链接/联接点/重解析点的流式只读目录元数据扫描；
- 可配置大文件分析、证据化的“疑似长期未使用”分析；
- 大小分组、快速哈希、SHA-256 和可选逐字节验证的重复文件检测；
- 后台扫描、进度、取消、分页筛选、排序和资源管理器定位；
- 不覆盖已有文件的 CSV/JSON 报告导出；
- 发送前逐次确认的模型意图规划和聚合结果说明；
- 结构化审计、崩溃会话清理和最小无界面启动入口。
- 分析结果勾选、有限规则重命名、同卷移动和普通目录创建；
- 真实文件系统 Preview、名称冲突/批量/卷检查和 R1 精确确认；
- 基于 Windows Volume Serial Number + File ID + 元数据的 TOCTOU 复验；
- SQLite `OperationTransaction`、逐项状态、写前 Undo、失败即停止和异常中断检测；
- 从持久化 Undo 逆序生成的回滚 Preview、独立确认、冲突检查和结果验证。

它不会覆盖或永久删除，不会执行跨卷移动、管理员提权或 Shell。Stage 2A 只允许
已授权本地目录内的 R1 可逆操作，Stage 2B 仅支持回收站，Stage 4A 仅支持上述受控进程生命
周期操作，Stage 4B/4C1/4C2 也只开放各节列出的窄工具；Stage 4D2A/B/C1/C2 各自只有一个
专用卸载工具，Stage 4D3 只有三个只读报告工具；不存在通用软件、注册表、服务、包管理器、
残留清理或命令接口。

## 安装

需要 Windows 11、Python 3.11–3.14 和 [uv](https://docs.astral.sh/uv/)。

```powershell
git clone https://github.com/liweobo/windows-pc-manager-agent.git
cd windows-pc-manager-agent
uv sync --all-groups
```

如需启用 OpenAI 开发适配器，请在启动应用的同一个 PowerShell 窗口中设置：

```powershell
$env:PC_MANAGER_LLM_PROVIDER = "openai"
$env:OPENAI_MODEL = "你有权使用的模型 ID"
$env:OPENAI_API_KEY = "你的 API Key"
```

不要把真实密钥写入 `.env.example`、源代码、截图或 Git 提交。未配置模型时，
应用仍可通过界面阈值生成确定性计划，不会发起 API 请求。启用模型后，每次发送前都会
显示具体数据范围；真实路径、文件名和文件内容不会发送给规划模型。

## 运行

```powershell
uv run pc-manager-agent
```

关闭主窗口默认隐藏到托盘；从托盘菜单可以重新打开或安全退出。退出时会请求扫描停止，
写事务只停止尚未开始的后续项，已完成项会保留审计和 Undo。

无界面启动检查：

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
uv run python -m pc_manager_agent --smoke-test
```

## 使用阶段 1 文件分析

1. 在“文件分析”页添加一个明确授权的本地目录，可另行添加禁止子目录。
2. 输入目标，或直接调整大小、闲置天数、分析类型和匹配方式。
3. 检查结构化计划中的根目录、排除目录、阈值、R0 风险和零修改声明。
4. 点击“确认计划”，再点击“开始只读分析”；条件变化会使旧确认失效。
5. 查看进度；可随时协作式取消，已取得的安全结果会保留并标为 `CANCELLED`。
6. 在结果表按名称、路径、类型、大小或时间筛选和排序，或导出新 CSV/JSON 文件。

可直接尝试：

```text
帮我找出 Downloads 里超过 1GB 的文件。
帮我查看 Documents 中有没有重复文件。
找出 D:\Videos 中超过 500MB 且疑似半年未使用的文件。
```

“疑似长期未使用”只表示时间证据符合规则，不代表文件无用或可以删除。重复文件只在
完整 SHA-256 验证后成组，应用不会替用户选择“原件”或“副本”。

## 使用 Stage 2A 安全文件操作

1. 先在“文件分析”页授权来源和目标根目录；授权子目录不会授权它的父目录。
2. 在分析结果中勾选对象，点击“移动勾选项”或“重命名勾选项”；也可在“安全文件操作”
   页手动选文件/目录。启用模型后，聊天可理解“按修改年份整理 PDF”等有限意图。
3. 检查 Preview 的最终源/目标、目录创建、字节数、冲突、阻止项、R1 和 FULL 回滚数量。
4. 明确确认当前 Preview，再单独点击执行。确认只绑定当前 plan/preview 哈希且只能消费一次。
5. 执行中“停止后续操作”不会强杀当前 Win32 调用；当前项完成验证后才停止下一项。
6. 在事务历史选择一次操作，生成回滚 Preview，检查当前身份和原路径冲突，再独立确认回滚。

可尝试：

```text
把 Downloads 中的 PDF 移到 Documents\PDF。
把这些图片按 photo_001 开始编号。
把 Downloads 里的 PDF 按修改年份整理。
撤销刚才的整理。
```

跨磁盘移动会阻止；目标存在会标为冲突且不覆盖；回滚时原位置出现新对象或结果已修改会
停止相应恢复。事务创建的目录只有仍为同一目录且在逆序回滚后为空时才会移除。

## 使用 Stage 4D2C2 MSIX / Store App 安全卸载

从“系统状态”软件清单选择一个带精确 MSIX Package 身份的当前用户应用，进入受控卸载。
界面先重新读取当前用户 Package 清单，区分 Package Family 与具体版本实例，再检查类型、
依赖、进程、服务、并发事务和权限。Framework、Resource、Bundle、Optional、System、
Security、Provisioned、Dependency 和 Unknown Package 均不执行。

计划确认后会再次检查全部证据；只有 Package Full Name、版本、架构、范围、类型、依赖快照
和风险完全不变，才显示即时确认。确认会明确说明：Windows 可能移除 Package-managed
LocalState，也可能移除无人依赖的依赖包；本版只在没有已知依赖风险时允许继续，并固定请求
保留 Roamable 数据。Agent 不额外删除 AppData 或用户文件，不运行 PowerShell，不提权，
不结束进程，不停止服务。MSIX 卸载回滚等级为 `NONE`。

## 开发与测试

```powershell
uv run ruff format --check .
uv run ruff check .
uv run mypy src
$env:QT_QPA_PLATFORM = "offscreen"
uv run pytest -m "not performance" --cov=pc_manager_agent --cov-report=term-missing --cov-fail-under=85
uv run pytest tests/performance/test_large_scan.py -q -s
uv run pytest tests/integration/test_windows_process_management_real.py -q
uv run pytest tests/integration/test_windows_startup_readonly.py -q
uv run pytest tests/integration/test_msix_windows_inventory.py -q
uv run pytest tests/unit/test_privileged_broker_branches.py tests/integration/test_privileged_action_flow.py -q
uv run bandit -q -r src
uv run pip-audit
uv build
```

详细设计见 `docs/architecture.md`、`docs/security-model.md`、
`docs/threat-model.md` 和 `docs/developer-guide.md`。所有生产代码函数的签名、参数、
返回值、异常、副作用和安全约束见 `docs/api-reference.md`。

## 本地数据

SQLite 审计、授权、临时分析索引、操作事务和 Undo 默认位于当前用户的本地应用数据目录，
不位于仓库内。
日志不会保存 API Key、Token、Cookie 或文件正文。若审计存储不可用，工具执行会失败关闭。

## 回滚代码变更

已推送提交应使用新的修复分支和 `git revert` 回滚：

```powershell
git switch -c fix/revert-stage-0
git revert <commit-sha>
git push -u origin fix/revert-stage-0
```

不要把 `git reset --hard` 作为默认回滚手段。
