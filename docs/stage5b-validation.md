# Stage 5B 验证记录

日期：2026-08-31。环境：Windows、Python 3.13.1，Qt 无界面测试模式。
这是受控语音 V1 的开发验证，不是真实音频设备认证、识别准确率评测或签名发行认证。

## 最终本地结果

| 检查 | 实际结果 |
|---|---|
| 完整非性能回归 | 1537 passed、6 skipped、10 deselected，214.14 秒；核心覆盖率 87.53%，门槛 85% |
| Stage 5B 单元、GUI、集成、安全专项 | 158 passed，23.41 秒；指定关键模块覆盖率 99.52%，门槛 95% |
| 性能回归 | 10 passed，191.17 秒；使用原有合成文件、软件记录和 Office 负载 |
| Ruff 格式 / lint | 通过；728 个 Python 文件格式检查通过 |
| mypy | 通过；463 个生产源文件 |
| Bandit | 通过 |
| pip-audit | 未发现已知依赖漏洞；本地项目未在 PyPI 发布，其自身不属于该扫描的漏洞数据库覆盖范围 |
| pre-commit | 对实际暂存文件列表执行：格式、lint、YAML、大文件、冲突标记、私钥、换行检查通过 |
| 提交内容检查 | 未暂存音频、用户数据、日志、数据库、密钥配置或构建产物；已知凭据模式命中仅为原有 Office 安全测试的合成私钥标题 |
| wheel / sdist 构建 | 通过 |
| `--smoke-test` 启动 | 通过；不打开真实麦克风、不播放声音、不调用模型 |
| API 文档一致性 | 新增语音函数/方法的名称和签名逐项验证通过；Office 公开取消入口同步更新 |
| 独立 Broker 构建及隔离 | 开发构建成功；导入图不含 GUI、OpenAI、语音供应商或音频模块；无参数运行按预期返回 20，没有 UAC |

本地完整回归针对工作区；用户已有的 `.env.example` 和旧 LLM 适配器修改保持原样，
不纳入本阶段提交。远端干净检出的验证以对应 commit 的 GitHub Actions 为准。
本阶段没有新增依赖，也没有修改 `uv.lock`。

覆盖率运行使用不同的 `COVERAGE_FILE`，以防并行测试相互覆盖结果。早期共享覆盖文件的
运行不作为以上覆盖率依据，表中结果来自独立文件的最终复跑。关键专项明确包含原生 Qt
音频适配层，不继承普通核心报告中对 GUI 的排除；完整核心覆盖率不代表整套 GUI 覆盖率。

6 项跳过：5 项符号链接/重解析测试需要本机未启用的开发权限，1 项真实回收站测试需要
显式 opt-in。未为测试提权，没有清空本机回收站，也没有执行真实卸载或服务修改。

## 语音测试边界

已用模拟设备、合成 PCM、伪供应商及 HTTPX MockTransport 验证：

- 构造界面不打开设备；仅显式可见按住操作能录音，松开/隐藏/取消/退出停止。
- 时长、字节数、格式、设备故障、权限拒绝、取消、超时和迟到回调。
- partial 不提交、final 必须复核、UNKNOWN 不伪造可信度、重复 final 只消费一次。
- 模糊目标、路径/PID/软件名/服务名不产生执行权限；页面变化使旧上下文失效。
- R0/R1/R2/R3 的口头“确认”均不能批准业务；语音上下文不能携带命令、权限或工具参数。
- 语音与文字共享请求分流，R0 扫描仍需要原来的计划确认，Office 仅进入原有准备界面。
- 外发确认绑定精确内容、目的、供应商和有效期，单次消费；数据库/审计失败停止后续动作。
- TTS 仅播报有限安全摘要；秘密、路径、代码和任意业务正文不能进入朗读输入。
- 播报停止后才可重新录音；不会自动把 TTS 作为下一条命令。取消并非 Undo。
- 供应商调试日志可能暴露请求参数时，在发送前拒绝；不擅自改变全局日志设置。
- Broker 导入隔离、无 Shell/通用执行器、无后台监听/唤醒词/声纹或语音管理员模式。

这些是程序边界测试，不证明真实噪声环境、外部扬声器、恶意同权限进程或自定义日志钩子
下不存在风险。音频上传前无法可靠判断其中是否包含秘密；本地不保存不等于云端零保留。

## 复现命令

```powershell
uv sync --all-groups --locked
uv run ruff format --check .
uv run ruff check .
uv run mypy src
$env:QT_QPA_PLATFORM = 'offscreen'
$env:COVERAGE_FILE = '.coverage.stage5b-core-final'
uv run pytest -m 'not performance' --cov-config=pyproject.toml --cov=pc_manager_agent --cov-report=term --cov-fail-under=85 -q
$env:COVERAGE_FILE = '.coverage.stage5b-voice-final'
uv run pytest tests/unit/voice tests/gui/test_voice_audio.py tests/gui/test_voice_interaction.py tests/integration/test_voice_request_flow.py tests/security/test_voice_boundaries.py --cov-config=.coveragerc-stage5b --cov=pc_manager_agent.voice --cov=pc_manager_agent.domain.voice --cov=pc_manager_agent.domain.user_requests --cov=pc_manager_agent.safety.voice --cov=pc_manager_agent.confirmation.voice_disclosure --cov=pc_manager_agent.persistence.voice --cov=pc_manager_agent.audit.voice --cov=pc_manager_agent.orchestration.user_requests --cov=pc_manager_agent.ui.voice_audio --cov=pc_manager_agent.providers.speech_logging --cov-report=term-missing --cov-fail-under=95 -q
uv run pytest tests/performance -q -s
uv run bandit -q -r src
uv run pip-audit
uv build
uv run python -m pc_manager_agent --smoke-test
```

原有 `scripts/build-privileged-broker.ps1` 构建检查也已运行。生成的未签名开发 Broker
SHA-256 为 `74a91b7866d0854ce8c70ced584cbea407b3125274820a0e4c94c31218180b6b`。
重建会改变开发二进制的信任摘要，但不会更新用户信任配置；不得把开发产物描述为生产可信发布。

## 尚未验证

- 本地未安装可直接调用的 Gitleaks；私钥 hook 和已知模式审查不能冒充完整 Gitleaks 扫描。
  远端保留 Gitleaks 门禁。CI 成功与否以该提交的运行页面为准。
- 本地只跑 Python 3.13.1；Python 3.11 交给原有 Windows CI 矩阵。
- 中文、英文/混合语音、真实 TTS 听感、真实按住说话和打断播放、Windows 麦克风权限、
  实际 R2/R3 确认窗口体验均未进行真实语音验收；只有上述模拟/程序验证。
- 未调用真实付费 STT/TTS，没有录音文件可供回放或准确率分析。
- 只支持默认设备及 24kHz 单声道 16-bit PCM；没有设备选择、重采样、离线供应商或实时会话。
- 服务、Office 和特权结果尚不进入统一的已验证语音摘要，真实状态以原业务页面为准。

详细操作见 [手工验收清单](voice-manual-tests.md)。完成真实设备验收前，不宣称生产就绪。

## 回滚

正常关闭应用，等待语音网络请求取消后，在同一项目的新分支使用 `git revert <本阶段提交 SHA>`。
保留用户配置、已有数据和恢复证据；新增语音数据库无需删除，旧版不会消费它。
代码回滚不撤回网络上传，也不自动还原已经发生的业务操作。
详见 [回滚说明](rollback.md)、[语音安全模型](voice-interaction-model.md) 与 [逐函数 API](api-voice-interaction.md)。
