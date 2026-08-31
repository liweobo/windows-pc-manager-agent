"""Typed aggregate-only voice audit; never serialize user requests or provider responses."""

import hashlib
from typing import Literal
from uuid import UUID

from pc_manager_agent import __version__
from pc_manager_agent.audit.models import AuditEvent
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.voice import ConfidenceLevel, VoiceState


class VoiceAudit:
    """Append safe input metadata to the existing audit viewer."""

    def __init__(self, repository: AuditRepository, git_commit: str | None = None) -> None:
        self._repository = repository
        self._git_commit = git_commit

    def record(
        self,
        reference: UUID,
        state: VoiceState,
        *,
        code: str = "VOICE_STATE_CHANGED",
        count: int = 0,
        confidence: ConfidenceLevel = ConfidenceLevel.UNKNOWN,
        edited: bool = False,
        request_ref: UUID | None = None,
        duration_ms: int | None = None,
    ) -> None:
        """Only UUIDs, enums, counts and fixed codes enter audit, even on provider failure."""
        if not code.isascii() or not code.replace("_", "").isalnum() or len(code) > 100:
            code = "VOICE_OPERATION_FAILED"
        self._repository.record(
            AuditEvent(
                event_type="voice.state",
                app_version=__version__,
                git_commit=self._git_commit,
                risk_level=RiskLevel.R0,
                duration_ms=duration_ms,
                parameters={
                    "reference": str(reference),
                    "state": state.value,
                    "code": code,
                    "count": count,
                    "confidence": confidence.value,
                    "edited": edited,
                    "request_ref": str(request_ref) if request_ref else None,
                    "raw_audio_saved": False,
                },
            )
        )

    def provider_event(
        self,
        reference: UUID,
        purpose: Literal["STT", "TTS"],
        *,
        destination: str,
        approved: bool,
        completed: bool = False,
        count: int = 0,
        trace: str | None = None,
        duration_ms: int = 0,
    ) -> None:
        """Journal exact outbound consent and aggregate outcome without recording any payload."""
        self._repository.record(
            AuditEvent(
                event_type="voice.disclosure",
                app_version=__version__,
                git_commit=self._git_commit,
                risk_level=RiskLevel.R2,
                confirmation_required=True,
                confirmation_result="APPROVED" if approved else "REJECTED",
                duration_ms=duration_ms,
                parameters={
                    "reference": str(reference),
                    "purpose": purpose,
                    "destination_digest": hashlib.sha256(destination.encode()).hexdigest(),
                    "count": count,
                    "completed": completed,
                    "trace_digest": hashlib.sha256(trace.encode()).hexdigest() if trace else None,
                    "raw_audio_saved": False,
                },
            )
        )
