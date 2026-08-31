# Stage 5A 验证记录

日期：2026-08-31。环境：Windows、Python 3.13.1；所有文档均为临时合成数据，模型使用伪供应商。
这是窄范围 V1 的工程验证，不是完整 Microsoft Office 兼容性认证或签名发行认证。

## 最终本地结果

| 检查 | 实际结果 |
|---|---|
| 最终待提交快照的完整非性能回归 | 1379 passed，6 skipped，10 deselected；核心覆盖率 87.20%，门槛 85% |
| 最终 Office / Windows 文件安全专项 | 138 passed；覆盖率 98.17%，门槛 95% |
| Office 提交、Undo、Restore 模块 | 专项运行中语句及分支均为 100% |
| 综合安全门禁 | 832 passed、3 skipped，95.45%；最终权限摘要补强另外经过上述完整回归及 Office 专项 |
| 性能回归 | 10 passed，272.36 秒；含 3 项新 Office 负载 |
| Ruff 格式及检查 | 通过，688 个 Python 文件格式检查通过 |
| mypy | 通过，436 个生产源文件 |
| Bandit | 通过 |
| pip-audit | 未发现已知依赖漏洞；本地项目不是 PyPI 包，项目自身由静态检查及测试验证 |
| Gitleaks 8.30.1 | 对待提交索引导出的快照扫描，未发现密钥泄漏 |
| 锁定依赖同步 / wheel / sdist 构建 | 通过 |
| 无界面启动检查 | 通过，不写持久用户数据 |
| API 文档一致性 | 208 个函数/方法的名称和参数/返回签名逐项验证 |

完整回归使用从 Git 索引导出的临时快照，并显式把该快照的 `src` 放入 `PYTHONPATH`。
已确认实际导入来源是快照，而非当前目录的可编辑安装。这样不会把用户未暂存的配置修改
混入待提交代码的验证。初次隔离检查发生过导入目录错误，修正后重新完整运行，以上仅列最终结果。

6 项跳过：5 项需要本机未启用的符号链接/重解析测试权限，1 项真实回收站测试需要显式 opt-in。
未为测试提权；没有清空本机回收站。CI 保留独立的临时对象验证步骤。

综合安全门禁最初为 94.76%，未通过；将现有 Office 和系统优化确认专项纳入该门禁后为 95.45%。
没有下调覆盖率门槛，也没有把新增 Office 原生文件适配器排除出其 95% 专项。

## Office 性能样本

| 合成输入 | 解析时间 | Python 跟踪峰值内存 |
|---|---:|---:|
| 10,000 行 CSV | 0.683 秒 | 21.05 MiB |
| 10,000 行 XLSX | 6.068 秒 | 36.54 MiB |
| 100 页静态 PDF | 0.280 秒 | 0.98 MiB |

上述内存来自 tracemalloc，不等于完整进程工作集，也不包含所有原生库内存。
这是指定合成输入的测量，不承诺任意大文件都能在同样时间内完成。

## 复现入口

```powershell
uv sync --all-groups --locked
uv run ruff format --check .
uv run ruff check .
uv run mypy src
$env:QT_QPA_PLATFORM = 'offscreen'
uv run pytest -m 'not performance' --cov=pc_manager_agent --cov-report=term-missing --cov-fail-under=85 -q
uv run pytest tests/unit/office tests/integration/office tests/security/test_office_boundaries.py tests/security/test_office_failure_paths.py tests/gui/test_office_tab.py -q
uv run pytest tests/performance -q -s
uv run bandit -q -r src
uv run pip-audit
uv build
uv run python -m pc_manager_agent --smoke-test
```

精确专项覆盖命令见 `.github/workflows/ci.yml` 的 Stage 5A 步骤及 `tests/office-coverage.ini`。
密钥扫描只对待提交/已提交快照执行，输出使用 `--redact`；不要把用户自己的 `.env` 内容粘贴进聊天。

## 尚需区分的验证

- GitHub Actions 的最终状态以本次提交对应运行页面为准，本地成功不冒充云端 CI 成功。
- Python 3.11 的验证由 CI 矩阵执行，本地只实际运行了 Python 3.13.1。
- 未向真实模型发送文档、未产生真实 API 调用费用；未进行 Word/Excel 人工兼容性验收。
- 尚无 OCR、复杂 Office 保真编辑、网络写入、一般多输出批处理或签名桌面发行。

## 回滚与数据恢复

代码使用 `git revert <本阶段提交 SHA>` 生成新回滚提交，不删除历史、备份或数据库。
代码回滚不等于文档恢复。应用内 Restore 需要当前文件未变化、原件与备份完整以及全新的确认。
提交中断可能留下 `.pending` / `.original` / `.recovered` 文件，应先查看事务历史，不能盲目删除。

详见 [Office 安全模型](office-automation-model.md)、[逐函数 API](api-office-automation.md) 和
[回滚说明](rollback.md)。
