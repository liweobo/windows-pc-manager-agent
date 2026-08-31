# Stage 5B 逐函数 API 文档

本文覆盖本次语音新增模块的全部命名函数/方法（包括内部方法）。签名中的类型是输入/返回契约，默认值是实现值；构造器只注入依赖。`self` 指当前对象，`None` 表示无返回数据。异步供应商方法必须 await，不能直接当作同步结果使用。

危险业务接口未由语音模块暴露。示例和自动测试只能使用合成音频；先阅读 [整体安全模型](voice-interaction-model.md)。所有 `VoiceError` 均为稳定错误代码，不能据此自动重试。

## app/voice.py

只在普通权限主应用组合依赖；不启动音频、联网或修改业务对象。Broker 不导入此模块。

### `build_voice_services`

```python
build_voice_services(directory: Path, audit: AuditRepository, settings: VoiceSettings | None=None) -> VoiceServices
```

作用：Initialize metadata-only storage and disabled-by-default adapters, without network/audio.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `audio_process_is_elevated`

```python
audio_process_is_elevated() -> bool
```

作用：Fail closed off Windows or when token information is unavailable; never request UAC.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceServices.close`

```python
VoiceServices.close(self) -> None
```

作用：Close journal handles only after capture/playback and provider workers have stopped.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceServices.reconfigure`

```python
VoiceServices.reconfigure(self, settings: VoiceSettings) -> 'VoiceServices'
```

作用：Replace adapter settings after the UI has cancelled all work; no key is persisted.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

## audit/voice.py

仅写现有审计仓库：固定代码、数量、状态、标识与摘要。不记录音频、完整转写或原始供应商异常。

### `VoiceAudit.__init__`

```python
VoiceAudit.__init__(self, repository: AuditRepository, git_commit: str | None=None) -> None
```

作用：初始化所列依赖和内部状态，或完成当前类的有限内部辅助步骤；不授予业务操作权限。

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceAudit.record`

```python
VoiceAudit.record(self, reference: UUID, state: VoiceState, *, code: str='VOICE_STATE_CHANGED', count: int=0, confidence: ConfidenceLevel=ConfidenceLevel.UNKNOWN, edited: bool=False, request_ref: UUID | None=None, duration_ms: int | None=None) -> None
```

作用：Only UUIDs, enums, counts and fixed codes enter audit, even on provider failure.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceAudit.provider_event`

```python
VoiceAudit.provider_event(self, reference: UUID, purpose: Literal['STT', 'TTS'], *, destination: str, approved: bool, completed: bool=False, count: int=0, trace: str | None=None, duration_ms: int=0) -> None
```

作用：Journal exact outbound consent and aggregate outcome without recording any payload.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

## config/voice.py

不可变配置。默认关闭供应商/播报；API 密钥只读环境变量且不参与序列化。

### `VoiceSettings.from_environment`

```python
VoiceSettings.from_environment(cls) -> 'VoiceSettings'
```

作用：Read only documented environment variables, never a project .env or saved key.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

## confirmation/voice_disclosure.py

这里只是某一次外发的批准，绝不是业务计划批准；参数改变、到期或消费过都必须拒绝。

### `VoiceDisclosureService.__init__`

```python
VoiceDisclosureService.__init__(self, store: VoiceTranscriptConsumptionStore, clock: Callable[[], float], ttl: int) -> None
```

作用：初始化所列依赖和内部状态，或完成当前类的有限内部辅助步骤；不授予业务操作权限。

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceDisclosureService.offer`

```python
VoiceDisclosureService.offer(self, owner: UUID, purpose: Literal['STT', 'TTS'], destination: str, content_digest: str, quantity: int, options: str='') -> VoiceDisclosure
```

作用：Bind content, destination/model, language/voice options and purpose to one request.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceDisclosureService.consume`

```python
VoiceDisclosureService.consume(self, value: VoiceDisclosure, approved: bool, destination: str, content_digest: str, options: str='') -> None
```

作用：Reject any changed body, model, language, provider, expiry or duplicate consent.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceDisclosureService.digest`

```python
VoiceDisclosureService.digest(purpose: str, destination: str, content: str, options: str) -> str
```

作用：Canonical digest without storing outbound data.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

## domain/voice.py

Pydantic 领域数据，禁止额外字段；音频和文字字段不出现在 JSON 或 repr 中。对象自身没有执行权限。

### `transcript_digest`

```python
transcript_digest(session_id: UUID, text: str) -> str
```

作用：Salt the correlation hash per session; it is never an authentication proof.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceError.__init__`

```python
VoiceError.__init__(self, code: str) -> None
```

作用：初始化所列依赖和内部状态，或完成当前类的有限内部辅助步骤；不授予业务操作权限。

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `CapturedAudio.require_complete_frames`

```python
CapturedAudio.require_complete_frames(self) -> CapturedAudio
```

作用：Reject truncated samples and the hard duration limit independently of byte capacity.

拒绝/异常分支：`ValueError('Invalid or oversized PCM frames')`。依赖抛出的存储/设备异常仍可向上传递；UI 只显示稳定代码。

### `CapturedAudio.duration_seconds`

```python
CapturedAudio.duration_seconds(self) -> float
```

作用：Derive duration from samples, never a provider/user claim.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `CapturedAudio.digest`

```python
CapturedAudio.digest(self) -> str
```

作用：Bind disclosure to the exact PCM content and fixed format.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `SpeechToTextResult.require_confidence_evidence`

```python
SpeechToTextResult.require_confidence_evidence(self) -> SpeechToTextResult
```

作用：Do not accept a fabricated confidence number without declared provider evidence.

拒绝/异常分支：`ValueError('Confidence must match actual provider evidence')`。依赖抛出的存储/设备异常仍可向上传递；UI 只显示稳定代码。

### `SpeechAudio.validate_pcm`

```python
SpeechAudio.validate_pcm(self) -> SpeechAudio
```

作用：Require complete fixed-format frames and at most two minutes of output.

