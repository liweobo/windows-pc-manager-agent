"""Explicit capture controller; a release/stop never approves upload or a business action."""

from uuid import UUID

from pc_manager_agent.domain.user_requests import VoiceInteractionContext
from pc_manager_agent.domain.voice import VoiceError
from pc_manager_agent.voice.audio import (
    AudioCaptureService,
    MicrophonePermissionService,
    MicrophoneStatus,
)
from pc_manager_agent.voice.session import VoiceSessionCoordinator


class PushToTalkController:
    """Exactly one hardware owner with safe stop paths independent of database availability."""

    def __init__(
        self,
        capture: AudioCaptureService,
        permission: MicrophonePermissionService,
        coordinator: VoiceSessionCoordinator,
    ) -> None:
        self.capture, self.permission, self.coordinator = capture, permission, coordinator
        self._active: UUID | None = None

    @property
    def active(self) -> bool:
        """Return whether this controller owns a live device activation."""
        return self._active is not None

    def press(
        self,
        context: VoiceInteractionContext,
        *,
        user_gesture: bool,
        visible: bool,
        status: MicrophoneStatus = MicrophoneStatus.UNKNOWN,
    ) -> UUID:
        """Validate explicit activation, journal, then open the device once."""
        if self._active is not None:
            raise VoiceError("VOICE_INPUT_ALREADY_ACTIVE")
        self.permission.require_activation(
            user_gesture=user_gesture, visible=visible, status=status
        )
        reference = self.coordinator.begin(context)
        try:
            self.capture.start()
            self.coordinator.listening(reference)
            self._active = reference
        except Exception:
            self.capture.discard()
            self.coordinator.fail(reference, "MICROPHONE_CAPTURE_FAILED")
            raise
        return reference

    def release(self) -> UUID:
        """Close the microphone before retaining the recording for an upload preview."""
        reference, self._active = self._active, None
        if reference is None:
            raise VoiceError("VOICE_NOT_RECORDING")
        try:
            audio = self.capture.stop()
            self.coordinator.finish_capture(reference, audio)
        except Exception:
            self.capture.discard()
            self.coordinator.fail(reference, "VOICE_CAPTURE_FINALIZATION_FAILED")
            raise
        return reference

    def cancel(self) -> None:
        """Stop hardware first; persistence failure cannot leave the microphone running."""
        self._active = None
        try:
            self.capture.discard()
        finally:
            self.coordinator.cancel()
