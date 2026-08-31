from __future__ import annotations

import asyncio
import io
import wave
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from pydantic import ValidationError

from pc_manager_agent.config.voice import VoiceSettings
from pc_manager_agent.confirmation.voice_disclosure import VoiceDisclosureService
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.user_requests import (
    RequestChannel,
    RequestDomain,
    UserRequest,
    VoiceInteractionContext,
)
from pc_manager_agent.domain.voice import (
    CapturedAudio,
    ConfidenceLevel,
    SpeechAudio,
    SpeechToTextResult,
    VoiceError,
    VoiceState,
)
from pc_manager_agent.orchestration.user_requests import UserRequestDispatcher
from pc_manager_agent.persistence.voice import VoiceTranscriptConsumptionStore
from pc_manager_agent.safety.voice import (
    SafeSpeechOutputPolicy,
    SensitiveTranscriptRedactor,
    SpeechDecision,
    TranscriptConfidencePolicy,
    VoiceConfirmationPolicy,
)
from pc_manager_agent.voice.audio import (
    BoundedAudioBuffer,
    MicrophonePermissionService,
    MicrophoneStatus,
    wav_bytes,
)
from pc_manager_agent.voice.providers import VoiceProviderService, bounded_provider_call
from pc_manager_agent.voice.push_to_talk import PushToTalkController
from pc_manager_agent.voice.routing import VoiceIntentRouter
from pc_manager_agent.voice.speech import SpeechOutcome, SpeechSummaryBuilder, SpeechSummaryFacts


class FakeCapture:
    def __init__(self):
        self.started = 0
        self.stopped = False

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped = True
        return CapturedAudio(pcm=b"\x01\x00" * 2400)

    def discard(self):
        self.stopped = True


class FakeSTT:
    destination = "synthetic STT | test-endpoint | model"

    def __init__(self, text="检查内存"):
        self.text, self.calls = text, 0

    async def transcribe(self, audio, language_hint):
        self.calls += 1
        return SpeechToTextResult(text=self.text, is_final=True, provider="fake")


class FakeTTS:
    destination = "synthetic TTS | test-endpoint | model | voice"

    async def synthesize(self, text):
        return SpeechAudio(pcm=b"\0\0" * 240, provider="fake")


def captured(coordinator):
    capture = FakeCapture()
    ptt = PushToTalkController(capture, MicrophonePermissionService(lambda: False), coordinator)
    reference = ptt.press(VoiceInteractionContext(), user_gesture=True, visible=True)
    ptt.release()
    return reference, ptt, capture


def reviewed(coordinator, text="检查内存"):
    reference, _, _ = captured(coordinator)
    provider = FakeSTT(text)
    service = VoiceProviderService(coordinator, provider, FakeTTS())
    asyncio.run(service.transcribe(service.prepare_stt(reference), True, Event()))
    return reference, service, provider


def test_full_ptt_review_and_one_request(coordinator):
    reference, _, provider = reviewed(coordinator)
    assert provider.calls == 1
    assert coordinator.transcript.confidence_level is ConfidenceLevel.UNKNOWN
    router = VoiceIntentRouter(coordinator, UserRequestDispatcher())
    request, route = router.submit(reference, "检查 CPU")
    assert request.channel is RequestChannel.VOICE_EDITED
    assert route.domain is RequestDomain.DIAGNOSTICS
    assert coordinator.state is VoiceState.COMPLETED
    with pytest.raises(VoiceError):
        router.submit(reference, "检查 CPU")
    assert coordinator.transcript is None


@pytest.mark.parametrize("risk", list(RiskLevel))
def test_no_voice_confirmation_for_any_risk(risk):
    with pytest.raises(VoiceError, match=f"NOT_ALLOWED_FOR_{risk.value}"):
        VoiceConfirmationPolicy().require_visual(risk)


@pytest.mark.parametrize(
    "text",
    [
        "password: synthetic",
        "api_key = synthetic",
        "我的密码是测试",
        "confirmation_id",
        "nonce",
        "C:\\Users\\person\\report.txt",
        "https://example.invalid",
        "`code`",
        "a" * 64,
    ],
)
def test_speech_sensitive_output_denied(text):
    assert SafeSpeechOutputPolicy().check(text, 300) is not SpeechDecision.ALLOW_SUMMARY


@pytest.mark.parametrize(
    "text", ["token=synthetic", "密码是测试", "API KEY synthetic", "银行卡号123456789012"]
)
def test_transcript_secrets_never_routed(text):
    assert SensitiveTranscriptRedactor().redact(text) == "[SENSITIVE_TRANSCRIPT_REDACTED]"
    with pytest.raises(VoiceError, match="SENSITIVE"):
        TranscriptConfidencePolicy().validate_text(text)