拒绝/异常分支：`ValueError('Invalid synthesized PCM')`。依赖抛出的存储/设备异常仍可向上传递；UI 只显示稳定代码。

## orchestration/user_requests.py

文字与语音共用有限目标域分类；只选择准备/导航入口，不选择具体执行对象或降低风险。

### `diagnostic_preparation_goal`

```python
diagnostic_preparation_goal(text: str) -> str
```

作用：Preserve the finite requested collector category without journaling a voice transcript.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `UserRequestDispatcher.route`

```python
UserRequestDispatcher.route(self, request: UserRequest) -> RequestRoute
```

作用：Return a finite destination; its own policy determines execution risk.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `UserRequestDispatcher._classify`

```python
UserRequestDispatcher._classify(self, text: str) -> RequestDomain
```

作用：初始化所列依赖和内部状态，或完成当前类的有限内部辅助步骤；不授予业务操作权限。

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

## persistence/voice.py

SQLite 只存元数据，使用事务与版本比较进行单次消费；出错 fail closed，重启不恢复待执行任务。

### `VoiceTranscriptConsumptionStore.__init__`

```python
VoiceTranscriptConsumptionStore.__init__(self, path: Path, instance: UUID) -> None
```

作用：初始化所列依赖和内部状态，或完成当前类的有限内部辅助步骤；不授予业务操作权限。

拒绝/异常分支：`VoiceError('VOICE_STORAGE_UNAVAILABLE')`。依赖抛出的存储/设备异常仍可向上传递；UI 只显示稳定代码。

### `VoiceTranscriptConsumptionStore.create`

```python
VoiceTranscriptConsumptionStore.create(self, reference: UUID, now: float) -> VoiceSessionRecord
```

作用：Persist the start before any microphone is activated.

拒绝/异常分支：`VoiceError('VOICE_STORAGE_UNAVAILABLE')`。依赖抛出的存储/设备异常仍可向上传递；UI 只显示稳定代码。

### `VoiceTranscriptConsumptionStore.transition`

```python
VoiceTranscriptConsumptionStore.transition(self, previous: VoiceSessionRecord, state: VoiceState, now: float, *, digest: str | None=None, request_ref: UUID | None=None) -> VoiceSessionRecord
```

作用：CAS a session revision; a second final callback or submit cannot consume it again.

拒绝/异常分支：`VoiceError('VOICE_STORAGE_UNAVAILABLE')`；`VoiceError('VOICE_SESSION_STALE_OR_CONSUMED')`。依赖抛出的存储/设备异常仍可向上传递；UI 只显示稳定代码。

### `VoiceTranscriptConsumptionStore.offer`

```python
VoiceTranscriptConsumptionStore.offer(self, reference: UUID, owner: UUID, purpose: str, digest: str, expiry: float) -> None
```

作用：Persist a digest-only proposal; no PCM, text, destination or secret is stored.

拒绝/异常分支：`VoiceError('VOICE_STORAGE_UNAVAILABLE')`。依赖抛出的存储/设备异常仍可向上传递；UI 只显示稳定代码。

### `VoiceTranscriptConsumptionStore.consume`

```python
VoiceTranscriptConsumptionStore.consume(self, reference: UUID, owner: UUID, purpose: str, digest: str, now: float, *, approved: bool) -> None
```

作用：Atomically approve-and-consume one exact unexpired disclosure, or reject it.

拒绝/异常分支：`VoiceError('VOICE_DISCLOSURE_REJECTED')`；`VoiceError('VOICE_STORAGE_UNAVAILABLE')`；`VoiceError('VOICE_DISCLOSURE_STALE_OR_CONSUMED')`。依赖抛出的存储/设备异常仍可向上传递；UI 只显示稳定代码。

### `VoiceTranscriptConsumptionStore.recent`

```python
VoiceTranscriptConsumptionStore.recent(self, limit: int=50) -> tuple[VoiceSessionRecord, ...]
```

作用：Return bounded input history without transcript or audio bodies.

拒绝/异常分支：`VoiceError('VOICE_STORAGE_UNAVAILABLE')`。依赖抛出的存储/设备异常仍可向上传递；UI 只显示稳定代码。

### `VoiceTranscriptConsumptionStore.close`

```python
VoiceTranscriptConsumptionStore.close(self) -> None
```

作用：Release SQLite connections after voice workers finish.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

## providers/speech_to_text/base.py

可替换异步供应商适配器；只用官方固定端点、超时、无自动重试、内存音频。调用前由服务层验证外发确认。

### `SpeechToTextProvider.destination`

```python
SpeechToTextProvider.destination(self) -> str
```

作用：Identify exact provider, endpoint and model for disclosure binding.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `SpeechToTextProvider.transcribe`

```python
SpeechToTextProvider.transcribe(self, audio: CapturedAudio, language_hint: str) -> SpeechToTextResult
```

作用：Return untrusted text; never route, approve, record or retry a business operation.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

## providers/speech_to_text/openai.py

可替换异步供应商适配器；只用官方固定端点、超时、无自动重试、内存音频。调用前由服务层验证外发确认。

### `OpenAISpeechToTextProvider.__init__`

```python
OpenAISpeechToTextProvider.__init__(self, settings: VoiceSettings, client: AsyncOpenAI | None=None) -> None
```

作用：初始化所列依赖和内部状态，或完成当前类的有限内部辅助步骤；不授予业务操作权限。

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `OpenAISpeechToTextProvider.destination`

```python
OpenAISpeechToTextProvider.destination(self) -> str
```

作用：Show the official API destination instead of implicitly inheriting a custom proxy.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `OpenAISpeechToTextProvider.transcribe`

```python
OpenAISpeechToTextProvider.transcribe(self, audio: CapturedAudio, language_hint: str) -> SpeechToTextResult
```

作用：Use an in-memory WAV with a generic filename; confidence is explicitly unknown.

