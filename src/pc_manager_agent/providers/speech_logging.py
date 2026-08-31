"""Read-only logging preflight for sensitive speech uploads; never reconfigure global logging."""

import logging

from pc_manager_agent.domain.voice import VoiceError


def require_private_speech_logging() -> None:
    """Refuse SDK debug mode, which can serialize multipart audio in request-option logs."""
    if any(
        logging.getLogger(name).isEnabledFor(logging.DEBUG)
        for name in ("openai", "openai._base_client", "httpx", "httpcore")
    ):
        raise VoiceError("VOICE_VERBOSE_PROVIDER_LOGGING_BLOCKED")
