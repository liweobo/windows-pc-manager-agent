"""Official, bounded OpenAI file STT; isolated from legacy user-customized LLM endpoints."""

from openai import APIError, AsyncOpenAI, omit
from pydantic import ValidationError

from pc_manager_agent.config.voice import VoiceSettings
from pc_manager_agent.domain.voice import CapturedAudio, SpeechToTextResult, VoiceError
from pc_manager_agent.providers.speech_logging import require_private_speech_logging
from pc_manager_agent.voice.audio import wav_bytes


class OpenAISpeechToTextProvider:
    """Completed-recording transcription only; no realtime listener, diarization or tools."""

    def __init__(self, settings: VoiceSettings, client: AsyncOpenAI | None = None) -> None:
        self._settings, self._client = settings, client

    @property
    def destination(self) -> str:
        """Show the official API destination instead of implicitly inheriting a custom proxy."""
        return (
            f"OpenAI | https://api.openai.com/v1/audio/transcriptions | {self._settings.stt_model}"
        )

    async def transcribe(self, audio: CapturedAudio, language_hint: str) -> SpeechToTextResult:
        """Use an in-memory WAV with a generic filename; confidence is explicitly unknown."""
        require_private_speech_logging()
        key = self._settings.api_key
        if self._client is None and (key is None or not key.get_secret_value().strip()):
            raise VoiceError("VOICE_PROVIDER_CONFIGURATION_REQUIRED")
        client = self._client or AsyncOpenAI(
            api_key=key.get_secret_value() if key else "",
            base_url="https://api.openai.com/v1",
            timeout=self._settings.stt_timeout,
            max_retries=0,
        )
        try:
            response = await client.audio.transcriptions.create(
                file=("input.wav", wav_bytes(audio), "audio/wav"),
                model=self._settings.stt_model,
                language=language_hint if language_hint in {"zh", "en"} else omit,
                response_format="json",
                stream=False,
            )
            return SpeechToTextResult(
                text=response.text,
                is_final=True,
                provider="openai",
                confidence=None,
                provider_request_id=response._request_id,
            )
        except (APIError, ValidationError):
            raise VoiceError("VOICE_STT_REQUEST_FAILED") from None
        finally:
            if self._client is None:
                await client.close()