@pytest.mark.parametrize(
    "confidence, expected", [(None, "UNKNOWN"), (0.99, "HIGH"), (0.8, "MEDIUM"), (0.2, "LOW")]
)
def test_real_confidence_only(confidence, expected):
    result = SpeechToTextResult(
        text="检查内存",
        is_final=True,
        provider="fake",
        confidence=confidence,
        confidence_evidence="not_provided" if confidence is None else "provider_calibrated",
    )
    assert TranscriptConfidencePolicy().assess(result).value == expected


def test_unsubstantiated_confidence_rejected():
    with pytest.raises(ValidationError):
        SpeechToTextResult(text="text", is_final=True, provider="fake", confidence=0.98)


@pytest.mark.parametrize("text", ["", "  ", "text\x00", "x\u202ey", "x" * 4001])
def test_transcript_limits(text):
    with pytest.raises(VoiceError):
        TranscriptConfidencePolicy().validate_text(text)


def test_audio_contract_and_memory_wav():
    buffer = BoundedAudioBuffer(VoiceSettings(max_voice_input_seconds=1))
    buffer.append(b"\x02\x00" * 24000)
    with pytest.raises(VoiceError):
        buffer.append(b"\0\0")
    audio = buffer.finish()
    assert buffer.size == 0 and audio.duration_seconds == 1
    assert "pcm" not in audio.model_dump() and "\\x02" not in repr(audio)
    with wave.open(io.BytesIO(wav_bytes(audio)), "rb") as recording:
        assert recording.getframerate() == 24000
        assert recording.getnchannels() == 1
        assert recording.readframes(24000) == audio.pcm
    with pytest.raises(VoiceError):
        buffer.finish()
    for pcm in [b"a", b"\0" * (120 * 48000 + 2)]:
        with pytest.raises(ValidationError):
            CapturedAudio(pcm=pcm)


@pytest.mark.parametrize(
    "gesture,visible,elevated,status",
    [
        (False, True, False, MicrophoneStatus.UNKNOWN),
        (True, False, False, MicrophoneStatus.UNKNOWN),
        (True, True, True, MicrophoneStatus.UNKNOWN),
        (True, True, False, MicrophoneStatus.DENIED),
        (True, True, False, MicrophoneStatus.MISSING),
    ],
)
def test_permission_boundaries(gesture, visible, elevated, status):
    with pytest.raises(VoiceError):
        MicrophonePermissionService(lambda: elevated).require_activation(
            user_gesture=gesture, visible=visible, status=status
        )


def test_duplicate_ptt_and_cancel(coordinator):
    capture = FakeCapture()
    ptt = PushToTalkController(capture, MicrophonePermissionService(lambda: False), coordinator)
    ptt.press(VoiceInteractionContext(), user_gesture=True, visible=True)
    with pytest.raises(VoiceError):
        ptt.press(VoiceInteractionContext(), user_gesture=True, visible=True)
    ptt.cancel()
    assert capture.started == 1 and capture.stopped and not ptt.active
    assert coordinator.state is VoiceState.CANCELLED
    with pytest.raises(VoiceError):
        ptt.release()


def test_partial_and_duplicate_callback(coordinator):
    reference, _, _ = captured(coordinator)
    coordinator.start_transcription(reference)
    with pytest.raises(VoiceError, match="PARTIAL"):
        coordinator.accept_final(
            reference, SpeechToTextResult(text="检查", is_final=False, provider="fake")
        )
    coordinator.accept_final(
        reference, SpeechToTextResult(text="检查内存", is_final=True, provider="fake")
    )
    with pytest.raises(VoiceError):
        coordinator.accept_final(
            reference, SpeechToTextResult(text="删除", is_final=True, provider="fake")
        )


def test_concurrent_consumption(coordinator):
    reference, _, _ = reviewed(coordinator)

    def consume():
        try:
            return coordinator.consume_final(reference, "检查内存")
        except VoiceError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: consume(), range(2)))
    assert sum(item is not None for item in results) == 1


