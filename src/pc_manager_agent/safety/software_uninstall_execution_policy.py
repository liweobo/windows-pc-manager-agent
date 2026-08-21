"""Execution-specific default-deny policy for Stage 4D2A MSI uninstall."""

from __future__ import annotations

from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.software_uninstall_analysis import (
    SoftwareSafetyAssessment,
    SoftwareSafetyClass,
    SoftwareSafetyDecision,
)
from pc_manager_agent.domain.software_uninstall_execution import (
    MsiExecutionAssessment,
    MsiExecutionDecision,
    ValidatedMsiProduct,
)
from pc_manager_agent.domain.system_diagnostics import SoftwareScope

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


class SoftwareUninstallExecutionPolicy:
    """Convert read-only classification into a narrower execution permission."""

    def assess(
        self,
        product: ValidatedMsiProduct,
        analysis: SoftwareSafetyAssessment,
    ) -> MsiExecutionAssessment:
        """Allow only current-user applications and selected developer software."""
        if product.scope is not SoftwareScope.CURRENT_USER:
            return _blocked(
                analysis.safety_class,
                RiskLevel.R3,
                "Machine-wide software is outside the ordinary-user execution boundary.",
            )
        if (
            analysis.decision
            in {SoftwareSafetyDecision.BLOCKED, SoftwareSafetyDecision.UNSUPPORTED}
            or analysis.safety_class in _BLOCKED_CLASSES
        ):
            return _blocked(
                analysis.safety_class,
                RiskLevel.R3,
                "The deterministic software class is protected or unsupported.",
            )
        if analysis.safety_class is SoftwareSafetyClass.USER_APPLICATION:
            return MsiExecutionAssessment(
                decision=MsiExecutionDecision.ALLOW,
                safety_class=analysis.safety_class,
                risk_level=RiskLevel.R2,
                reasons=("A current-user MSI application may be removed after all fresh gates.",),
                evidence=(
                    "Stage 4D1 allowed Preview for a named publisher.",
                    "Windows Installer registration and current-user scope were verified.",
                ),
            )
        if analysis.safety_class in {
            SoftwareSafetyClass.DEVELOPER_TOOL,
            SoftwareSafetyClass.DEVELOPER_RUNTIME,
        }:
            return MsiExecutionAssessment(
                decision=MsiExecutionDecision.ALLOW,
                safety_class=analysis.safety_class,
                risk_level=RiskLevel.R2_HIGH_IMPACT,
                reasons=(
                    "Removal may affect local projects, interpreters, IDEs, or toolchains.",
                    "The Agent cannot prove that no other software depends on this product.",
                ),
                evidence=("Windows Installer registration and current-user scope were verified.",),
            )
        return _blocked(
            analysis.safety_class,
            RiskLevel.R3,
            "This software class has no Stage 4D2A execution rule.",
        )


def _blocked(
    safety_class: SoftwareSafetyClass,
    risk_level: RiskLevel,
    reason: str,
) -> MsiExecutionAssessment:
    return MsiExecutionAssessment(
        decision=MsiExecutionDecision.BLOCK,
        safety_class=safety_class,
        risk_level=risk_level,
        reasons=(reason,),
        evidence=("MSI capability does not imply that removal is safe.",),
    )
