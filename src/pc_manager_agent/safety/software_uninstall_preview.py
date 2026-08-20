"""Build an expiring, immutable and permanently non-executable software Preview."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from pc_manager_agent.domain.risk import RollbackLevel
from pc_manager_agent.domain.software_uninstall_analysis import (
    CapabilitySupport,
    NormalizedInstalledSoftware,
    RawInstalledSoftwareEntry,
    SoftwareSafetyDecision,
    SoftwareUninstallAnalysisPlan,
    SoftwareUninstallPreview,
)
from pc_manager_agent.orchestration.software_capability import UninstallCapabilityResolver
from pc_manager_agent.orchestration.software_impact_analyzer import SoftwareImpactAnalyzer
from pc_manager_agent.safety.software_uninstall_policy import SoftwareUninstallSafetyPolicy
from pc_manager_agent.tools.manifest import CancellationToken


class SoftwareUninstallPreviewEngine:
    """Compose policy, capability, and impact evidence without any execution dependency."""

    def __init__(
        self,
        policy: SoftwareUninstallSafetyPolicy,
        capability: UninstallCapabilityResolver,
        impact: SoftwareImpactAnalyzer,
        ttl_seconds: int = 300,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._policy = policy
        self._capability = capability
        self._impact = impact
        self._ttl_seconds = ttl_seconds
        self._now = now or (lambda: datetime.now(UTC))

    def build(
        self,
        plan: SoftwareUninstallAnalysisPlan,
        software: NormalizedInstalledSoftware,
        raw: RawInstalledSoftwareEntry,
        cancellation: CancellationToken,
    ) -> SoftwareUninstallPreview:
        """Create a Preview whose schema fixes execution flags to false."""
        safety = self._policy.assess(software)
        capability = self._capability.resolve(software, raw)
        impact = self._impact.analyze(software, safety, cancellation)
        blocked = (
            safety.decision in {SoftwareSafetyDecision.BLOCKED, SoftwareSafetyDecision.UNSUPPORTED}
            or capability.support is not CapabilitySupport.METADATA_SUPPORTED
        )
        if safety.decision is SoftwareSafetyDecision.BLOCKED:
            stop_reason = "Safety policy blocks this protected or unknown target."
        elif capability.support is not CapabilitySupport.METADATA_SUPPORTED:
            stop_reason = (
                "No single supported uninstall capability can be established from metadata."
            )
        else:
            stop_reason = (
                "Stage 4D1 ends after target understanding; uninstall execution is absent."
            )
        generated = self._now()
        future_recovery = (
            RollbackLevel.NONE
            if safety.decision is SoftwareSafetyDecision.BLOCKED
            else RollbackLevel.MANUAL
        )
        return SoftwareUninstallPreview(
            plan_id=plan.plan_id,
            plan_digest=plan.canonical_digest(),
            generated_at=generated,
            expires_at=generated + timedelta(seconds=self._ttl_seconds),
            target=software,
            identity_digest=software.identity.canonical_digest(),
            metadata_digest=software.metadata_digest(),
            capability=capability,
            capability_digest=capability.canonical_digest(),
            safety=safety,
            impact=impact,
            future_recovery_level=future_recovery,
            blocked=blocked,
            stop_reason=stop_reason,
        )