def test_disclosure_binding_and_expiry(coordinator):
    clock = [100.0]
    service = VoiceDisclosureService(coordinator.store, lambda: clock[0], 60)
    value = service.offer(uuid4(), "STT", "fake", "content", 1, "zh")
    with pytest.raises(VoiceError):
        service.consume(value, True, "changed", "content", "zh")
    with pytest.raises(VoiceError):
        service.consume(value, True, "fake", "changed", "zh")
    with pytest.raises(VoiceError):
        service.consume(value, True, "fake", "content", "en")
    service.consume(value, True, "fake", "content", "zh")
    with pytest.raises(VoiceError):
        service.consume(value, True, "fake", "content", "zh")
    expired = service.offer(uuid4(), "STT", "fake", "content", 1)
    clock[0] = 160
    with pytest.raises(VoiceError):
        service.consume(expired, True, "fake", "content")


def test_rejected_upload_does_not_call_provider(coordinator):
    reference, _, _ = captured(coordinator)
    provider = FakeSTT()
    service = VoiceProviderService(coordinator, provider, None)
    with pytest.raises(VoiceError, match="REJECTED"):
        asyncio.run(service.transcribe(service.prepare_stt(reference), False, Event()))
    assert provider.calls == 0 and coordinator.state is VoiceState.FAILED


def test_speech_summary_and_barge_in_invalidation(coordinator):
    service = VoiceProviderService(coordinator, FakeSTT(), FakeTTS())
    proposal = service.prepare_speech(SpeechSummaryFacts(outcome=SpeechOutcome.UNVERIFIED))
    assert "尚未可靠验证" in proposal.text
    result = asyncio.run(service.synthesize(proposal, True, Event()))
    assert result.pcm
    with pytest.raises(VoiceError):
        asyncio.run(service.synthesize(proposal, True, Event()))
    proposal = service.prepare_speech(SpeechSummaryFacts(outcome=SpeechOutcome.PREPARATION))
    service.stop_speech()
    with pytest.raises(VoiceError):
        asyncio.run(service.synthesize(proposal, True, Event()))


@pytest.mark.parametrize("outcome", list(SpeechOutcome))
def test_every_summary_uses_only_structured_facts(outcome):
    text = SpeechSummaryBuilder().build(SpeechSummaryFacts(outcome=outcome, verified_items=3))
    assert len(text) <= 300
    assert SafeSpeechOutputPolicy().check(text, 300) is SpeechDecision.ALLOW_SUMMARY


@pytest.mark.parametrize(
    "text,domain",
    [
        ("检查内存", "DIAGNOSTICS"),
        ("找出大文件", "FILES"),
        ("重命名文件", "FILE_OPERATIONS"),
        ("移入回收站", "TRASH"),
        ("清空回收站", "RECYCLE_BIN_EMPTY"),
        ("清理缓存", "CLEANUP"),
        ("优化电脑", "OPTIMIZATION"),
        ("关闭 Chrome", "PROCESS"),
        ("关闭 Chrome 进程", "PROCESS"),
        ("把 Spotify startup 关掉", "STARTUP"),
        ("停止 Example service", "SERVICE"),
        ("卸载 Python", "SOFTWARE"),
        ("预算增加5%", "OFFICE"),
        ("确认卸载", "CONFIRMATION"),
        ("I accept the risk", "CONFIRMATION"),
        ("取消", "CANCEL"),
        ("status", "STATUS"),
        ("执行 powershell", "BLOCKED"),
        ("打开网页", "UNSUPPORTED"),
        ("hello", "UNSUPPORTED"),
    ],
)
def test_shared_routes(text, domain):
    dispatcher = UserRequestDispatcher()
    for channel in RequestChannel:
        request = UserRequest(channel=channel, text=text)
        assert dispatcher.route(request).domain.value == domain
        assert "text" not in request.model_dump()


def test_provider_cancellation_and_timeout():
    async def slow():
        await asyncio.sleep(10)

    cancelled = Event()
    cancelled.set()
    with pytest.raises(VoiceError, match="CANCELLED"):
        asyncio.run(bounded_provider_call(slow(), 1, cancelled))
    with pytest.raises(VoiceError, match="TIMEOUT"):
        asyncio.run(bounded_provider_call(slow(), 0.001, Event()))


def test_restart_never_resumes(coordinator, tmp_path):
    reference, _, _ = captured(coordinator)
    pending = VoiceProviderService(coordinator, FakeSTT(), None).prepare_stt(reference)
    restarted = VoiceTranscriptConsumptionStore(tmp_path / "voice.db", uuid4())
    try:
        assert restarted.recent()[0].state is VoiceState.INTERRUPTED
        with pytest.raises(VoiceError):
            coordinator.store.consume(
                pending.reference,
                reference,
                "STT",
                pending.payload_digest,
                coordinator.clock(),
                approved=True,
            )
    finally:
        restarted.close()