拒绝/异常分支：`VoiceError('VOICE_PROVIDER_CONFIGURATION_REQUIRED')`；`VoiceError('VOICE_STT_REQUEST_FAILED')`。依赖抛出的存储/设备异常仍可向上传递；UI 只显示稳定代码。

## providers/text_to_speech/base.py

可替换异步供应商适配器；只用官方固定端点、超时、无自动重试、内存音频。调用前由服务层验证外发确认。

### `TextToSpeechProvider.destination`

```python
TextToSpeechProvider.destination(self) -> str
```

作用：Bind provider, endpoint, model and voice options in disclosure.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `TextToSpeechProvider.synthesize`

```python
TextToSpeechProvider.synthesize(self, text: str) -> SpeechAudio
```

作用：Return bounded PCM; do not play it or change the business verification result.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

## providers/text_to_speech/openai.py

可替换异步供应商适配器；只用官方固定端点、超时、无自动重试、内存音频。调用前由服务层验证外发确认。

### `OpenAITextToSpeechProvider.__init__`

```python
OpenAITextToSpeechProvider.__init__(self, settings: VoiceSettings, client: AsyncOpenAI | None=None) -> None
```

作用：初始化所列依赖和内部状态，或完成当前类的有限内部辅助步骤；不授予业务操作权限。

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `OpenAITextToSpeechProvider.destination`

```python
OpenAITextToSpeechProvider.destination(self) -> str
```

作用：Bind the voice together with endpoint/model in the exact disclosure.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `OpenAITextToSpeechProvider.synthesize`

```python
OpenAITextToSpeechProvider.synthesize(self, text: str) -> SpeechAudio
```

作用：Read PCM in small chunks, closing the response on limit, timeout or cancellation.

拒绝/异常分支：`VoiceError('VOICE_SPEECH_LENGTH_LIMIT')`；`VoiceError('VOICE_PROVIDER_CONFIGURATION_REQUIRED')`；`VoiceError('VOICE_TTS_REQUEST_FAILED')`；`VoiceError('VOICE_SPEECH_AUDIO_LIMIT')`。依赖抛出的存储/设备异常仍可向上传递；UI 只显示稳定代码。

## safety/voice.py

纯确定性规则：语音不能批准任何业务风险级别。秘密检测是保守模式匹配，不是完整数据防泄漏系统。

### `SensitiveTranscriptRedactor.contains_sensitive`

```python
SensitiveTranscriptRedactor.contains_sensitive(self, text: str) -> bool
```

作用：Check normalized text before routing or speech; never inspect credentials on disk.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `SensitiveTranscriptRedactor.redact`

```python
SensitiveTranscriptRedactor.redact(self, text: str) -> str
```

作用：Drop the entire sensitive utterance instead of guessing where a secret ends.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `TranscriptConfidencePolicy.assess`

```python
TranscriptConfidencePolicy.assess(self, result: SpeechToTextResult) -> ConfidenceLevel
```

作用：Return UNKNOWN without evidence; reject partial, empty, oversized or control text.

拒绝/异常分支：`VoiceError('VOICE_PARTIAL_TRANSCRIPT_NOT_ROUTABLE')`。依赖抛出的存储/设备异常仍可向上传递；UI 只显示稳定代码。

### `TranscriptConfidencePolicy.validate_text`

```python
TranscriptConfidencePolicy.validate_text(self, text: str) -> None
```

作用：Reject unsafe control characters and obvious secrets before creating a request.

拒绝/异常分支：`VoiceError('VOICE_TRANSCRIPT_INVALID')`；`VoiceError('VOICE_TRANSCRIPT_CONTROL_CHARACTERS')`；`VoiceError('VOICE_SENSITIVE_TRANSCRIPT_BLOCKED')`。依赖抛出的存储/设备异常仍可向上传递；UI 只显示稳定代码。

### `VoiceConfirmationPolicy.require_visual`

```python
VoiceConfirmationPolicy.require_visual(self, risk: RiskLevel) -> None
```

作用：Always deny spoken approval with the domain-supplied risk, never infer its risk.

拒绝/异常分支：`VoiceError(f'VOICE_CONFIRMATION_NOT_ALLOWED_FOR_{risk.value}')`。依赖抛出的存储/设备异常仍可向上传递；UI 只显示稳定代码。

### `SafeSpeechOutputPolicy.check`

```python
SafeSpeechOutputPolicy.check(self, text: str, maximum_chars: int) -> SpeechDecision
```

作用：Reject secrets, paths, long opaque values, code and any oversized output.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

## ui/voice_audio.py

Qt 主线程只管理设备/显示/交互，异步供应商在私有工作池运行；界面不会直接执行业务工具。

### `internal_format`

```python
internal_format() -> QAudioFormat
```

作用：Describe the sole tested V1 format; unsupported devices fail explicitly, never guess.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `QtAudioCapture.__init__`

```python
QtAudioCapture.__init__(self, settings: VoiceSettings, parent: QObject | None=None) -> None
```

作用：初始化所列依赖和内部状态，或完成当前类的有限内部辅助步骤；不授予业务操作权限。

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `QtAudioCapture.start`

```python
QtAudioCapture.start(self) -> None
```

作用：Open only the current default input; absence/permission/format errors are terminal.

拒绝/异常分支：`VoiceError('VOICE_INPUT_ALREADY_ACTIVE')`；`VoiceError('MICROPHONE_UNAVAILABLE')`；`VoiceError('MICROPHONE_FORMAT_UNSUPPORTED')`；`VoiceError('MICROPHONE_PERMISSION_OR_DEVICE_ERROR')`。依赖抛出的存储/设备异常仍可向上传递；UI 只显示稳定代码。

### `QtAudioCapture.stop`

```python
QtAudioCapture.stop(self) -> CapturedAudio
```

作用：Stop the device first and transfer the bounded complete samples, never a disk file.

拒绝/异常分支：`VoiceError('VOICE_NOT_RECORDING')`。依赖抛出的存储/设备异常仍可向上传递；UI 只显示稳定代码。

### `QtAudioCapture.discard`

```python
QtAudioCapture.discard(self) -> None
```

