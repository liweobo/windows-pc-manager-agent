# Stage 5B 语音交互：设计、权限与真实限制

## 目标与范围

语音是已有电脑管理能力的另一种输入方式，不是另一个可以绕过安全层的 Agent。
本次保留原来的业务工具注册表、计划确认、即时确认、Fresh 身份检查、UAC 以及回滚规则。
自动测试只使用合成 PCM、假的录音/播放设备和本地模拟的供应商响应。

不实现常驻监听、唤醒词、说话人识别、声纹认证、声音克隆、全局键盘钩子、桌面自动点击、
Shell 播放器、任意音频文件解码、新的管理员能力或 Browser Stage 5C。

## 一次输入的数据流

| 阶段 | 发生什么 | 尚未获得什么权限 |
|---|---|---|
| 按住说话 | 可见用户操作、普通权限检查、先写元数据审计，再开默认设备 | 无上传/业务权限 |
| 松开 | 停止设备，只保留当前有限 PCM | 无上传/业务权限 |
| 查看外发确认 | 显示实际端点、模型、完整录音秒数/字节、成本及隐私提醒 | 只有该次上传候选 |
| 明确批准上传 | 原子消费精确、短时、单次外发确认，再调用 STT | 不是计划批准 |
| 识别完成 | 只接收当前会话的 final；UNKNOWN 可信度不冒充精确概率 | 不自动提交 |
| 编辑并提交 | 检查秘密/控制字符，SQLite 分配一个请求 UUID，清除可重放的正文引用 | 只得到 UserRequest |
| 原业务准备 | 共用文字入口，重新核对明确对象，生成自己的 Preview/计划 | 仍没有写权限 |
| 原业务确认执行 | 全部由已有模块完成并验证 | 语音不能替代其中任何确认 |

`COMPLETED` 在语音 journal 中仅表示输入已交付，不表示电脑操作已完成。
声音被识别为“确认”“yes”“我承担风险”也不会批准 R0/R1/R2/R3，更不会自动点击 UAC。
即使用户修改文字为“确认”，也不会产生业务批准。V1 没有任何语音业务确认接口。

## AudioCapture 与硬件

- PySide6 QtMultimedia，默认输入/输出设备；仅 24000 Hz、单声道、16-bit signed little-endian PCM。
- 构造和启动应用不会枚举或打开设备。明确按下 PTT 后才查询默认输入、检查格式并启动。
- 不支持格式则报 `MICROPHONE_FORMAT_UNSUPPORTED`。不猜测采样率，不临时调用 ffmpeg/Shell。
- 默认最长 60 秒，允许配置 1–120 秒，硬 PCM 大小上限 6 MiB；设备事件每次读取有界块。
- 计时或容量上限、权限/设备错误会停止硬件并丢弃不完整输入；不会自动上传截断录音。
- 只允许一个捕获 owner。取消、隐藏到托盘、应用失去前台及退出都会停止音频。
- PTT 前使用输出设备 reset，立即丢弃队列；合成结果 ID 已失效时不再播放。
- 不需要管理员权限。非 Windows 或无法可靠读取进程权限时，真实录音路径关闭。

Windows 麦克风隐私开关由用户在系统设置管理。本应用不会修改隐私设置、尝试提权或声称
“设备存在”就等于“已授权”。实际设备启动失败仍是最终拒绝依据。

## 云端与供应商

`SpeechToTextProvider` / `TextToSpeechProvider` 是可替换的异步接口。V1 内置 OpenAI，默认 disabled。
当前内置转写模型默认 `gpt-transcribe`，播报默认 `gpt-4o-mini-tts`、`coral`。
端点固定为官方 `api.openai.com/v1/audio/transcriptions` 与 `api.openai.com/v1/audio/speech`，
不会继承用户改过的 LLM base URL。请求关闭自动重试，并设有限超时。

