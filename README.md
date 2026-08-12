# Windows PC Manager Agent

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

它不会覆盖、永久删除或把文件移入回收站，也不会执行跨卷移动、管理员提权、Shell、
注册表、服务、启动项或软件修改。Stage 2A 只允许已授权本地目录内的 R1 可逆操作。

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
