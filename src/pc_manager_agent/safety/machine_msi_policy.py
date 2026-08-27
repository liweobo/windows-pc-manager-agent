"""Execution policy for exact machine-scope MSI products routed through Stage 4X3."""

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
    MsiInstallContext,
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


class MachineMsiExecutionPolicy:
    """Allow only ordinary applications/developer tools after machine identity validation."""

    def assess(
        self,
        product: ValidatedMsiProduct,
        analysis: SoftwareSafetyAssessment,
    ) -> MsiExecutionAssessment:
        """Preserve protected-class blocks while allowing the explicit Broker scope."""
        if (
            product.install_context is not MsiInstallContext.MACHINE
            or product.scope is not SoftwareScope.LOCAL_MACHINE
        ):
            return _blocked(analysis.safety_class, "Product is not one exact machine MSI.")
        if (
            analysis.decision
            in {SoftwareSafetyDecision.BLOCKED, SoftwareSafetyDecision.UNSUPPORTED}
            or analysis.safety_class in _BLOCKED_CLASSES
        ):
            return _blocked(
                analysis.safety_class,
                "The deterministic software class is protected or unsupported.",
            )
        if analysis.safety_class is SoftwareSafetyClass.USER_APPLICATION:
            return MsiExecutionAssessment(
                decision=MsiExecutionDecision.ALLOW,
                safety_class=analysis.safety_class,
                risk_level=RiskLevel.R2,
                reasons=("An exact ordinary machine MSI may enter the R3 Broker flow.",),
                evidence=("Fresh machine registration and protected-class policy passed.",),
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
                    "Removal may affect machine-wide projects, runtimes, IDEs, or toolchains.",
                    "The Agent cannot prove that no other software depends on this product.",
                ),
                evidence=("Fresh machine registration and protected-class policy passed.",),
            )
        return _blocked(analysis.safety_class, "This software class has no machine MSI rule.")


def _blocked(safety_class: SoftwareSafetyClass, reason: str) -> MsiExecutionAssessment:
    return MsiExecutionAssessment(
        decision=MsiExecutionDecision.BLOCK,
        safety_class=safety_class,
        risk_level=RiskLevel.R3,
        reasons=(reason,),
        evidence=("Machine MSI identity does not imply safe removal.",),
    )
