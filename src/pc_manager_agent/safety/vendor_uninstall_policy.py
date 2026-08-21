"""Execution-specific default-deny policy for Stage 4D2B Vendor uninstall."""

from __future__ import annotations

from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.software_uninstall_analysis import (
    SoftwareSafetyAssessment,
    SoftwareSafetyClass,
    SoftwareSafetyDecision,
)
from pc_manager_agent.domain.system_diagnostics import SoftwareScope
from pc_manager_agent.domain.vendor_uninstall import (
    VendorExecutionAssessment,
    VendorExecutionDecision,
    VendorUninstallerIdentity,
)

_BLOCKED_CLASSES = frozenset(
    {
        SoftwareSafetyClass.SHARED_RUNTIME,
        SoftwareSafetyClass.DATABASE_SERVER,
        SoftwareSafetyClass.BACKGROUND_PLATFORM,
        SoftwareSafetyClass.DEVICE_DRIVER,
        SoftwareSafetyClass.HARDWARE_UTILITY,
        SoftwareSafetyClass.WINDOWS_COMPONENT,
        SoftwareSafetyClass.WINDOWS_FEATURE,
        SoftwareSafetyClass.SECURITY_SOFTWARE,
        SoftwareSafetyClass.VPN_OR_NETWORK_COMPONENT,
        SoftwareSafetyClass.AGENT_COMPONENT,
        SoftwareSafetyClass.ENTERPRISE_MANAGED,
        SoftwareSafetyClass.PACKAGE_MANAGER,
        SoftwareSafetyClass.UNKNOWN,
    }
)


class VendorUninstallExecutionPolicy:
    """Combine Stage 4D1 class and executable trust without weakening either decision."""

    def assess(
        self,
        scope: SoftwareScope,
        analysis: SoftwareSafetyAssessment,
        identity: VendorUninstallerIdentity,
    ) -> VendorExecutionAssessment:
        """Allow only current-user user apps and selected developer software."""
        if scope is not SoftwareScope.CURRENT_USER:
            return _blocked(analysis.safety_class, "Machine-wide Vendor uninstall is unsupported.")
        if identity.trust.decision.value != "trusted_for_execution":
            return _blocked(
                analysis.safety_class,
                "Executable trust evidence is insufficient for execution.",
            )
        if (
            analysis.decision
            in {SoftwareSafetyDecision.BLOCKED, SoftwareSafetyDecision.UNSUPPORTED}
            or analysis.safety_class in _BLOCKED_CLASSES
        ):
            return _blocked(
                analysis.safety_class,
                "The deterministic software class is protected or Preview-only.",
            )
        if analysis.safety_class is SoftwareSafetyClass.USER_APPLICATION:
            return VendorExecutionAssessment(
                decision=VendorExecutionDecision.ALLOW,
                safety_class=analysis.safety_class,
                risk_level=RiskLevel.R2,
                reasons=(
                    "A trusted current-user application may enter the two-confirmation flow.",
                ),
                evidence=("Stage 4D1 policy and all Vendor executable gates passed.",),
            )
        if analysis.safety_class in {
            SoftwareSafetyClass.DEVELOPER_TOOL,
            SoftwareSafetyClass.DEVELOPER_RUNTIME,
        }:
            return VendorExecutionAssessment(
                decision=VendorExecutionDecision.ALLOW,
                safety_class=analysis.safety_class,
                risk_level=RiskLevel.R2_HIGH_IMPACT,
                reasons=(
                    "Removal may affect projects, IDEs, interpreters, runtimes, or toolchains.",
                    "The Agent cannot prove that other software has no dependency on this target.",
                ),
                evidence=("Stage 4D1 policy and all Vendor executable gates passed.",),
            )
        return _blocked(
            analysis.safety_class,
            "This software class has no Stage 4D2B execution rule.",
        )


def _blocked(safety_class: SoftwareSafetyClass, reason: str) -> VendorExecutionAssessment:
    """Build one non-executable R3 policy result for UI explanation."""
    return VendorExecutionAssessment(
        decision=VendorExecutionDecision.BLOCK,
        safety_class=safety_class,
        risk_level=RiskLevel.R3,
        reasons=(reason,),
        evidence=("Vendor capability does not imply that removal is safe.",),
    )
