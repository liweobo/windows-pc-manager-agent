"""Read-only impact Preview for one exact startup action."""

from __future__ import annotations

from pc_manager_agent.domain.startup_actions import (
    StartupActionPlan,
    StartupActionPreview,
    StartupBackupReference,
    StartupObservation,
)
from pc_manager_agent.safety.startup_policy import StartupSafetyPolicy


class StartupPreviewEngine:
    """Bind deterministic classification and verified backup to a live observation."""

    def __init__(self, policy: StartupSafetyPolicy) -> None:
        self._policy = policy

    def build(
        self,
        plan: StartupActionPlan,
        observation: StartupObservation,
        backup: StartupBackupReference,
    ) -> StartupActionPreview:
        """Build a digest-bound Preview without performing a startup mutation."""
        if observation.identity.canonical_digest() != plan.target_identity.canonical_digest():
            raise ValueError("Startup observation identity does not match the plan")
        if backup.backup_id != plan.backup_id or backup.payload_digest != plan.backup_digest:
            raise ValueError("Startup backup reference does not match the plan")
        if observation.current_state_digest() != plan.expected_state_digest:
            raise ValueError("Startup state changed before Preview")
        assessment = self._policy.assess(observation, plan.action)
        return StartupActionPreview(
            transaction_id=plan.transaction_id,
            plan_id=plan.plan_id,
            plan_digest=plan.canonical_digest(),
            action=plan.action,
            observation=observation,
            assessment=assessment,
            current_state_digest=observation.current_state_digest(),
            backup_id=backup.backup_id,
            backup_digest=backup.payload_digest,
            backup_verified=backup.verified,
        )
