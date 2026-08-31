"""Provider-neutral voice data. Audio and transcript bodies are volatile, never audit models."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class VoiceError(RuntimeError):
    """Expose only a stable code at worker and UI boundaries."""

    def __init__(self, code: str) -> None:
        self.code = (
            code if re.fullmatch(r"[A-Z][A-Z0-9_]{0,99}", code) else "VOICE_OPERATION_FAILED"
        )
        super().__init__(self.code)


class VoiceState(StrEnum):
    """Input lifecycle, intentionally without system execution authority."""

    IDLE = "IDLE"
    REQUESTING_PERMISSION = "REQUESTING_PERMISSION"
    LISTENING = "LISTENING"
    FINALIZING_AUDIO = "FINALIZING_AUDIO"
    AWAITING_DISCLOSURE = "AWAITING_DISCLOSURE"
    TRANSCRIBING = "TRANSCRIBING"
    REVIEWING_TRANSCRIPT = "REVIEWING_TRANSCRIPT"
    ROUTING = "ROUTING"
    PROCESSING = "PROCESSING"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"


class ConfidenceLevel(StrEnum):
    """Recognition quality is not permission or business risk."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


class FrozenVoiceModel(BaseModel):
    """Strict immutable boundary model."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, allow_inf_nan=False, hide_input_in_errors=True
    )


class InternalAudioFormat(FrozenVoiceModel):
    """V1 accepts one tested uncompressed PCM format, not codecs or executable decoders."""

    sample_rate: Literal[24000] = 24000
    channels: Literal[1] = 1
    sample_width: Literal[2] = 2
    encoding: Literal["signed_pcm_little_endian"] = "signed_pcm_little_endian"


class CapturedAudio(FrozenVoiceModel):
    """Bounded immutable PCM in memory; repr/JSON never exposes recording bytes."""

    capture_id: UUID = Field(default_factory=uuid4)
    format: InternalAudioFormat = Field(default_factory=InternalAudioFormat)
    pcm: bytes = Field(min_length=2, max_length=6 * 1024**2, exclude=True, repr=False)

    @model_validator(mode="after")
    def require_complete_frames(self) -> CapturedAudio:
        """Reject truncated samples and the hard duration limit independently of byte capacity."""
        if len(self.pcm) % 2 or self.duration_seconds > 120:
            raise ValueError("Invalid or oversized PCM frames")
        return self

    @property
    def duration_seconds(self) -> float:
        """Derive duration from samples, never a provider/user claim."""
        return len(self.pcm) / 48000

    @property
    def digest(self) -> str:
        """Bind disclosure to the exact PCM content and fixed format."""
        return hashlib.sha256(b"pcm_s16le_24000_mono\0" + self.pcm).hexdigest()


class SpeechToTextResult(FrozenVoiceModel):
    """Untrusted STT output. Absent calibrated confidence remains None."""

    text: str = Field(min_length=1, max_length=4000, exclude=True, repr=False)
    language: str | None = Field(default=None, max_length=20, pattern=r"^[a-zA-Z-]+$")
    confidence: float | None = Field(default=None, ge=0, le=1)
    confidence_evidence: Literal["not_provided", "provider_calibrated"] = "not_provided"
    is_final: bool
    provider: str = Field(max_length=40, pattern=r"^[a-zA-Z0-9_-]+$")
    provider_request_id: str | None = Field(
        default=None, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$"
    )

    @model_validator(mode="after")
    def require_confidence_evidence(self) -> SpeechToTextResult:
        """Do not accept a fabricated confidence number without declared provider evidence."""
        if (self.confidence is None) != (self.confidence_evidence == "not_provided"):
            raise ValueError("Confidence must match actual provider evidence")
        return self


class VoiceTranscript(FrozenVoiceModel):
    """One final transcript version, volatile and separate from a consumed business request."""

    session_id: UUID
    text: str = Field(min_length=1, max_length=4000, exclude=True, repr=False)
    confidence_level: ConfidenceLevel
    language: str | None = None
    edited: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SpeechAudio(FrozenVoiceModel):
    """Bounded synthesized PCM; synthesis cannot start playback or the microphone."""

    pcm: bytes = Field(min_length=2, max_length=6 * 1024**2, exclude=True, repr=False)
    provider: str = Field(max_length=40, pattern=r"^[a-zA-Z0-9_-]+$")

    @model_validator(mode="after")
    def validate_pcm(self) -> SpeechAudio:
        """Require complete fixed-format frames and at most two minutes of output."""
        if len(self.pcm) % 2 or len(self.pcm) > 120 * 48000:
            raise ValueError("Invalid synthesized PCM")
        return self


def transcript_digest(session_id: UUID, text: str) -> str:
    """Salt the correlation hash per session; it is never an authentication proof."""
    return hashlib.sha256(session_id.bytes + text.encode("utf-8")).hexdigest()
