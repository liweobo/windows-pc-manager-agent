"""Provider-neutral short-text synthesis contract."""

from typing import Protocol

from pc_manager_agent.domain.voice import SpeechAudio


class TextToSpeechProvider(Protocol):
    """A provider receives only a policy-checked, explicitly disclosed short summary."""

    @property
    def destination(self) -> str:
        """Bind provider, endpoint, model and voice options in disclosure."""
        ...

    async def synthesize(self, text: str) -> SpeechAudio:
        """Return bounded PCM; do not play it or change the business verification result."""
        ...
