"""Failure injection uses synthetic data and private temporary SQLite only."""

import asyncio
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import MetaData
from sqlalchemy.exc import SQLAlchemyError
from tests.unit.voice.test_foundation import FakeCapture, FakeSTT, FakeTTS, captured, reviewed

from pc_manager_agent.config.voice import VoiceSettings
from pc_manager_agent.domain.optimization_actions import OptimizationOutcomeType
from pc_manager_agent.domain.optimization_receipts import (
    DomainReceiptSnapshot,
    OptimizationReceiptKind,
    OptimizationTransactionReference,
)
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.user_requests import (
    RequestChannel,
    RequestDomain,
    UserRequest,
    VoiceInteractionContext,
)
from pc_manager_agent.domain.voice import CapturedAudio, SpeechAudio, VoiceError, VoiceState
from pc_manager_agent.orchestration.user_requests import UserRequestDispatcher
from pc_manager_agent.persistence.voice import VoiceTranscriptConsumptionStore
from pc_manager_agent.safety.voice import SafeSpeechOutputPolicy, SpeechDecision
from pc_manager_agent.voice.audio import MicrophonePermissionService
from pc_manager_agent.voice.providers import VoiceProviderService, bounded_provider_call
from pc_manager_agent.voice.push_to_talk import PushToTalkController
from pc_manager_agent.voice.results import VoiceResultSummaryService
from pc_manager_agent.voice.speech import SpeechOutcome, SpeechSummaryBuilder, SpeechSummaryFacts


def fail(*_args, **_kwargs):
    raise SQLAlchemyError("synthetic storage failure")


@pytest.mark.parametrize("method", ["create", "transition", "offer", "consume", "recent"])
def test_storage_unavailable_rejects_every_boundary(coordinator, monkeypatch, method):
    store = coordinator.store
    record = store.create(uuid4(), 1)
    monkeypatch.setattr(store._engine, "begin", fail)
    monkeypatch.setattr(store._engine, "connect", fail)
    calls = {
        "create": lambda: store.create(uuid4(), 1),
        "transition": lambda: store.transition(record, VoiceState.LISTENING, 2),
        "offer": lambda: store.offer(uuid4(), uuid4(), "STT", "a" * 64, 100),
        "consume": lambda: store.consume(uuid4(), uuid4(), "STT", "a" * 64, 2, approved=True),
        "recent": lambda: store.recent(),
    }
    with pytest.raises(VoiceError, match="STORAGE_UNAVAILABLE"):
        calls[method]()


def test_storage_initialization_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(MetaData, "create_all", fail)
    with pytest.raises(VoiceError, match="STORAGE_UNAVAILABLE"):
        VoiceTranscriptConsumptionStore(tmp_path / "fake.db", uuid4())


def test_stale_revision_cannot_update(coordinator):
    record = coordinator.store.create(uuid4(), 1)
    coordinator.store.transition(record, VoiceState.LISTENING, 2)
    with pytest.raises(VoiceError, match="STALE"):
        coordinator.store.transition(record, VoiceState.CANCELLED, 3)


@pytest.mark.parametrize("failure_method", ["start", "stop"])
def test_capture_failures_stop_hardware_first(coordinator, monkeypatch, failure_method):
    capture = FakeCapture()
    ptt = PushToTalkController(capture, MicrophonePermissionService(lambda: False), coordinator)
    monkeypatch.setattr(capture, failure_method, fail)
    with pytest.raises(SQLAlchemyError):
        ptt.press(VoiceInteractionContext(), user_gesture=True, visible=True)
        ptt.release()
    assert capture.stopped and not ptt.active
    assert coordinator.state is VoiceState.FAILED


def test_audit_failure_never_leaves_recording_or_releases_request(coordinator, monkeypatch):
    _reference, ptt, capture = captured(coordinator)
    monkeypatch.setattr(coordinator.audit, "record", fail)
    with pytest.raises(SQLAlchemyError):
        ptt.cancel()
    assert capture.stopped and not ptt.active
    assert coordinator._audio is None


def test_missing_and_expired_transcript_and_audio(coordinator):
    reference, _, _ = captured(coordinator)
    with pytest.raises(VoiceError, match="ALREADY_ACTIVE"):
        coordinator.begin(VoiceInteractionContext())
    coordinator.drop_audio(reference)
    with pytest.raises(VoiceError, match="UNAVAILABLE"):
        coordinator.audio_for_disclosure(reference)
    coordinator.cancel()
    reference, _, _ = reviewed(coordinator)
    coordinator.clock = lambda: 10**12
    with pytest.raises(VoiceError, match="EXPIRED"):
        coordinator.consume_final(reference, "检查内存")
    coordinator._transcript = None
    with pytest.raises(VoiceError, match="UNAVAILABLE"):
        coordinator.consume_final(reference, "检查内存")