作用：Stop hardware regardless of journal availability, then release pending audio.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `QtAudioCapture._stop_device`

```python
QtAudioCapture._stop_device(self) -> None
```

作用：初始化所列依赖和内部状态，或完成当前类的有限内部辅助步骤；不授予业务操作权限。

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `QtAudioCapture._read`

```python
QtAudioCapture._read(self) -> None
```

作用：初始化所列依赖和内部状态，或完成当前类的有限内部辅助步骤；不授予业务操作权限。

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `QtAudioCapture._limit`

```python
QtAudioCapture._limit(self) -> None
```

作用：初始化所列依赖和内部状态，或完成当前类的有限内部辅助步骤；不授予业务操作权限。

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `QtAudioCapture._state_changed`

```python
QtAudioCapture._state_changed(self, state: QtAudio.State) -> None
```

作用：初始化所列依赖和内部状态，或完成当前类的有限内部辅助步骤；不授予业务操作权限。

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `QtAudioPlayback.__init__`

```python
QtAudioPlayback.__init__(self, parent: QObject | None=None) -> None
```

作用：初始化所列依赖和内部状态，或完成当前类的有限内部辅助步骤；不授予业务操作权限。

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `QtAudioPlayback.play`

```python
QtAudioPlayback.play(self, audio: SpeechAudio) -> None
```

作用：Play already-consented bounded synthetic speech only, with no file/URL decoder.

拒绝/异常分支：`VoiceError('VOICE_OUTPUT_DEVICE_OR_FORMAT_UNAVAILABLE')`。依赖抛出的存储/设备异常仍可向上传递；UI 只显示稳定代码。

### `QtAudioPlayback.stop`

```python
QtAudioPlayback.stop(self) -> None
```

作用：Immediately flush native queued audio and release the in-memory PCM copy.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `QtAudioPlayback._state_changed`

```python
QtAudioPlayback._state_changed(self, state: QtAudio.State) -> None
```

作用：初始化所列依赖和内部状态，或完成当前类的有限内部辅助步骤；不授予业务操作权限。

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

## ui/voice_controller.py

Qt 主线程只管理设备/显示/交互，异步供应商在私有工作池运行；界面不会直接执行业务工具。

### `AudioPlayback.play`

```python
AudioPlayback.play(self, audio: SpeechAudio) -> None
```

作用：Start bounded generated PCM.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `AudioPlayback.stop`

```python
AudioPlayback.stop(self) -> None
```

作用：Flush queued speech immediately.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceJob.__init__`

```python
VoiceJob.__init__(self, work: Callable[[], Coroutine[object, object, object]]) -> None
```

作用：初始化所列依赖和内部状态，或完成当前类的有限内部辅助步骤；不授予业务操作权限。

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceJob.run`

```python
VoiceJob.run(self) -> None
```

作用：Bounded provider service owns deadline/cleanup; late UI results are discarded by ID.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceUiController.__init__`

```python
VoiceUiController.__init__(self, services: VoiceServices, capture: AudioCaptureService, playback: AudioPlayback, permission: MicrophonePermissionService, parent: QObject | None=None) -> None
```

作用：初始化所列依赖和内部状态，或完成当前类的有限内部辅助步骤；不授予业务操作权限。

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceUiController.press`

```python
VoiceUiController.press(self, context: VoiceInteractionContext, *, visible: bool) -> None
```

作用：Flush output, discard old input, and require a fresh visible activation.

拒绝/异常分支：`VoiceError('VOICE_INPUT_ALREADY_ACTIVE')`；`VoiceError('VOICE_WAIT_FOR_PROVIDER_CANCELLATION')`。依赖抛出的存储/设备异常仍可向上传递；UI 只显示稳定代码。

### `VoiceUiController.release`

```python
VoiceUiController.release(self) -> None
```

作用：Finish input only; upload is a separate explicit confirmation.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceUiController.disclosure`

```python
VoiceUiController.disclosure(self) -> VoiceDisclosure
```

作用：Prepare exact STT disclosure from the stopped current recording.

拒绝/异常分支：`VoiceError('VOICE_AUDIO_UNAVAILABLE')`。依赖抛出的存储/设备异常仍可向上传递；UI 只显示稳定代码。

### `VoiceUiController.configure`

```python
VoiceUiController.configure(self, settings: VoiceSettings) -> None
```

作用：Apply volatile provider/language/output preferences only while all work is stopped.

拒绝/异常分支：`VoiceError('VOICE_WAIT_FOR_PROVIDER_CANCELLATION')`。依赖抛出的存储/设备异常仍可向上传递；UI 只显示稳定代码。

### `VoiceUiController.capture_progress`

```python
VoiceUiController.capture_progress(self, milliseconds: int) -> None
```

作用：Display captured duration without sampling or retaining additional audio.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceUiController.transcribe`

```python
VoiceUiController.transcribe(self, disclosure: VoiceDisclosure, approved: bool) -> None
```

作用：Dispatch one exact independently confirmed upload, without blocking Qt.

拒绝/异常分支：`VoiceError('VOICE_PROVIDER_ALREADY_ACTIVE')`。依赖抛出的存储/设备异常仍可向上传递；UI 只显示稳定代码。

### `VoiceUiController.submit`

```python
VoiceUiController.submit(self, text: str) -> None
```

作用：Consume the reviewed recording once before handing off a normal UserRequest.

拒绝/异常分支：`VoiceError('VOICE_TRANSCRIPT_UNAVAILABLE')`。依赖抛出的存储/设备异常仍可向上传递；UI 只显示稳定代码。

### `VoiceUiController.speech_proposal`

```python
VoiceUiController.speech_proposal(self, facts: SpeechSummaryFacts) -> SpeechProposal
```

作用：Build a finite factual summary for a separate visible TTS upload preview.

拒绝/异常分支：`VoiceError('VOICE_INPUT_ACTIVE')`。依赖抛出的存储/设备异常仍可向上传递；UI 只显示稳定代码。

