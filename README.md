# Windows PC Manager Agent

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

Stage 2A / `0.1.0` 开发版本在阶段 1 只读分析基础上包含：

- PySide6 主窗口和系统托盘；
- 基础聊天、计划、风险提示与确认界面；
- 可替换的 `LLMProvider` 与 OpenAI 开发适配器；
- Pydantic 结构化任务计划；
- R0–R4 风险等级、工具注册表和安全审查；
- 与计划摘要和有效期绑定的确认状态机；
- SQLite 结构化审计日志及敏感字段脱敏；
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

它不会覆盖或永久删除，不会执行跨卷移动、管理员提权、Shell、注册表、服务、启动项或
软件修改。Stage 2A 只允许已授权本地目录内的 R1 可逆操作，Stage 2B 仅支持回收站，
Stage 4A 仅支持上述受控进程生命周期操作。

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
