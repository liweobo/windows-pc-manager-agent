"""Read-only Preview builder for backed-up service startup configuration changes."""

from pc_manager_agent.domain.service_actions import (
    ServiceObservation,
    ServiceStartupConfiguration,
)
from pc_manager_agent.domain.service_startup_actions import (
    ServiceStartupActionPlan,
    ServiceStartupActionPreview,
    ServiceStartupBackupReference,
    ServiceStartupPermissionEvidence,
)
from pc_manager_agent.safety.service_startup_policy import (
    ServiceStartupSafetyPolicy,
    build_service_startup_impact,
)


class ServiceStartupPreviewEngine:
    """Build a deterministic Preview from fresh local evidence only."""

    def __init__(self, policy: ServiceStartupSafetyPolicy) -> None:
        self._policy = policy

    def build(
        self,
        plan: ServiceStartupActionPlan,
        observation: ServiceObservation,
        target: ServiceStartupConfiguration,
        permissions: ServiceStartupPermissionEvidence,
        backup: ServiceStartupBackupReference,
    ) -> ServiceStartupActionPreview:
        """Bind plan, source, target, impact, permission, and verified backup digests."""
        return ServiceStartupActionPreview(
            transaction_id=plan.transaction_id,
            plan_id=plan.plan_id,
            plan_digest=plan.canonical_digest(),
            action=plan.action,
            observation=observation,
            target_configuration=target,
            safety=self._policy.assess(observation, plan.action, target),
            impact=build_service_startup_impact(observation),
            permissions=permissions,
            current_state_digest=observation.state_digest(),
            backup_id=backup.backup_id,
            backup_digest=backup.payload_digest,
            backup_verified=backup.verified,
        )