def test_coordinator_limit_and_stale_fail(coordinator):
    reference = coordinator.begin(VoiceInteractionContext())
    coordinator.listening(reference)
    coordinator.settings = VoiceSettings(max_voice_input_seconds=1)
    with pytest.raises(VoiceError, match="LIMIT"):
        coordinator.finish_capture(reference, CapturedAudio(pcm=b"\0\0" * 48000))
    coordinator.fail(uuid4(), "LATE_FAILURE")
    assert coordinator.state is VoiceState.LISTENING
    coordinator.drop_audio(uuid4())
    coordinator.fail(reference, "CAPTURE_FAILED")
    coordinator.fail(reference, "DUPLICATE_FAILED")
    coordinator.cancel()


def test_disabled_provider_and_cancel_before_dispatch(coordinator):
    reference, _, _ = captured(coordinator)
    disabled = VoiceProviderService(coordinator, None, None)
    assert not disabled.configured
    with pytest.raises(VoiceError, match="CONFIGURATION"):
        disabled.prepare_stt(reference)
    service = VoiceProviderService(coordinator, FakeSTT(), FakeTTS())
    proposal = service.prepare_stt(reference)
    cancel = Event()
    cancel.set()
    with pytest.raises(VoiceError, match="CANCELLED"):
        asyncio.run(service.transcribe(proposal, True, cancel))
    with pytest.raises(VoiceError, match="TTS_DISABLED"):
        disabled.prepare_speech(SpeechSummaryFacts(outcome=SpeechOutcome.PREPARATION))


def test_late_tts_and_unexpected_errors(coordinator):
    class LateTTS:
        destination = "fake"

        async def synthesize(self, text):
            service.stop_speech()
            return SpeechAudio(pcm=b"\0\0", provider="fake")

    service = VoiceProviderService(coordinator, FakeSTT(), LateTTS())
    proposal = service.prepare_speech(SpeechSummaryFacts(outcome=SpeechOutcome.PREPARATION))
    with pytest.raises(VoiceError, match="STALE"):
        asyncio.run(service.synthesize(proposal, True, Event()))
    assert service._speech is None


def test_cancel_immediately_after_provider_result():
    cancel = Event()

    async def result():
        cancel.set()
        return 1

    with pytest.raises(VoiceError, match="CANCELLED"):
        asyncio.run(bounded_provider_call(result(), 1, cancel))


def test_invalid_audio_and_summary_length():
    with pytest.raises(ValidationError):
        SpeechAudio(pcm=b"123", provider="fake")
    assert SafeSpeechOutputPolicy().check("", 10) is SpeechDecision.TEXT_ONLY
    with pytest.raises(VoiceError, match="POLICY"):
        SpeechSummaryBuilder().build(SpeechSummaryFacts(outcome=SpeechOutcome.VERIFIED), 1)
    for recovery in RollbackLevel:
        text = SpeechSummaryBuilder().build(
            SpeechSummaryFacts(
                outcome=SpeechOutcome.UNVERIFIED, risk=RiskLevel.R2, recovery=recovery
            )
        )
        assert "R2" in text and "不能确认操作成功" in text


def test_context_and_english_bin_routes():
    dispatcher = UserRequestDispatcher()
    request = UserRequest(
        channel=RequestChannel.VOICE,
        text="关掉它",
        context=VoiceInteractionContext(active_surface=RequestDomain.PROCESS),
    )
    assert dispatcher.route(request).domain is RequestDomain.PROCESS
    assert dispatcher.route(UserRequest(channel=RequestChannel.TEXT, text="empty recycle bin"))


def test_unsafe_audit_code_is_not_recorded(coordinator):
    coordinator.audit.record(uuid4(), VoiceState.FAILED, code="arbitrary body secret value")
    events = coordinator.audit._repository.list_recent(1)
    assert events[0].parameters["code"] == "VOICE_OPERATION_FAILED"


@pytest.mark.parametrize("outcome", list(OptimizationOutcomeType))
def test_speech_receipt_preserves_truth(outcome):
    now = datetime.now(UTC)
    reference = OptimizationTransactionReference(
        kind=OptimizationReceiptKind.MSI, transaction_id=uuid4()
    )
    receipt = DomainReceiptSnapshot(
        reference=reference,
        plan_id=uuid4(),
        plan_digest="a" * 64,
        created_at=now,
        updated_at=now,
        state="COMPLETE",
        outcome=outcome,
        confirmation_id=uuid4(),
        risk=RiskLevel.R2,
        recovery=RollbackLevel.NONE,
    )
    service = VoiceResultSummaryService(lambda _: receipt, now - timedelta(seconds=1))
    result = service.summarize(reference)
    assert (result.outcome is SpeechOutcome.VERIFIED) == (
        outcome is OptimizationOutcomeType.APPLIED_VERIFIED
    )
    assert result.recovery is RollbackLevel.NONE
    assert (
        VoiceResultSummaryService(lambda _: receipt, now + timedelta(seconds=1))
        .summarize(reference)
        .outcome
        is SpeechOutcome.UNVERIFIED
    )
    assert service.summarize(reference.model_copy(update={"transaction_id": uuid4()})).outcome is (
        SpeechOutcome.UNVERIFIED
    )
    assert (
        VoiceResultSummaryService(fail, now).summarize(reference).outcome
        is SpeechOutcome.UNVERIFIED
    )