### `VoiceUiController.speak`

```python
VoiceUiController.speak(self, proposal: SpeechProposal, approved: bool) -> None
```

作用：Synthesize only the exact confirmed proposal; never replay automatically.

拒绝/异常分支：`VoiceError('VOICE_PROVIDER_ALREADY_ACTIVE')`。依赖抛出的存储/设备异常仍可向上传递；UI 只显示稳定代码。

### `VoiceUiController.stop_speech`

```python
VoiceUiController.stop_speech(self) -> None
```

作用：Invalidate queued TTS, cancel network work and flush hardware before new input.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceUiController.cancel`

```python
VoiceUiController.cancel(self) -> None
```

作用：Stop only voice input/output; cancellation of a business task is separately bound.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceUiController.hardware_error`

```python
VoiceUiController.hardware_error(self, code: str) -> None
```

作用：Stop all audio before reporting a native capture/playback failure.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceUiController.playback_finished`

```python
VoiceUiController.playback_finished(self) -> None
```

作用：Acknowledge output completion, never business completion.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceUiController.error`

```python
VoiceUiController.error(self, exc: Exception) -> None
```

作用：Present stable codes with actionable local help, not provider bodies or secrets.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceUiController.shutdown`

```python
VoiceUiController.shutdown(self) -> bool
```

作用：Stop hardware immediately; defer database shutdown while network cleanup is pending.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceUiController._start_job`

```python
VoiceUiController._start_job(self, job: VoiceJob) -> None
```

作用：初始化所列依赖和内部状态，或完成当前类的有限内部辅助步骤；不授予业务操作权限。

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceUiController._completed`

```python
VoiceUiController._completed(self, reference: UUID, value: object, code: str) -> None
```

作用：初始化所列依赖和内部状态，或完成当前类的有限内部辅助步骤；不授予业务操作权限。

拒绝/异常分支：`VoiceError(code)`；`VoiceError('VOICE_PROVIDER_RESULT_INVALID')`。依赖抛出的存储/设备异常仍可向上传递；UI 只显示稳定代码。

## ui/voice_controls.py

Qt 主线程只管理设备/显示/交互，异步供应商在私有工作池运行；界面不会直接执行业务工具。

### `VoiceControls.__init__`

```python
VoiceControls.__init__(self, controller: VoiceUiController, context: Callable[[], VoiceInteractionContext], parent: QWidget | None=None, summary: Callable[[], SpeechSummaryFacts] | None=None) -> None
```

作用：初始化所列依赖和内部状态，或完成当前类的有限内部辅助步骤；不授予业务操作权限。

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceControls.refresh`

```python
VoiceControls.refresh(self) -> None
```

作用：Reflect shared state without overwriting a user's ongoing review edits.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceControls._press`

```python
VoiceControls._press(self) -> None
```

作用：初始化所列依赖和内部状态，或完成当前类的有限内部辅助步骤；不授予业务操作权限。

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceControls._release`

```python
VoiceControls._release(self) -> None
```

作用：初始化所列依赖和内部状态，或完成当前类的有限内部辅助步骤；不授予业务操作权限。

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceControls._upload`

```python
VoiceControls._upload(self) -> None
```

作用：初始化所列依赖和内部状态，或完成当前类的有限内部辅助步骤；不授予业务操作权限。

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceControls._speak`

```python
VoiceControls._speak(self) -> None
```

作用：初始化所列依赖和内部状态，或完成当前类的有限内部辅助步骤；不授予业务操作权限。

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceControls._ask`

```python
VoiceControls._ask(self, title: str, text: str) -> bool
```

作用：初始化所列依赖和内部状态，或完成当前类的有限内部辅助步骤；不授予业务操作权限。

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceControls.hideEvent`

```python
VoiceControls.hideEvent(self, event: QHideEvent) -> None
```

作用：A disappearing capture control cannot leave the microphone live behind a tray.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceControls._settings`

```python
VoiceControls._settings(self) -> None
```

作用：初始化所列依赖和内部状态，或完成当前类的有限内部辅助步骤；不授予业务操作权限。

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

## voice/audio.py

无 UI、Windows writer、业务确认器或工具注册表依赖。只协调受限输入、输出和只读事实。

### `wav_bytes`

```python
wav_bytes(audio: CapturedAudio) -> bytes
```

作用：Encode fixed PCM as a bounded WAV in memory, never a public temporary file.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `MicrophonePermissionService.__init__`

```python
MicrophonePermissionService.__init__(self, is_elevated: Callable[[], bool]) -> None
```

作用：初始化所列依赖和内部状态，或完成当前类的有限内部辅助步骤；不授予业务操作权限。

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `MicrophonePermissionService.require_activation`

```python
MicrophonePermissionService.require_activation(self, *, user_gesture: bool, visible: bool, status: MicrophoneStatus) -> None
```

作用：No automatic probe, retry, privilege escalation or model-driven recording.

拒绝/异常分支：`VoiceError('VOICE_EXPLICIT_VISIBLE_ACTIVATION_REQUIRED')`；`VoiceError('VOICE_ELEVATED_AUDIO_BLOCKED')`；`VoiceError('MICROPHONE_PERMISSION_DENIED')`；`VoiceError('MICROPHONE_UNAVAILABLE')`。依赖抛出的存储/设备异常仍可向上传递；UI 只显示稳定代码。

### `AudioCaptureService.start`

```python
AudioCaptureService.start(self) -> None
```

作用：Open the explicitly selected device after activation gates.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `AudioCaptureService.stop`

```python
AudioCaptureService.stop(self) -> CapturedAudio
```

作用：Stop hardware and return the complete bounded recording.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `AudioCaptureService.discard`

```python
AudioCaptureService.discard(self) -> None
```

作用：Stop hardware and drop all pending samples, even after an error.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `BoundedAudioBuffer.__init__`

```python
BoundedAudioBuffer.__init__(self, settings: VoiceSettings) -> None
```