STT 使用内存 WAV、通用文件名 `input.wav`，不传本地路径。TTS 只读有界 PCM 响应，
不使用系统临时音频文件。默认 API 超时 30 秒，可在 1–60 秒内配置。

OpenAI SDK 的 DEBUG 请求选项日志可能包含 multipart 音频。检测到 OpenAI/httpx/httpcore
调试日志开启时，语音适配器在请求前直接拒绝，不擅自修改全局日志。请关闭这些调试设置并
重启后再录音；不要为了诊断语音而记录真实音频请求。任意自定义日志回调/恶意运行时不在可信边界内。

外发批准包含目的、目标/模型/voice、内容摘要、语言提示、对象 UUID 和到期时间，
数据库原子消费后不能重复使用。更换供应商/语言/模型/内容后必须重新确认。
拒绝、不完整输入、过期、数据库错误、重复回调或取消，不会自动换供应商或重试。

注意：音频尚未转写时无法可靠检查其中的秘密。本地不保存不等于云端零保留。
不要录入密码、令牌、银行信息或其他人的私密谈话。已发送的字节无法通过“取消”撤回。
API 调用可能收费；本次开发没有调用真实付费语音 API。

官方依据（开发时核对）：[STT](https://developers.openai.com/api/docs/guides/speech-to-text)、
[TTS 与 AI 声音披露](https://developers.openai.com/api/docs/guides/text-to-speech)、
[数据控制](https://developers.openai.com/api/docs/guides/your-data)、
[Qt QAudioSource](https://doc.qt.io/qtforpython-6/PySide6/QtMultimedia/QAudioSource.html)、
[Qt QAudioSink](https://doc.qt.io/qtforpython-6/PySide6/QtMultimedia/QAudioSink.html)。

## 本地设置

“语音设置”可改本次运行的供应商、语言和播报模式，不写 API Key。
默认关闭播报；SHORT 和 NORMAL 在 V1 都只使用有限、简短的确定性模板，不朗读长对话。
持久偏好可通过环境变量设置；界面更改不会写回 shell 或配置文件。

| 环境变量 | 默认 | 含义 |
|---|---|---|
| OPENAI_API_KEY | 未设置 | 只在内存读取，不显示或保存 |
| PC_MANAGER_VOICE_PROVIDER | disabled | disabled / openai |
| PC_MANAGER_VOICE_STT_MODEL | gpt-transcribe | 转写模型 ID |
| PC_MANAGER_VOICE_TTS_MODEL | gpt-4o-mini-tts | 合成模型 ID |
| PC_MANAGER_VOICE_TTS_VOICE | coral | alloy / coral / nova / sage |
| PC_MANAGER_VOICE_LANGUAGE_HINT | auto | auto / zh / en |
| PC_MANAGER_VOICE_SPOKEN_RESPONSE_MODE | OFF | OFF / SHORT / NORMAL |
| PC_MANAGER_VOICE_MAX_VOICE_INPUT_SECONDS | 60 | 1–120 秒 |
| PC_MANAGER_VOICE_MAX_AUDIO_BYTES | 6291456 | 48000–6291456 字节，并受时长限制 |
| PC_MANAGER_VOICE_MAX_SPOKEN_RESPONSE_CHARS | 300 | 60–1000 字符 |
| PC_MANAGER_VOICE_STT_TIMEOUT | 30 | 1–60 秒 |
| PC_MANAGER_VOICE_TTS_TIMEOUT | 30 | 1–60 秒 |
| PC_MANAGER_VOICE_DISCLOSURE_TTL_SECONDS | 60 | 10–120 秒 |
| PC_MANAGER_VOICE_REVIEW_TTL_SECONDS | 300 | 10–600 秒 |

例如仅启用语音适配器（仍然不会自动录音/上传）：

```powershell
$env:PC_MANAGER_VOICE_PROVIDER = 'openai'
uv run python -m pc_manager_agent
```

## 识别安全和日志

供应商没有提供经过校准的可信度时，显示 UNKNOWN，不从语言流畅度猜测。
Partial 不可提交；所有 final 都需检查。文本限制 4000 字符，拒绝控制/隐藏字符和已知秘密模式。
文字和音频均排除于 Pydantic 的 JSON 和 repr；这是默认序列化安全，不是内存加密。

新增的 `voice.sqlite3` 只存会话 ID、进程实例、状态、版本、时间、请求 ID 和摘要，
外发确认表只存目的、标识、摘要、到期和消费状态。原审计查看器记录 `voice.state` 和
`voice.disclosure`：状态、数量、识别可信度、编辑标记、外发决定、耗时以及目标/追踪摘要。
没有正文、PCM、密码、API Key 或原始供应商错误。摘要是相关性证据，不是秘密加密或身份认证。
可在构建/启动时提供 `PC_MANAGER_GIT_COMMIT`（7–40 位十六进制提交标识）；未提供或无效时记录
未知，不猜测版本。语音数量汇总表示已验证业务事务，不代表其中的文件数或已释放空间。

语音传给旧业务规划器的是有限的规范化目标，名称/PID 仅用于该领域本地 Fresh 解析；
完整转写不会作为旧计划的 original_request 落盘。Office 原文目标停留在易失界面，
后续 Office 模型发送仍需其独立、精确的外发确认。

## 与既有领域的接入

| 目标 | 语音最多能做什么 | 后续边界 |
|---|---|---|
| 只读诊断/文件分析 | 生成原 R0 计划或进入分析页 | 目录授权、最小范围、计划确认 |
| 移动/重命名/Undo | 进入原文件页 | 手工选择确切对象、Preview、冲突/身份验证与恢复条件 |
| 回收站/清理 | 导航原工具页面；清空流程独立 | 默认不勾选，Fresh、双确认、MANUAL/NONE 如实提示 |
| 进程 | 用明确名称/PID 生成原正常退出审查 | 名称非身份，重新解析；强制终止仍独立流程 |
| 启动项 | 进入当前清单 | 用户重新明确选择，独立备份/恢复/确认 |
| 服务 | 明确查询提示交原服务准备 | 唯一服务名、配置/依赖/Fresh/确认及窄权限边界 |
| 软件卸载 | 明确名称交原只读能力/机制解析 | 不执行原卸载字符串；每个机制原有双确认与验证 |
| Office | 填入易失目标并进入文档页 | 精确文件授权、读取/模型/备份/写入/恢复各自确认 |

模糊“它”、复杂串联请求或录音后页面变化不会自动选对象。原业务处于模态审查时，
语音只允许取消/查看状态/提示视觉确认；不得用新请求替换旧计划。

## 播报与取消

播报只接受 `SpeechSummaryFacts`：有限结果枚举、数量、风险和恢复等级。不会朗读原聊天、路径、
文件名、长文档、令牌或完整异常。结果读取复用现有只读结果证据器，确认关联完整且真正验证
才可说已验证；关闭窗口、退出码、没有异常都不是成功证据。未知/旧/不支持结果保留 UNVERIFIED。
目前常规文件、进程、启动项、卸载、清理等受支持；服务、Office 和特权结果以原业务页为准。

业务“取消”绑定录音时的当前页面/确认窗口，只调用它已有的安全取消/关闭入口。
这不撤销已做的步骤、不终止外部卸载器、不停止服务进程，也不自动回滚或再次尝试。
重启中断旧录音/外发确认，不会自动恢复录音或提交任务。声音不是身份凭据。

## 发布前未验证项

自动测试不能替代真实 Windows 麦克风隐私弹窗、设备切换、驱动支持、听感与公网费用验证。
在 [手工清单](voice-manual-tests.md) 完成之前，本阶段只能称为具有模拟验证的开发版本。
需要广泛硬件支持时，应单独增加有限重采样与设备选择测试，不能静默加入通用解码器。
