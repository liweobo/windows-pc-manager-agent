"""OpenAI short-summary TTS with bounded streaming PCM, not an arbitrary audio player."""

from openai import APIError, AsyncOpenAI
from pydantic import ValidationError

from pc_manager_agent.config.voice import VoiceSettings
from pc_manager_agent.domain.voice import SpeechAudio, VoiceError
from pc_manager_agent.providers.speech_logging import require_private_speech_logging


class OpenAITextToSpeechProvider:
    """Finite built-in voices; no cloning, shell players, disk audio or automatic retries."""

    def __init__(self, settings: VoiceSettings, client: AsyncOpenAI | None = None) -> None:
        self._settings, self._client = settings, client

    @property
    def destination(self) -> str:
        """Bind the voice together with endpoint/model in the exact disclosure."""
        return (
            f"OpenAI | https://api.openai.com/v1/audio/speech | "
            f"{self._settings.tts_model} | {self._settings.tts_voice}"
        )

    async def synthesize(self, text: str) -> SpeechAudio:
        """Read PCM in small chunks, closing the response on limit, timeout or cancellation."""
        require_private_speech_logging()
        if not text or len(text) > self._settings.max_spoken_response_chars:
            raise VoiceError("VOICE_SPEECH_LENGTH_LIMIT")
        key = self._settings.api_key
        if self._client is None and (key is None or not key.get_secret_value().strip()):
            raise VoiceError("VOICE_PROVIDER_CONFIGURATION_REQUIRED")
        client = self._client or AsyncOpenAI(
            api_key=key.get_secret_value() if key else "",
            base_url="https://api.openai.com/v1",
            timeout=self._settings.tts_timeout,
            max_retries=0,
        )
        audio = bytearray()
        try:
            async with client.audio.speech.with_streaming_response.create(
                model=self._settings.tts_model,
                voice=self._settings.tts_voice,
                input=text,
                response_format="pcm",
            ) as response:
                async for chunk in response.iter_bytes(chunk_size=16384):
                    if len(audio) + len(chunk) > 120 * 48000:
                        raise VoiceError("VOICE_SPEECH_AUDIO_LIMIT")
                    audio.extend(chunk)
            return SpeechAudio(pcm=bytes(audio), provider="openai")
        except (APIError, ValidationError):
            raise VoiceError("VOICE_TTS_REQUEST_FAILED") from None
        finally:
            audio.clear()
            if self._client is None:
                await client.close()
