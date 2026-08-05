# Windows PC Manager Agent

面向个人用户的 Windows 11 电脑管理 Agent。项目采用“先计划、再审查、再确认、
后执行”的安全边界；大模型只能生成结构化计划，不能直接操作电脑。

## 当前版本

阶段 0 / `0.1.0` 基础版本包含：

- PySide6 主窗口和系统托盘；
- 基础聊天、计划、风险提示与确认界面；
- 可替换的 `LLMProvider` 与 OpenAI 开发适配器；
- Pydantic 结构化任务计划；
- R0–R4 风险等级、工具注册表和安全审查；
- 与计划摘要和有效期绑定的确认状态机；
- SQLite 结构化审计日志及敏感字段脱敏；
- 不跟随符号链接/重解析点的只读目录元数据扫描；
- 后台扫描、取消、结果表格和最小无界面启动入口。

它不会移动、重命名、删除或回收文件，也不会修改注册表、服务、启动项或软件。

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
应用仍可使用确定性扫描流程，不会发起 API 请求。

## 运行

```powershell
uv run pc-manager-agent
```

关闭主窗口默认隐藏到托盘；从托盘菜单可以重新打开或安全退出。退出时会取消仍在
运行的扫描任务。

无界面启动检查：

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
uv run python -m pc_manager_agent --smoke-test
```

## 使用只读扫描

1. 在“只读扫描”页选择一个允许目录。
2. 检查计划中的根目录、排除目录、风险和文件数量上限。
3. 点击“确认计划”。
4. 点击“开始只读扫描”；可随时取消。
5. 扫描只读取元数据。链接、联接点、禁止目录和无权限对象会被跳过并记录。

## 开发与测试

```powershell
uv run ruff format --check .
uv run ruff check .
uv run mypy src
$env:QT_QPA_PLATFORM = "offscreen"
uv run pytest --cov=pc_manager_agent --cov-report=term-missing --cov-fail-under=85
uv run bandit -q -r src
uv run pip-audit
uv build
```

详细设计见 `docs/architecture.md`、`docs/security-model.md`、
`docs/threat-model.md` 和 `docs/developer-guide.md`。

## 本地数据

SQLite 审计数据库默认位于当前用户的本地应用数据目录，不位于仓库内。日志不会
保存 API Key、Token、Cookie 或文件正文。若审计存储不可用，工具执行会失败关闭。

## 回滚代码变更

已推送提交应使用新的修复分支和 `git revert` 回滚：

```powershell
git switch -c fix/revert-stage-0
git revert <commit-sha>
git push -u origin fix/revert-stage-0
```

不要把 `git reset --hard` 作为默认回滚手段。