作用：初始化所列依赖和内部状态，或完成当前类的有限内部辅助步骤；不授予业务操作权限。

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `BoundedAudioBuffer.append`

```python
BoundedAudioBuffer.append(self, samples: bytes) -> None
```

作用：Fail without retaining an over-limit block; the caller must stop the device.

拒绝/异常分支：`VoiceError('VOICE_INPUT_LIMIT_REACHED')`。依赖抛出的存储/设备异常仍可向上传递；UI 只显示稳定代码。

### `BoundedAudioBuffer.size`

```python
BoundedAudioBuffer.size(self) -> int
```

作用：Return captured bytes for visible progress only.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `BoundedAudioBuffer.finish`

```python
BoundedAudioBuffer.finish(self) -> CapturedAudio
```

作用：Transfer complete samples then drop the mutable recording buffer.

拒绝/异常分支：`VoiceError('VOICE_EMPTY_OR_TRUNCATED_AUDIO')`。依赖抛出的存储/设备异常仍可向上传递；UI 只显示稳定代码。

### `BoundedAudioBuffer.clear`

```python
BoundedAudioBuffer.clear(self) -> None
```

作用：Overwrite this mutable copy then release it; Python/OS-wide erasure is not promised.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

## voice/providers.py

无 UI、Windows writer、业务确认器或工具注册表依赖。只协调受限输入、输出和只读事实。

### `bounded_provider_call`

```python
bounded_provider_call(work: Coroutine[object, object, T], timeout: float, cancelled: Event) -> T
```

作用：Cancel network work on deadline/user stop, await cleanup, and never retry business work.

拒绝/异常分支：`VoiceError('VOICE_PROVIDER_CANCELLED')`；`VoiceError('VOICE_PROVIDER_TIMEOUT')`。依赖抛出的存储/设备异常仍可向上传递；UI 只显示稳定代码。

### `VoiceProviderService.__init__`

```python
VoiceProviderService.__init__(self, coordinator: VoiceSessionCoordinator, stt: SpeechToTextProvider | None, tts: TextToSpeechProvider | None) -> None
```

作用：初始化所列依赖和内部状态，或完成当前类的有限内部辅助步骤；不授予业务操作权限。

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceProviderService.configured`

```python
VoiceProviderService.configured(self) -> bool
```

作用：Whether a transcription adapter has been explicitly configured.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceProviderService.prepare_stt`

```python
VoiceProviderService.prepare_stt(self, reference: UUID) -> VoiceDisclosure
```

作用：Describe exact recorded bytes, selected language and actual destination before upload.

拒绝/异常分支：`VoiceError('VOICE_PROVIDER_CONFIGURATION_REQUIRED')`。依赖抛出的存储/设备异常仍可向上传递；UI 只显示稳定代码。

### `VoiceProviderService.transcribe`

```python
VoiceProviderService.transcribe(self, disclosure: VoiceDisclosure, approved: bool, cancelled: Event) -> VoiceTranscript
```

作用：Consume upload consent, dispatch once, discard audio and require final review.

拒绝/异常分支：`VoiceError('VOICE_STT_UNAVAILABLE_OR_CANCELLED')`；`VoiceError(exc.code if isinstance(exc, VoiceError) else 'VOICE_STT_REQUEST_FAILED')`。依赖抛出的存储/设备异常仍可向上传递；UI 只显示稳定代码。

### `VoiceProviderService.prepare_speech`

```python
VoiceProviderService.prepare_speech(self, facts: SpeechSummaryFacts) -> SpeechProposal
```

作用：Accept only finite facts, never an arbitrary text string supplied by an LLM or UI.

拒绝/异常分支：`VoiceError('VOICE_TTS_DISABLED')`。依赖抛出的存储/设备异常仍可向上传递；UI 只显示稳定代码。

### `VoiceProviderService.synthesize`

```python
VoiceProviderService.synthesize(self, proposal: SpeechProposal, approved: bool, cancelled: Event) -> SpeechAudio
```

作用：Consume one exact safe summary; cancellation invalidates late playback results.

拒绝/异常分支：`VoiceError('VOICE_TTS_STALE_OR_CANCELLED')`；`VoiceError(exc.code if isinstance(exc, VoiceError) else 'VOICE_TTS_REQUEST_FAILED')`。依赖抛出的存储/设备异常仍可向上传递；UI 只显示稳定代码。

### `VoiceProviderService.stop_speech`

```python
VoiceProviderService.stop_speech(self) -> None
```

作用：Invalidate a pending synthesis; callers also stop hardware and cancel provider work.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

## voice/push_to_talk.py

无 UI、Windows writer、业务确认器或工具注册表依赖。只协调受限输入、输出和只读事实。

### `PushToTalkController.__init__`

```python
PushToTalkController.__init__(self, capture: AudioCaptureService, permission: MicrophonePermissionService, coordinator: VoiceSessionCoordinator) -> None
```

作用：初始化所列依赖和内部状态，或完成当前类的有限内部辅助步骤；不授予业务操作权限。

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `PushToTalkController.active`

```python
PushToTalkController.active(self) -> bool
```

作用：Return whether this controller owns a live device activation.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `PushToTalkController.press`

```python
PushToTalkController.press(self, context: VoiceInteractionContext, *, user_gesture: bool, visible: bool, status: MicrophoneStatus=MicrophoneStatus.UNKNOWN) -> UUID
```

作用：Validate explicit activation, journal, then open the device once.

拒绝/异常分支：`VoiceError('VOICE_INPUT_ALREADY_ACTIVE')`。依赖抛出的存储/设备异常仍可向上传递；UI 只显示稳定代码。

### `PushToTalkController.release`

```python
PushToTalkController.release(self) -> UUID
```

作用：Close the microphone before retaining the recording for an upload preview.

拒绝/异常分支：`VoiceError('VOICE_NOT_RECORDING')`。依赖抛出的存储/设备异常仍可向上传递；UI 只显示稳定代码。

### `PushToTalkController.cancel`

