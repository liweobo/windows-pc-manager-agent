from pathlib import Path
from uuid import uuid4

import pytest

from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.audit.voice import VoiceAudit
from pc_manager_agent.config.voice import VoiceSettings
from pc_manager_agent.persistence.voice import VoiceTranscriptConsumptionStore
from pc_manager_agent.voice.session import VoiceSessionCoordinator


@pytest.fixture
def coordinator(tmp_path: Path):
    audit = AuditRepository(tmp_path / "voice.db")
    audit.initialize()
    store = VoiceTranscriptConsumptionStore(tmp_path / "voice.db", uuid4())
    settings = VoiceSettings(spoken_response_mode="SHORT")
    result = VoiceSessionCoordinator(store, VoiceAudit(audit), settings)
    yield result
    store.close()
    audit.close()
