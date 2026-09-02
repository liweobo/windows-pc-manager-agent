"""Value-free audit events for user-controlled Memory operations."""

from __future__ import annotations

import hashlib
from uuid import UUID

from pc_manager_agent import __version__
from pc_manager_agent.audit.models import AuditEvent
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.domain.memory import MemoryScope, MemoryWriteDecision
from pc_manager_agent.domain.risk import RiskLevel


class MemoryAuditLogger:
    """Record only IDs, digests, decisions, counts, and fixed reason codes."""

    def __init__(self, repository: AuditRepository, git_commit: str | None = None) -> None:
        self._repository = repository
        self._git_commit = git_commit

    def write_decision(
        self,
        candidate_id: UUID,
        key: str,
        decision: MemoryWriteDecision,
        reason_code: str,
    ) -> None:
        """Audit a candidate decision without serializing its value."""
        self._repository.record(
            AuditEvent(
                event_type="memory.write_decision",
                risk_level=RiskLevel.R1,
                confirmation_required=decision is MemoryWriteDecision.REQUIRE_USER_CONFIRMATION,
                parameters={
                    "candidate_id": str(candidate_id),
                    "key_digest": hashlib.sha256(key.encode()).hexdigest(),
                    "decision": decision.value,
                    "reason_code": reason_code,
                    "memory_value_saved_to_audit": False,
                },
                app_version=__version__,
                git_commit=self._git_commit,
            )
        )

    def changed(
        self,
        action: str,
        *,
        memory_id: UUID | None = None,
        scope: MemoryScope | None = None,
        affected_count: int = 0,
        confirmed: bool = True,
    ) -> None:
        """Audit an explicit local change using aggregate metadata only."""
        self._repository.record(
            AuditEvent(
                event_type="memory.changed",
                risk_level=RiskLevel.R2 if action.startswith("CLEAR") else RiskLevel.R1,
                confirmation_required=action not in {"ENABLED", "DISABLED"},
                confirmation_result="APPROVED" if confirmed else "REJECTED",
                parameters={
                    "action": action,
                    "memory_id": str(memory_id) if memory_id else None,
                    "scope": scope.value if scope else None,
                    "affected_count": affected_count,
                    "memory_value_saved_to_audit": False,
                },
                app_version=__version__,
                git_commit=self._git_commit,
            )
        )
