"""Finite in-memory audio capture contracts and buffers; no device opens at import/construction."""

import io
import wave
from collections.abc import Callable
from enum import StrEnum
from typing import Protocol

from pc_manager_agent.config.voice import VoiceSettings
from pc_manager_agent.domain.voice import CapturedAudio, VoiceError


class MicrophoneStatus(StrEnum):
    """Unknown permission is not granted; actual device open errors remain authoritative."""

    UNKNOWN = "UNKNOWN"
    AVAILABLE = "AVAILABLE"
    DENIED = "DENIED"
    MISSING = "MISSING"


class MicrophonePermissionService:
    """Require an explicit visible user gesture in a standard-user Main process."""

    def __init__(self, is_elevated: Callable[[], bool]) -> None:
        self._is_elevated = is_elevated

    def require_activation(
        self, *, user_gesture: bool, visible: bool, status: MicrophoneStatus
    ) -> None:
        """No automatic probe, retry, privilege escalation or model-driven recording."""
        if not user_gesture or not visible:
            raise VoiceError("VOICE_EXPLICIT_VISIBLE_ACTIVATION_REQUIRED")
        if self._is_elevated():
            raise VoiceError("VOICE_ELEVATED_AUDIO_BLOCKED")
        if status is MicrophoneStatus.DENIED:
            raise VoiceError("MICROPHONE_PERMISSION_DENIED")
        if status is MicrophoneStatus.MISSING:
            raise VoiceError("MICROPHONE_UNAVAILABLE")


class AudioCaptureService(Protocol):
    """Platform-specific bounded device capture, owned only by the UI's PTT controller."""

    def start(self) -> None:
        """Open the explicitly selected device after activation gates."""
        ...

    def stop(self) -> CapturedAudio:
        """Stop hardware and return the complete bounded recording."""
        ...

    def discard(self) -> None:
        """Stop hardware and drop all pending samples, even after an error."""
        ...


class BoundedAudioBuffer:
    """Cap raw PCM before copying; no filesystem, shell, decoder or provider access."""

    def __init__(self, settings: VoiceSettings) -> None:
        self._maximum = min(settings.max_audio_bytes, settings.max_voice_input_seconds * 48000)
        self._samples = bytearray()

    def append(self, samples: bytes) -> None:
        """Fail without retaining an over-limit block; the caller must stop the device."""
        if len(samples) + len(self._samples) > self._maximum:
            raise VoiceError("VOICE_INPUT_LIMIT_REACHED")
        self._samples.extend(samples)

    @property
    def size(self) -> int:
        """Return captured bytes for visible progress only."""
        return len(self._samples)

    def finish(self) -> CapturedAudio:
        """Transfer complete samples then drop the mutable recording buffer."""
        try:
            if not self._samples or len(self._samples) % 2:
                raise VoiceError("VOICE_EMPTY_OR_TRUNCATED_AUDIO")
            return CapturedAudio(pcm=bytes(self._samples))
        finally:
            self.clear()

    def clear(self) -> None:
        """Overwrite this mutable copy then release it; Python/OS-wide erasure is not promised."""
        self._samples[:] = b"\0" * len(self._samples)
        self._samples.clear()


def wav_bytes(audio: CapturedAudio) -> bytes:
    """Encode fixed PCM as a bounded WAV in memory, never a public temporary file."""
    stream = io.BytesIO()
    with wave.open(stream, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(24000)
        writer.writeframes(audio.pcm)
    return stream.getvalue()
