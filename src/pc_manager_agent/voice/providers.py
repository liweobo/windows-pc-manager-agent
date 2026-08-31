"""Independently consented, cancellable provider work; no business executor or confirmation API."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Coroutine
from dataclasses import dataclass
from threading import Event
from typing import TypeVar
from uuid import UUID, uuid4

from pc_manager_agent.confirmation.voice_disclosure import VoiceDisclosure, VoiceDisclosureService
from pc_manager_agent.domain.voice import SpeechAudio, VoiceError, VoiceState, VoiceTranscript
from pc_manager_agent.providers.speech_to_text.base import SpeechToTextProvider
from pc_manager_agent.providers.text_to_speech.base import TextToSpeechProvider
from pc_manager_agent.voice.session import VoiceSessionCoordinator
from pc_manager_agent.voice.speech import SpeechSummaryBuilder, SpeechSummaryFacts

T = TypeVar("T")


async def bounded_provider_call(
    work: Coroutine[object, object, T], timeout: float, cancelled: Event
) -> T:
    """Cancel network work on deadline/user stop, await cleanup, and never retry business work."""
    task = asyncio.create_task(work)
    deadline = asyncio.get_running_loop().time() + timeout
    try:
        while True:
            if cancelled.is_set():
                raise VoiceError("VOICE_PROVIDER_CANCELLED")
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise VoiceError("VOICE_PROVIDER_TIMEOUT")
            done, _ = await asyncio.wait({task}, timeout=min(0.05, remaining))
            if done:
                if cancelled.is_set():
                    raise VoiceError("VOICE_PROVIDER_CANCELLED")
                return task.result()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@dataclass(frozen=True, repr=False)
class SpeechProposal:
    """One volatile text proposal with its separately persisted disclosure digest."""

    disclosure: VoiceDisclosure
    text: str


class VoiceProviderService:
    """STT/TTS boundary; exact single-use disclosure is never business consent."""

    def __init__(
        self,
        coordinator: VoiceSessionCoordinator,
        stt: SpeechToTextProvider | None,
        tts: TextToSpeechProvider | None,
    ) -> None:
        self.coordinator, self._stt, self._tts = coordinator, stt, tts
        self._disclosures = VoiceDisclosureService(
            coordinator.store, coordinator.clock, coordinator.settings.disclosure_ttl_seconds
        )
        self._speech: SpeechProposal | None = None

    @property
    def configured(self) -> bool:
        """Whether a transcription adapter has been explicitly configured."""
        return self._stt is not None

    def prepare_stt(self, reference: UUID) -> VoiceDisclosure:
        """Describe exact recorded bytes, selected language and actual destination before upload."""
        if self._stt is None:
            raise VoiceError("VOICE_PROVIDER_CONFIGURATION_REQUIRED")
        audio = self.coordinator.audio_for_disclosure(reference)
        return self._disclosures.offer(
            reference,
            "STT",
            self._stt.destination,
            audio.digest,
            len(audio.pcm),
            self.coordinator.settings.language_hint,
        )

    async def transcribe(
        self, disclosure: VoiceDisclosure, approved: bool, cancelled: Event
    ) -> VoiceTranscript:
        """Consume upload consent, dispatch once, discard audio and require final review."""
        reference = disclosure.owner_ref
        try:
            if self._stt is None or disclosure.purpose != "STT" or cancelled.is_set():
                raise VoiceError("VOICE_STT_UNAVAILABLE_OR_CANCELLED")
            audio = self.coordinator.audio_for_disclosure(reference)
            language = self.coordinator.settings.language_hint
            started = time.monotonic()
            self.coordinator.audit.provider_event(
                disclosure.reference,
                "STT",
                destination=self._stt.destination,
                approved=approved,
                count=len(audio.pcm),
            )
            self._disclosures.consume(
                disclosure, approved, self._stt.destination, audio.digest, language
            )
            self.coordinator.start_transcription(reference)
            result = await bounded_provider_call(
                self._stt.transcribe(audio, language),
                self.coordinator.settings.stt_timeout,
                cancelled,
            )
            self.coordinator.audit.provider_event(
                disclosure.reference,
                "STT",
                destination=self._stt.destination,
                approved=True,
                completed=True,
                count=len(result.text),
                trace=result.provider_request_id,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            return self.coordinator.accept_final(reference, result)
        except Exception as exc:
            self.coordinator.fail(
                reference, exc.code if isinstance(exc, VoiceError) else "VOICE_STT_REQUEST_FAILED"
            )
            raise VoiceError(
                exc.code if isinstance(exc, VoiceError) else "VOICE_STT_REQUEST_FAILED"
            ) from None
        finally:
            self.coordinator.drop_audio(reference)

    def prepare_speech(self, facts: SpeechSummaryFacts) -> SpeechProposal:
        """Accept only finite facts, never an arbitrary text string supplied by an LLM or UI."""
        if self._tts is None or self.coordinator.settings.spoken_response_mode == "OFF":
            raise VoiceError("VOICE_TTS_DISABLED")
        text = SpeechSummaryBuilder().build(
            facts, self.coordinator.settings.max_spoken_response_chars
        )
        disclosure = self._disclosures.offer(
            uuid4(),
            "TTS",
            self._tts.destination,
            hashlib.sha256(text.encode()).hexdigest(),
            len(text),
        )
        self._speech = SpeechProposal(disclosure, text)
        return self._speech

    async def synthesize(
        self, proposal: SpeechProposal, approved: bool, cancelled: Event
    ) -> SpeechAudio:
        """Consume one exact safe summary; cancellation invalidates late playback results."""
        if self._tts is None or self._speech != proposal or cancelled.is_set():
            raise VoiceError("VOICE_TTS_STALE_OR_CANCELLED")
        try:
            started = time.monotonic()
            self.coordinator.audit.provider_event(
                proposal.disclosure.reference,
                "TTS",
                destination=self._tts.destination,
                approved=approved,
                count=len(proposal.text),
            )
            self._disclosures.consume(
                proposal.disclosure,
                approved,
                self._tts.destination,
                hashlib.sha256(proposal.text.encode()).hexdigest(),
            )
            self.coordinator.audit.record(
                proposal.disclosure.owner_ref,
                VoiceState.PROCESSING,
                code="VOICE_TTS_REQUESTED",
                count=len(proposal.text),
            )
            result = await bounded_provider_call(
                self._tts.synthesize(proposal.text),
                self.coordinator.settings.tts_timeout,
                cancelled,
            )
            if self._speech != proposal:
                raise VoiceError("VOICE_TTS_STALE_OR_CANCELLED")
            self.coordinator.audit.provider_event(
                proposal.disclosure.reference,
                "TTS",
                destination=self._tts.destination,
                approved=True,
                completed=True,
                count=len(result.pcm),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            return result
        except Exception as exc:
            self.coordinator.audit.record(
                proposal.disclosure.owner_ref,
                VoiceState.FAILED,
                code=exc.code if isinstance(exc, VoiceError) else "VOICE_TTS_REQUEST_FAILED",
            )
            raise VoiceError(
                exc.code if isinstance(exc, VoiceError) else "VOICE_TTS_REQUEST_FAILED"
            ) from None
        finally:
            if self._speech == proposal:
                self._speech = None

    def stop_speech(self) -> None:
        """Invalidate a pending synthesis; callers also stop hardware and cancel provider work."""
        self._speech = None