```python
PushToTalkController.cancel(self) -> None
```

作用：Stop hardware first; persistence failure cannot leave the microphone running.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

## voice/results.py

无 UI、Windows writer、业务确认器或工具注册表依赖。只协调受限输入、输出和只读事实。

### `VoiceResultSummaryService.__init__`

```python
VoiceResultSummaryService.__init__(self, read: Callable[[OptimizationTransactionReference], DomainReceiptSnapshot], started_at: datetime) -> None
```

作用：初始化所列依赖和内部状态，或完成当前类的有限内部辅助步骤；不授予业务操作权限。

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceResultSummaryService.summarize`

```python
VoiceResultSummaryService.summarize(self, reference: OptimizationTransactionReference) -> SpeechSummaryFacts
```

作用：Read one fresh bound receipt; absent/old/contradictory evidence remains unverified.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

## voice/routing.py

无 UI、Windows writer、业务确认器或工具注册表依赖。只协调受限输入、输出和只读事实。

### `VoiceIntentRouter.__init__`

```python
VoiceIntentRouter.__init__(self, coordinator: VoiceSessionCoordinator, dispatcher: UserRequestDispatcher) -> None
```

作用：初始化所列依赖和内部状态，或完成当前类的有限内部辅助步骤；不授予业务操作权限。

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceIntentRouter.submit`

```python
VoiceIntentRouter.submit(self, reference: UUID, text: str) -> tuple[UserRequest, RequestRoute]
```

作用：Prepare one channel-neutral handoff; domain selection/confirmation still lies ahead.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

## voice/session.py

无 UI、Windows writer、业务确认器或工具注册表依赖。只协调受限输入、输出和只读事实。

### `VoiceSessionCoordinator.__init__`

```python
VoiceSessionCoordinator.__init__(self, store: VoiceTranscriptConsumptionStore, audit: VoiceAudit, settings: VoiceSettings, clock: Callable[[], float]=time.time) -> None
```

作用：初始化所列依赖和内部状态，或完成当前类的有限内部辅助步骤；不授予业务操作权限。

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceSessionCoordinator.state`

```python
VoiceSessionCoordinator.state(self) -> VoiceState
```

作用：Report input state; speaking belongs to the independent playback controller.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceSessionCoordinator.reference`

```python
VoiceSessionCoordinator.reference(self) -> UUID | None
```

作用：Return the active input ID, never a business authorization ID.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceSessionCoordinator.transcript`

```python
VoiceSessionCoordinator.transcript(self) -> VoiceTranscript | None
```

作用：Expose volatile text only to the local review UI.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceSessionCoordinator.begin`

```python
VoiceSessionCoordinator.begin(self, context: VoiceInteractionContext) -> UUID
```

作用：Journal one new input before the PTT controller opens its microphone.

拒绝/异常分支：`VoiceError('VOICE_INPUT_ALREADY_ACTIVE')`。依赖抛出的存储/设备异常仍可向上传递；UI 只显示稳定代码。

### `VoiceSessionCoordinator.listening`

```python
VoiceSessionCoordinator.listening(self, reference: UUID) -> None
```

作用：Acknowledge actual hardware start only for the current prepared input.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceSessionCoordinator.finish_capture`

```python
VoiceSessionCoordinator.finish_capture(self, reference: UUID, audio: CapturedAudio) -> None
```

作用：Keep only complete bounded audio, awaiting separate external upload consent.

拒绝/异常分支：`VoiceError('VOICE_INPUT_LIMIT_REACHED')`。依赖抛出的存储/设备异常仍可向上传递；UI 只显示稳定代码。

### `VoiceSessionCoordinator.audio_for_disclosure`

```python
VoiceSessionCoordinator.audio_for_disclosure(self, reference: UUID) -> CapturedAudio
```

作用：Return only the current undispatched recording for its exact disclosure preview.

拒绝/异常分支：`VoiceError('VOICE_AUDIO_UNAVAILABLE')`。依赖抛出的存储/设备异常仍可向上传递；UI 只显示稳定代码。

### `VoiceSessionCoordinator.start_transcription`

```python
VoiceSessionCoordinator.start_transcription(self, reference: UUID) -> None
```

作用：Mark dispatch after consent consumption, before the provider is contacted.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceSessionCoordinator.accept_final`

```python
VoiceSessionCoordinator.accept_final(self, reference: UUID, result: SpeechToTextResult) -> VoiceTranscript
```

作用：Reject stale/duplicate/partial output; all final text still requires human review.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceSessionCoordinator.consume_final`

```python
VoiceSessionCoordinator.consume_final(self, reference: UUID, reviewed_text: str) -> UserRequest
```

作用：Allocate one normal request atomically; edited text cannot reuse a consumed recording.

拒绝/异常分支：`VoiceError('VOICE_TRANSCRIPT_UNAVAILABLE')`；`VoiceError('VOICE_TRANSCRIPT_EXPIRED')`。依赖抛出的存储/设备异常仍可向上传递；UI 只显示稳定代码。

### `VoiceSessionCoordinator.routed`

```python
VoiceSessionCoordinator.routed(self, reference: UUID) -> None
```

作用：Finish INPUT delivery only; never report that the requested business action completed.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceSessionCoordinator.cancel`

```python
VoiceSessionCoordinator.cancel(self) -> None
```

作用：Discard volatile input; the caller must stop audio hardware before this journal call.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceSessionCoordinator.fail`

```python
VoiceSessionCoordinator.fail(self, reference: UUID, code: str) -> None
```

作用：Drop input after error; late callbacks cannot change a newer recording.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceSessionCoordinator.drop_audio`

```python
VoiceSessionCoordinator.drop_audio(self, reference: UUID) -> None
```

作用：Release recording references after outbound success, failure or cancellation.

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

### `VoiceSessionCoordinator._require`

```python
VoiceSessionCoordinator._require(self, reference: UUID, state: VoiceState) -> VoiceSessionRecord
```

