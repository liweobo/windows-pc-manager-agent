"""Shared UI speech lifecycle; one input, one playback, bounded cancellable provider work."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from threading import Event
from typing import Protocol
from uuid import UUID, uuid4

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from pc_manager_agent.app.voice import VoiceServices
from pc_manager_agent.config.voice import VoiceSettings
from pc_manager_agent.confirmation.voice_disclosure import VoiceDisclosure
from pc_manager_agent.domain.user_requests import VoiceInteractionContext
from pc_manager_agent.domain.voice import SpeechAudio, VoiceError, VoiceState, VoiceTranscript
from pc_manager_agent.voice.audio import AudioCaptureService, MicrophonePermissionService
from pc_manager_agent.voice.providers import SpeechProposal
from pc_manager_agent.voice.push_to_talk import PushToTalkController
from pc_manager_agent.voice.speech import SpeechSummaryFacts


class AudioPlayback(Protocol):
    """Playback port for the Main UI or a silent deterministic test fake."""

    def play(self, audio: SpeechAudio) -> None:
        """Start bounded generated PCM."""
        ...

    def stop(self) -> None:
        """Flush queued speech immediately."""
        ...


class VoiceJobSignals(QObject):
    """Carry transient results and stable error codes, never raw exception messages."""

    done = Signal(object, object, str)


class VoiceJob(QRunnable):
    """Run one cancellable network coroutine in a private pool, never on the GUI thread."""

    def __init__(self, work: Callable[[], Coroutine[object, object, object]]) -> None:
        super().__init__()
        self.reference = uuid4()
        self.cancelled = Event()
        self.signals = VoiceJobSignals()
        self.work = work

    def run(self) -> None:
        """Bounded provider service owns deadline/cleanup; late UI results are discarded by ID."""
        try:
            result = asyncio.run(self.work())
        except Exception as exc:
            code = exc.code if isinstance(exc, VoiceError) else "VOICE_PROVIDER_FAILED"
            self.signals.done.emit(self.reference, None, code)
        else:
            self.signals.done.emit(self.reference, result, "")


class VoiceUiController(QObject):
    """UI-only coordinator, not a tool/plan confirmer or business executor."""

    changed = Signal()
    request_ready = Signal(object, object)

    def __init__(
        self,
        services: VoiceServices,
        capture: AudioCaptureService,
        playback: AudioPlayback,
        permission: MicrophonePermissionService,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.services, self.playback = services, playback
        self.ptt = PushToTalkController(capture, permission, services.coordinator)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.job: VoiceJob | None = None
        self.message = "未录音；按住说话。识别后必须检查文字，不接受语音确认。"
        self.review_text = ""
        self.speaking = False
        self._playback_reference: UUID | None = None
        self._playback_audit_failed = False
        self._closed = False

    def press(self, context: VoiceInteractionContext, *, visible: bool) -> None:
        """Flush output, discard old input, and require a fresh visible activation."""
        try:
            if self._closed or self.ptt.active:
                raise VoiceError("VOICE_INPUT_ALREADY_ACTIVE")
            if self._playback_audit_failed:
                raise VoiceError("VOICE_AUDIT_UNAVAILABLE_RESTART_REQUIRED")
            self.cancel()
            # Do not accumulate cancelled network jobs behind a new capture.
            if self.pool.activeThreadCount():
                raise VoiceError("VOICE_WAIT_FOR_PROVIDER_CANCELLATION")
            self.ptt.press(context, user_gesture=True, visible=visible)
            self.message = "正在录音；松开结束，隐藏窗口会丢弃录音。"
        except Exception as exc:
            self.error(exc)
        self.changed.emit()

    def release(self) -> None:
        """Finish input only; upload is a separate explicit confirmation."""
        if not self.ptt.active:
            return
        try:
            self.ptt.release()
            self.message = "录音已停止。请检查外发范围后决定是否上传识别。"
        except Exception as exc:
            self.error(exc)
        self.changed.emit()

    def disclosure(self) -> VoiceDisclosure:
        """Prepare exact STT disclosure from the stopped current recording."""
        reference = self.services.coordinator.reference
        if reference is None:
            raise VoiceError("VOICE_AUDIO_UNAVAILABLE")
        return self.services.providers.prepare_stt(reference)

    def configure(self, settings: VoiceSettings) -> None:
        """Apply volatile provider/language/output preferences only while all work is stopped."""
        self.cancel()
        if self.pool.activeThreadCount():
            raise VoiceError("VOICE_WAIT_FOR_PROVIDER_CANCELLATION")
        self.services = self.services.reconfigure(settings)
        self.message = "语音设置已应用到本次运行；未录音或联系供应商。"
        self.changed.emit()

    def capture_progress(self, milliseconds: int) -> None:
        """Display captured duration without sampling or retaining additional audio."""
        if self.ptt.active:
            self.message = f"正在录音：{milliseconds / 1000:.1f} 秒；松开结束。"
            self.changed.emit()

    def transcribe(self, disclosure: VoiceDisclosure, approved: bool) -> None:
        """Dispatch one exact independently confirmed upload, without blocking Qt."""
        if self.job is not None:
            raise VoiceError("VOICE_PROVIDER_ALREADY_ACTIVE")
        job = VoiceJob(
            lambda: self.services.providers.transcribe(disclosure, approved, job.cancelled)
        )
        self._start_job(job)

    def submit(self, text: str) -> None:
        """Consume the reviewed recording once before handing off a normal UserRequest."""
        try:
            reference = self.services.coordinator.reference
            if reference is None:
                raise VoiceError("VOICE_TRANSCRIPT_UNAVAILABLE")
            request, route = self.services.router.submit(reference, text)
            self.review_text = ""
            self.message = "已提交请求；不代表计划已确认或操作已执行。"
            self.request_ready.emit(request, route)
        except Exception as exc:
            self.error(exc)
        self.changed.emit()

    def speech_proposal(self, facts: SpeechSummaryFacts) -> SpeechProposal:
        """Build a finite factual summary for a separate visible TTS upload preview."""
        if self.ptt.active or self.services.coordinator.state in {
            VoiceState.TRANSCRIBING,
            VoiceState.AWAITING_DISCLOSURE,
        }:
            raise VoiceError("VOICE_INPUT_ACTIVE")
        return self.services.providers.prepare_speech(facts)

    def speak(self, proposal: SpeechProposal, approved: bool) -> None:
        """Synthesize only the exact confirmed proposal; never replay automatically."""
        if self.job is not None:
            raise VoiceError("VOICE_PROVIDER_ALREADY_ACTIVE")
        if self._playback_audit_failed:
            raise VoiceError("VOICE_AUDIT_UNAVAILABLE_RESTART_REQUIRED")
        job = VoiceJob(
            lambda: self.services.providers.synthesize(proposal, approved, job.cancelled)
        )
        self._start_job(job)

    def stop_speech(self) -> None:
        """Invalidate queued TTS, cancel network work and flush hardware before new input."""
        self.playback.stop()
        self.speaking = False
        self._finish_playback_audit("VOICE_PLAYBACK_STOPPED")
        self.services.providers.stop_speech()
        if self.job is not None:
            self.job.cancelled.set()
            self.job = None

    def cancel(self) -> None:
        """Stop only voice input/output; cancellation of a business task is separately bound."""
        self.stop_speech()
        self.review_text = ""
        try:
            self.ptt.cancel()
            if not self._playback_audit_failed:
                self.message = "语音输入/播报已停止；已发出的网络数据不能撤回。"
        except Exception as exc:
            self.error(exc)
        self.changed.emit()

    def hardware_error(self, code: str) -> None:
        """Stop all audio before reporting a native capture/playback failure."""
        self.cancel()
        self.error(VoiceError(code))
        self.changed.emit()

    def playback_finished(self) -> None:
        """Acknowledge output completion, never business completion."""
        self.speaking = False
        self._finish_playback_audit("VOICE_PLAYBACK_FINISHED")
        if not self._playback_audit_failed:
            self.message = "播报结束（AI 合成语音）。"
        self.changed.emit()

    def _finish_playback_audit(self, code: str) -> None:
        reference, self._playback_reference = self._playback_reference, None
        if reference is not None:
            try:
                self.services.coordinator.audit.record(reference, VoiceState.COMPLETED, code=code)
            except Exception as exc:
                # Native audio was already stopped; journal failure cannot keep a mic alive.
                self._playback_audit_failed = True
                self.error(exc)

    def error(self, exc: Exception) -> None:
        """Present stable codes with actionable local help, not provider bodies or secrets."""
        code = exc.code if isinstance(exc, VoiceError) else "VOICE_OPERATION_FAILED"
        self.message = (
            f"未继续语音操作：{code}。请检查语音设置、Windows 麦克风权限和默认设备；"
            "可使用文字输入。不会自动重试。"
        )

    def shutdown(self) -> bool:
        """Stop hardware immediately; defer database shutdown while network cleanup is pending."""
        self.cancel()
        if not self.pool.waitForDone(1000):
            return False
        if not self._closed:
            self.services.close()
            self._closed = True
        return True

    def _start_job(self, job: VoiceJob) -> None:
        self.job = job
        job.signals.done.connect(self._completed)
        self.message = "语音请求处理中；可以取消，不会直接执行系统操作。"
        self.pool.start(job)
        self.changed.emit()

    def _completed(self, reference: UUID, value: object, code: str) -> None:
        if self.job is None or self.job.reference != reference or self._closed:
            return
        self.job = None
        try:
            if code:
                raise VoiceError(code)
            if isinstance(value, VoiceTranscript):
                self.review_text = value.text
                self.message = f"请检查识别文字。可信度：{value.confidence_level.value}。"
            elif isinstance(value, SpeechAudio):
                self.services.coordinator.audit.record(
                    reference, VoiceState.PROCESSING, code="VOICE_PLAYBACK_REQUESTED"
                )
                self._playback_reference = reference
                self.speaking = True
                self.playback.play(value)
                if self.speaking:
                    self.message = "正在播报 AI 合成语音；按住说话会立即停止播报。"
            else:
                raise VoiceError("VOICE_PROVIDER_RESULT_INVALID")
        except Exception as exc:
            if isinstance(value, SpeechAudio):
                self.stop_speech()
            self.error(exc)
        self.changed.emit()
