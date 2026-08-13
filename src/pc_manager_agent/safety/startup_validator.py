"""Independent structural and policy review for startup action plans."""

from __future__ import annotations

from pydantic import BaseModel

from pc_manager_agent.domain.startup_actions import (
    StartupActionPlan,
    StartupActionPreview,
    StartupSafetyDecision,
)
from pc_manager_agent.tools.registry import ToolRegistry


class StartupSafetyReview(BaseModel):
    """Immutable review result suitable for UI and audit rendering."""

    approved: bool
    issues: tuple[str, ...] = ()


class StartupActionSafetyValidator:
    """Reject unregistered tools and any plan/Preview/backup binding mismatch."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def review(
        self,
        plan: StartupActionPlan,
        preview: StartupActionPreview,
    ) -> StartupSafetyReview:
        """Return a fail-closed independent startup action review."""
        issues: list[str] = []
        tool_name = "startup.disable" if plan.action.value == "DISABLE" else "startup.restore"
        try:
            manifest = self._registry.manifest(tool_name)
        except Exception:
            issues.append("The required narrow startup tool is not registered")
        else:
            if manifest.risk_level != plan.risk_level:
                issues.append("Tool risk does not match the startup plan")
            if manifest.rollback_level != plan.rollback_level:
                issues.append("Tool rollback capability does not match the startup plan")
            if manifest.max_batch_size != 1:
                issues.append("Startup mutations must be single-object operations")
        if preview.plan_id != plan.plan_id or preview.transaction_id != plan.transaction_id:
            issues.append("Preview does not belong to the startup plan")
        if preview.plan_digest != plan.canonical_digest():
            issues.append("Startup plan changed after Preview")
        if preview.action is not plan.action:
            issues.append("Startup action changed after Preview")
        if (
            preview.observation.identity.canonical_digest()
            != plan.target_identity.canonical_digest()
        ):
            issues.append("Startup target identity changed")
        if preview.current_state_digest != plan.expected_state_digest:
            issues.append("Startup configuration state changed")
        if (
            preview.backup_id != plan.backup_id
            or preview.backup_digest != plan.backup_digest
            or not preview.backup_verified
        ):
            issues.append("Exact startup backup is absent, changed, or unverified")
        if preview.assessment.decision is not StartupSafetyDecision.ALLOW:
            issues.append(preview.assessment.explanation)
        return StartupSafetyReview(approved=not issues, issues=tuple(issues))
