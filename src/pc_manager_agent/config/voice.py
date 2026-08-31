"""Voice-only settings; never imported into the privileged Broker composition root."""

import os
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class VoiceSettings(BaseModel):
    """Disabled-by-default audio with hard privacy/resource bounds."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    provider: Literal["disabled", "openai"] = "disabled"
    stt_model: str = Field(
        default="gpt-transcribe", min_length=1, max_length=80, pattern=r"^[\w.-]+$"
    )
    tts_model: str = Field(
        default="gpt-4o-mini-tts", min_length=1, max_length=80, pattern=r"^[\w.-]+$"
    )
    tts_voice: Literal["alloy", "coral", "nova", "sage"] = "coral"
    language_hint: Literal["auto", "zh", "en"] = "auto"
    spoken_response_mode: Literal["OFF", "SHORT", "NORMAL"] = "OFF"
    max_voice_input_seconds: int = Field(default=60, ge=1, le=120)
    max_audio_bytes: int = Field(default=6 * 1024**2, ge=48000, le=6 * 1024**2)
    max_spoken_response_chars: int = Field(default=300, ge=60, le=1000)
    stt_timeout: float = Field(default=30, ge=1, le=60)
    tts_timeout: float = Field(default=30, ge=1, le=60)
    disclosure_ttl_seconds: int = Field(default=60, ge=10, le=120)
    review_ttl_seconds: int = Field(default=300, ge=10, le=600)
    api_key: SecretStr | None = Field(default=None, exclude=True, repr=False)

    @classmethod
    def from_environment(cls) -> "VoiceSettings":
        """Read only documented environment variables, never a project .env or saved key."""
        raw: dict[str, object] = {"api_key": os.getenv("OPENAI_API_KEY")}
        for field in cls.model_fields:
            if field != "api_key":
                value = os.getenv(f"PC_MANAGER_VOICE_{field.upper()}")
                if value is not None:
                    raw[field] = value
        return cls.model_validate(raw)
