"""Single input coordinator. At-most-once request delivery is not business execution authority."""

from __future__ import annotations

import time
from collections.abc import Callable
from threading import RLock
from uuid import UUID, uuid4

from pc_manager_agent.audit.voice import VoiceAudit
from pc_manager_agent.config.voice import VoiceSettings
from pc_manager_agent.domain.user_requests import (
    RequestChannel,
    UserRequest,
    VoiceInteractionContext,
)
from pc_manager_agent.domain.voice import (
    CapturedAudio,
    SpeechToTextResult,
    VoiceError,
    VoiceState,
    VoiceTranscript,
    transcript_digest,
)
from pc_manager_agent.persistence.voice import VoiceSessionRecord, VoiceTranscriptConsumptionStore
from pc_manager_agent.safety.voice import TranscriptConfidencePolicy


class VoiceSessionCoordinator:
    """Own volatile input and metadata; no registries, confirmations or Windows writers."""

    def __init__(
        self,
        store: VoiceTranscriptConsumptionStore,
        audit: VoiceAudit,
        settings: VoiceSettings,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store, self.audit, self.settings, self.clock = store, audit, settings, clock
        self._lock = RLock()
        self._record: VoiceSessionRecord | None = None
        self._audio: CapturedAudio | None = None
        self._transcript: VoiceTranscript | None = None
        self._context = VoiceInteractionContext()

    @property
    def state(self) -> VoiceState:
        """Report input state; speaking belongs to the independent playback controller."""
        return self._record.state if self._record else VoiceState.IDLE

    @property
    def reference(self) -> UUID | None:
        """Return the active input ID, never a business authorization ID."""
        return self._record.reference if self._record else None

    @property
    def transcript(self) -> VoiceTranscript | None:
        """Expose volatile text only to the local review UI."""
        return self._transcript

    def begin(self, context: VoiceInteractionContext) -> UUID:
        """Journal one new input before the PTT controller opens its microphone."""
        with self._lock:
            if self.state not in {
                VoiceState.IDLE,
                VoiceState.COMPLETED,
                VoiceState.CANCELLED,
                VoiceState.FAILED,
                VoiceState.INTERRUPTED,
            }:
                raise VoiceError("VOICE_INPUT_ALREADY_ACTIVE")
            reference = uuid4()
            self.audit.record(reference, VoiceState.REQUESTING_PERMISSION)
            self._record = self.store.create(reference, self.clock())
            self._audio, self._transcript, self._context = None, None, context
            return reference

    def listening(self, reference: UUID) -> None:
        """Acknowledge actual hardware start only for the current prepared input."""
        with self._lock:
            self._change(reference, VoiceState.REQUESTING_PERMISSION, VoiceState.LISTENING)

    def finish_capture(self, reference: UUID, audio: CapturedAudio) -> None:
        """Keep only complete bounded audio, awaiting separate external upload consent."""
        with self._lock:
            self._require(reference, VoiceState.LISTENING)
            if (
                audio.duration_seconds > self.settings.max_voice_input_seconds
                or len(audio.pcm) > self.settings.max_audio_bytes
            ):
                raise VoiceError("VOICE_INPUT_LIMIT_REACHED")
            self._change(reference, VoiceState.LISTENING, VoiceState.FINALIZING_AUDIO)
            self._audio = audio
            self._change(reference, VoiceState.FINALIZING_AUDIO, VoiceState.AWAITING_DISCLOSURE)

    def audio_for_disclosure(self, reference: UUID) -> CapturedAudio:
        """Return only the current undispatched recording for its exact disclosure preview."""
        with self._lock:
            self._require(reference, VoiceState.AWAITING_DISCLOSURE)
            if self._audio is None:
                raise VoiceError("VOICE_AUDIO_UNAVAILABLE")
            return self._audio

    def start_transcription(self, reference: UUID) -> None:
        """Mark dispatch after consent consumption, before the provider is contacted."""
        with self._lock:
            self._change(reference, VoiceState.AWAITING_DISCLOSURE, VoiceState.TRANSCRIBING)

    def accept_final(self, reference: UUID, result: SpeechToTextResult) -> VoiceTranscript:
        """Reject stale/duplicate/partial output; all final text still requires human review."""
        with self._lock:
            self._require(reference, VoiceState.TRANSCRIBING)
            quality = TranscriptConfidencePolicy().assess(result)
            transcript = VoiceTranscript(
                session_id=reference,
                text=result.text.strip(),
                confidence_level=quality,
                language=result.language,
            )
            self.audit.record(reference, VoiceState.REVIEWING_TRANSCRIPT, confidence=quality)
            self._change(
                reference,
                VoiceState.TRANSCRIBING,
                VoiceState.REVIEWING_TRANSCRIPT,
                digest=transcript_digest(reference, transcript.text),
            )
            self._audio, self._transcript = None, transcript
            return transcript

    def consume_final(self, reference: UUID, reviewed_text: str) -> UserRequest:
        """Allocate one normal request atomically; edited text cannot reuse a consumed recording."""
        with self._lock:
            record = self._require(reference, VoiceState.REVIEWING_TRANSCRIPT)
            if self._transcript is None:
                raise VoiceError("VOICE_TRANSCRIPT_UNAVAILABLE")
            if self.clock() >= record.updated_at + self.settings.review_ttl_seconds:
                raise VoiceError("VOICE_TRANSCRIPT_EXPIRED")
            TranscriptConfidencePolicy().validate_text(reviewed_text)
            edited = reviewed_text.strip() != self._transcript.text
            request = UserRequest(
                channel=RequestChannel.VOICE_EDITED if edited else RequestChannel.VOICE,
                text=reviewed_text.strip(),
                context=self._context,
            )
            self.audit.record(
                reference, VoiceState.ROUTING, edited=edited, request_ref=request.request_id
            )
            self._record = self.store.transition(
                record,
                VoiceState.ROUTING,
                self.clock(),
                digest=transcript_digest(reference, request.text),
                request_ref=request.request_id,
            )
            self._transcript = None
            return request

    def routed(self, reference: UUID) -> None:
        """Finish INPUT delivery only; never report that the requested business action completed."""
        with self._lock:
            self._change(reference, VoiceState.ROUTING, VoiceState.COMPLETED)

    def cancel(self) -> None:
        """Discard volatile input; the caller must stop audio hardware before this journal call."""
        with self._lock:
            self._audio, self._transcript = None, None
            if self._record and self.state not in {
                VoiceState.COMPLETED,
                VoiceState.CANCELLED,
                VoiceState.FAILED,
                VoiceState.INTERRUPTED,
            }:
                self._change(self._record.reference, self.state, VoiceState.CANCELLED)

    def fail(self, reference: UUID, code: str) -> None:
        """Drop input after error; late callbacks cannot change a newer recording."""
        with self._lock:
            if self.reference != reference or self.state in {
                VoiceState.CANCELLED,
                VoiceState.COMPLETED,
                VoiceState.FAILED,
                VoiceState.INTERRUPTED,
            }:
                return
            self._audio, self._transcript = None, None
            self.audit.record(reference, VoiceState.FAILED, code=code)
            self._change(reference, self.state, VoiceState.FAILED)

    def drop_audio(self, reference: UUID) -> None:
        """Release recording references after outbound success, failure or cancellation."""
        with self._lock:
            if self.reference == reference:
                self._audio = None

    def _require(self, reference: UUID, state: VoiceState) -> VoiceSessionRecord:
        if (
            self._record is None
            or self._record.reference != reference
            or self._record.state is not state
        ):
            raise VoiceError("VOICE_SESSION_STALE_OR_CONSUMED")
        return self._record

    def _change(
        self, reference: UUID, expected: VoiceState, state: VoiceState, *, digest: str | None = None
    ) -> None:
        record = self._require(reference, expected)
        self.audit.record(reference, state)
        self._record = self.store.transition(record, state, self.clock(), digest=digest)
