"""Read-only impact Preview for controlled service actions."""

from pc_manager_agent.domain.service_actions import (
    ServiceActionPlan,
    ServiceActionPreview,
    ServiceObservation,
    ServicePermissionEvidence,
)
from pc_manager_agent.orchestration.service_dependency_analyzer import ServiceDependencyAnalyzer
from pc_manager_agent.safety.service_policy import ServiceSafetyPolicy


class ServicePreviewEngine:
    """Bind live identity, state, relationships, policy, and permissions."""

    def __init__(
        self,
        policy: ServiceSafetyPolicy,
        dependency_analyzer: ServiceDependencyAnalyzer,
    ) -> None:
        self._policy = policy
        self._dependencies = dependency_analyzer

    def build(
        self,
        plan: ServiceActionPlan,
        observation: ServiceObservation,
        permissions: ServicePermissionEvidence,
    ) -> ServiceActionPreview:
        """Build an immutable Preview without granting execution authority."""
        if observation.identity.canonical_digest() != plan.target_identity.canonical_digest():
            raise ValueError("Service observation does not match the planned identity")
        if observation.state_digest() != plan.expected_state_digest:
            raise ValueError("Service state changed before Preview")
        if observation.dependency_digest() != plan.expected_dependency_digest:
            raise ValueError("Service dependency graph changed before Preview")
        if permissions.canonical_digest() != plan.expected_permission_digest:
            raise ValueError("Service permissions changed before Preview")
        return ServiceActionPreview(
            transaction_id=plan.transaction_id,
            plan_id=plan.plan_id,
            plan_digest=plan.canonical_digest(),
            action=plan.action,
            observation=observation,
            safety=self._policy.assess(observation, plan.action),
            dependencies=self._dependencies.assess(observation, plan.action),
            permissions=permissions,
            current_state_digest=observation.state_digest(),
            risk_level=plan.risk_level,
        )
