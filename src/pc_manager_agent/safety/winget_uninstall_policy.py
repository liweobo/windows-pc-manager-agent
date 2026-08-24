"""Execution policy for the narrow Stage 4D2C1 package boundary."""

from __future__ import annotations

from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.software_uninstall_analysis import (
    SoftwareSafetyAssessment,
    SoftwareSafetyClass,
)
from pc_manager_agent.domain.system_diagnostics import SoftwareScope
from pc_manager_agent.domain.winget_uninstall import (
    WingetExecutionAssessment,
    WingetExecutionDecision,
)

_BLOCKED = frozenset(
    {
        SoftwareSafetyClass.SHARED_RUNTIME,
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
_HIGH_IMPACT = frozenset(
    {
        SoftwareSafetyClass.DEVELOPER_RUNTIME,
        SoftwareSafetyClass.DATABASE_SERVER,
        SoftwareSafetyClass.BACKGROUND_PLATFORM,
    }
)


class WingetUninstallPolicy:
    """Default-deny safety policy independent from winget capability metadata."""

    def assess(
        self,
        scope: SoftwareScope,
        analysis: SoftwareSafetyAssessment,
    ) -> WingetExecutionAssessment:
        """Allow only current-user applications and selected high-impact developer targets."""
        if scope is not SoftwareScope.CURRENT_USER:
            return _blocked(analysis, "Machine-wide packages are outside Stage 4D2C1.")
        if analysis.safety_class in _BLOCKED:
            return _blocked(analysis, "This protected software class cannot use winget removal.")
        if analysis.safety_class in _HIGH_IMPACT:
            return WingetExecutionAssessment(
                decision=WingetExecutionDecision.ALLOW,
                safety_class=analysis.safety_class,
                risk_level=RiskLevel.R2_HIGH_IMPACT,
                reasons=("Removal can affect dependent development or background workloads.",),
                evidence=analysis.evidence,
            )
        if analysis.safety_class in {
            SoftwareSafetyClass.USER_APPLICATION,
            SoftwareSafetyClass.DEVELOPER_TOOL,
        }:
            return WingetExecutionAssessment(
                decision=WingetExecutionDecision.ALLOW,
                safety_class=analysis.safety_class,
                risk_level=RiskLevel.R2,
                reasons=(
                    "One current-user application fits the narrow "
                    "reversible-by-reinstall boundary.",
                ),
                evidence=analysis.evidence,
            )
        return _blocked(analysis, "The software class is not explicitly allowed.")


def _blocked(
    analysis: SoftwareSafetyAssessment,
    reason: str,
) -> WingetExecutionAssessment:
    return WingetExecutionAssessment(
        decision=WingetExecutionDecision.BLOCK,
        safety_class=analysis.safety_class,
        risk_level=RiskLevel.R2,
        reasons=(reason,),
        evidence=analysis.evidence,
    )
