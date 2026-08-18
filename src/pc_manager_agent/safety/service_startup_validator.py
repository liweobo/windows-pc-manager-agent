"""Independent validator for Stage 4C2 plans and Previews."""

from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.service_startup_actions import (
    ServiceStartupActionPlan,
    ServiceStartupActionPreview,
)


class ServiceStartupSafetyReview(FrozenModel):
    """Final deterministic review consumed by orchestration, never by the LLM."""

    approved: bool
    issues: tuple[str, ...]


class ServiceStartupSafetyValidator:
    """Reject stale, unbacked, over-broad, or insufficiently privileged writes."""

    def review(
        self,
        plan: ServiceStartupActionPlan,
        preview: ServiceStartupActionPreview,
    ) -> ServiceStartupSafetyReview:
        """Validate all cross-model bindings and return explicit fail-closed issues."""
        issues: list[str] = []
        if preview.plan_id != plan.plan_id or preview.transaction_id != plan.transaction_id:
            issues.append("Preview does not belong to the supplied plan")
        if preview.plan_digest != plan.canonical_digest():
            issues.append("Plan digest changed")
        if preview.observation.identity.canonical_digest() != (
            plan.target_identity.canonical_digest()
        ):
            issues.append("Stable service identity changed")
        if preview.observation.startup_configuration != plan.source_configuration:
            issues.append("Source startup configuration changed")
        if preview.target_configuration != plan.target_configuration:
            issues.append("Target startup configuration changed")
        if preview.current_state_digest != plan.expected_state_digest:
            issues.append("Observed state no longer matches the plan")
        if preview.impact.canonical_digest() != plan.expected_impact_digest:
            issues.append("Dependency or runtime impact changed")
        if preview.permissions.canonical_digest() != plan.expected_permission_digest:
            issues.append("Service configuration permission evidence changed")
        if preview.backup_id != plan.backup_id or preview.backup_digest != plan.backup_digest:
            issues.append("Verified backup binding changed")
        if not preview.backup_verified:
            issues.append("Backup verification is incomplete")
        if not preview.safety.allowed:
            issues.append(preview.safety.explanation)
        if not preview.permissions.allows_change:
            issues.append("Ordinary-user SERVICE_CHANGE_CONFIG permission is unavailable")
        if not preview.executable:
            issues.append("Preview is not executable")
        unique = tuple(dict.fromkeys(issues))
        return ServiceStartupSafetyReview(approved=not unique, issues=unique)