作用：初始化所列依赖和内部状态，或完成当前类的有限内部辅助步骤；不授予业务操作权限。

拒绝/异常分支：`VoiceError('VOICE_SESSION_STALE_OR_CONSUMED')`。依赖抛出的存储/设备异常仍可向上传递；UI 只显示稳定代码。

### `VoiceSessionCoordinator._change`

```python
VoiceSessionCoordinator._change(self, reference: UUID, expected: VoiceState, state: VoiceState, *, digest: str | None=None) -> None
```

作用：初始化所列依赖和内部状态，或完成当前类的有限内部辅助步骤；不授予业务操作权限。

异常约定：遵循本模块的边界；依赖失败由调用链转换或传播。没有列出本地 raise 不表示不会失败。

## voice/speech.py

无 UI、Windows writer、业务确认器或工具注册表依赖。只协调受限输入、输出和只读事实。

### `SpeechSummaryBuilder.build`

```python
SpeechSummaryBuilder.build(self, facts: SpeechSummaryFacts, maximum_chars: int=300) -> str
```

作用：Render a short allow-listed statement; long details remain on screen.

拒绝/异常分支：`VoiceError('VOICE_SPEECH_POLICY_BLOCKED')`。依赖抛出的存储/设备异常仍可向上传递；UI 只显示稳定代码。

## 已有模块的新增/修改入口

| 方法 | 参数与返回 | 作用与边界 |
|---|---|---|
| MainWindow._handle_chat | 无 → None | 构造 TEXT UserRequest，通过共享分流器，不直接运行工具。 |
| MainWindow._route_request | UserRequest, RequestRoute → None | 再次验证有限路由；阻止语音确认、旧页面和模态审查替换；明确导航。 |
| MainWindow._dispatch_domain | UserRequest, RequestRoute → None | 进入原领域准备函数；语音使用固定审计目标，原文仅用于本地意图/名称解析。 |
| MainWindow._build_voice | 无 → None | 主应用构造唯一 VoiceServices/设备控制器；初始化失败则停用语音。 |
| MainWindow._voice_application_state | Qt.ApplicationState → None | 失去前台时停止语音，不等待数据库再停设备。 |
| MainWindow._voice_surface_changed | 页签序号 → None | 换页生成新上下文标识，使旧录音上下文失效。 |
| MainWindow._voice_context | 无 → VoiceInteractionContext | 提供页面与本地会话提示，不携带权限或目标句柄。 |
| MainWindow.eventFilter | QObject, QEvent → bool | 在既有审查对话框显示时加入共享语音控件；不增建录音会话。 |
| MainWindow._owns_voice_dialog | QDialog → bool | 沿 QObject 父链核对归属，适用于独立顶层确认窗口。 |
| MainWindow._voice_tab_preview | object → None | 只接收类型正确的业务 Preview 引用，不能接收声称成功的文字。 |
| MainWindow._voice_bind_receipt | UUID, object → None | 将现有业务事务引用关联到一个本地界面上下文。 |
| MainWindow._voice_summary | UUID → SpeechSummaryFacts | 只读业务结果投影；未关联或证据不足返回 UNVERIFIED。 |
| MainWindow._cancel_current_surface | 无 → None | 仅调用当前页既有取消入口，不跨任务取消，不调用 Undo。 |
| MainWindow.hideEvent | QHideEvent → None | 隐藏托盘前取消语音并调用父类行为。 |
| MainWindow.shutdown | 无 → bool | 先停止音频并等待短时网络清理；尚有语音工作时拒绝立即关闭数据库。 |
| OfficeTab.cancel_current_work | 无 → None | 暴露已有当前办公任务取消入口，不增加写入或恢复能力。 |

## 持久化与兼容性

### `VoiceUiController._finish_playback_audit`

```python
VoiceUiController._finish_playback_audit(self, code: str) -> None
```

作用：设备已停止后，用当前播报请求 UUID 记录完成或中断元数据，随即丢弃引用。
即使审计不可用，也必须继续停止录音和清理语音任务；错误只显示安全代码，不输出音频或正文。
播报前另外记录 `VOICE_PLAYBACK_REQUESTED`，供应商失败记录固定错误代码；这些事件不代表业务成功。

### providers/speech_logging.py

### `require_private_speech_logging`

```python
require_private_speech_logging() -> None
```

作用：每次 STT/TTS 请求前只读检查 OpenAI/HTTP 调试日志是否启用。SDK 的 DEBUG 请求参数可能
包含 multipart 音频，因此这种配置下直接拒绝语音请求，抛出 `VOICE_VERBOSE_PROVIDER_LOGGING_BLOCKED`。
不关闭或修改其他模块的日志设置，不生成 WAV、上传内容或尝试自动重试。

### `optimization_preparation_goal`

```python
optimization_preparation_goal(text: str) -> str
```

作用：从输入中保留空间、后台、响应、开机或电脑变慢等有限分析类别，生成固定规划目标。
没有明确类别时采用快速检查，不把完整语音正文交给旧的审计规划器，也不自动选择全面扫描。
返回值只是新的 R0 计划准备目标；尚未获得扫描/写权限。

### domain/user_requests.py：声明式模型

本模块没有手写函数；构造和 JSON Schema 校验方法由 Pydantic 提供。
`UserRequest` 包含请求 UUID、来源 TEXT/VOICE/VOICE_EDITED、仅内存正文和上下文。
`VoiceInteractionContext` 仅含页面类型、明确选择的 UUID 提示及会话/事务引用，不含权限。
`RequestRoute` 只有请求 ID、有限目标域和原因代码；`RequestDomain` 为完整导航枚举，
`RequestChannel` 记录输入来源而不是信任等级。额外字段（如 approved/admin/command）一律拒绝。

语音 journal 为新增的 voice.sqlite3，业务表不迁移。状态迁移/消费需要同一个应用 instance 和版本；body 永不进入表结构。适配器接口替换不能新增工具或修改业务确认语义。详情见 [开发指南](developer-guide.md) 和 [回滚说明](rollback.md)。
