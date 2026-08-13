"""Independent semantic and manifest review for service action plans."""

from pydantic import BaseModel, ConfigDict

from pc_manager_agent.domain.risk import RollbackLevel
from pc_manager_agent.domain.service_actions import (
    ServiceActionPlan,
    ServiceActionPreview,
    ServiceActionType,
    ServiceSafetyDecision,
)
from pc_manager_agent.tools.registry import ToolRegistry, ToolRegistryError


class ServiceSafetyReview(BaseModel):
    """Machine-readable fail-closed service review."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    approved: bool
    issues: tuple[str, ...]


class ServiceActionSafetyValidator:
    """Validate exact tools, step sequence, identity bindings, and policy gates."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def review(
        self,
        plan: ServiceActionPlan,
        preview: ServiceActionPreview,
    ) -> ServiceSafetyReview:
        """Return approval only when every deterministic boundary matches."""
        issues: list[str] = []
        expected_tools = {
            ServiceActionType.START: ("system.service.start",),
            ServiceActionType.STOP: ("system.service.stop",),
            ServiceActionType.RESTART: ("system.service.stop", "system.service.start"),
        }[plan.action]
        for name in expected_tools:
            try:
                manifest = self._registry.manifest(name)
            except ToolRegistryError as exc:
                issues.append(f"Required service tool is not registered: {exc}")
                continue
            if (
                manifest.read_only
                or not manifest.supports_preview
                or not manifest.requires_runtime_confirmation
                or manifest.rollback_level is not RollbackLevel.MANUAL
                or manifest.max_batch_size != 1
                or manifest.risk_level.value != "R2"
            ):
                issues.append(f"Service tool manifest weakens the contract: {name}")
        if (
            preview.plan_id != plan.plan_id
            or preview.transaction_id != plan.transaction_id
            or preview.plan_digest != plan.canonical_digest()
            or preview.action is not plan.action
            or preview.observation.identity.canonical_digest()
            != plan.target_identity.canonical_digest()
            or preview.current_state_digest != plan.expected_state_digest
            or preview.dependencies.graph_digest != plan.expected_dependency_digest
            or preview.permissions.canonical_digest() != plan.expected_permission_digest
        ):
            issues.append("Service Preview is stale or does not match the plan")
        if preview.safety.decision is not ServiceSafetyDecision.ALLOW:
            issues.append(preview.safety.explanation)
        if not preview.dependencies.allowed:
            issues.append(preview.dependencies.explanation)
        if not preview.permissions.allows(plan.action):
            issues.append("Ordinary-user permissions do not allow the exact service action")
        if not preview.executable:
            issues.append("Service Preview is not executable")
        return ServiceSafetyReview(approved=not issues, issues=tuple(dict.fromkeys(issues)))
