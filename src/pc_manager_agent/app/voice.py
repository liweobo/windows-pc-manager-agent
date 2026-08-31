"""Main-only speech composition. The Broker never imports this module."""

import os
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.audit.voice import VoiceAudit
from pc_manager_agent.config.voice import VoiceSettings
from pc_manager_agent.orchestration.user_requests import UserRequestDispatcher
from pc_manager_agent.persistence.voice import VoiceTranscriptConsumptionStore
from pc_manager_agent.providers.speech_to_text.openai import OpenAISpeechToTextProvider
from pc_manager_agent.providers.text_to_speech.openai import OpenAITextToSpeechProvider
from pc_manager_agent.voice.providers import VoiceProviderService
from pc_manager_agent.voice.routing import VoiceIntentRouter
from pc_manager_agent.voice.session import VoiceSessionCoordinator


@dataclass(frozen=True)
class VoiceServices:
    """One application-wide input owner; mirrored widgets never create independent sessions."""

    coordinator: VoiceSessionCoordinator
    providers: VoiceProviderService
    router: VoiceIntentRouter

    def close(self) -> None:
        """Close journal handles only after capture/playback and provider workers have stopped."""
        self.coordinator.store.close()

    def reconfigure(self, settings: VoiceSettings) -> "VoiceServices":
        """Replace adapter settings after the UI has cancelled all work; no key is persisted."""
        self.coordinator.cancel()
        self.providers.stop_speech()
        self.coordinator.settings = settings
        enabled = settings.provider == "openai"
        providers = VoiceProviderService(
            self.coordinator,
            OpenAISpeechToTextProvider(settings) if enabled else None,
            OpenAITextToSpeechProvider(settings) if enabled else None,
        )
        return VoiceServices(self.coordinator, providers, self.router)


def build_voice_services(
    directory: Path, audit: AuditRepository, settings: VoiceSettings | None = None
) -> VoiceServices:
    """Initialize metadata-only storage and disabled-by-default adapters, without network/audio."""
    settings = settings or VoiceSettings.from_environment()
    store = VoiceTranscriptConsumptionStore(directory / "voice.sqlite3", uuid4())
    commit = os.getenv("PC_MANAGER_GIT_COMMIT", "")
    validated_commit = commit if re.fullmatch(r"[0-9a-fA-F]{7,40}", commit) else None
    coordinator = VoiceSessionCoordinator(store, VoiceAudit(audit, validated_commit), settings)
    enabled = settings.provider == "openai"
    providers = VoiceProviderService(
        coordinator,
        OpenAISpeechToTextProvider(settings) if enabled else None,
        OpenAITextToSpeechProvider(settings) if enabled else None,
    )
    return VoiceServices(
        coordinator, providers, VoiceIntentRouter(coordinator, UserRequestDispatcher())
    )


def audio_process_is_elevated() -> bool:
    """Fail closed off Windows or when token information is unavailable; never request UAC."""
    if os.name != "nt":
        return True
    from pc_manager_agent.platform_support.windows.office_files import office_main_is_elevated

    return office_main_is_elevated()
