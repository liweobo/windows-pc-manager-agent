"""Typed STT interface independent of UI, planner and business execution."""

from typing import Protocol

from pc_manager_agent.domain.voice import CapturedAudio, SpeechToTextResult


class SpeechToTextProvider(Protocol):
    """A provider can only transcribe already captured and separately consented audio."""

    @property
    def destination(self) -> str:
        """Identify exact provider, endpoint and model for disclosure binding."""
        ...

    async def transcribe(self, audio: CapturedAudio, language_hint: str) -> SpeechToTextResult:
        """Return untrusted text; never route, approve, record or retry a business operation."""
        ...
