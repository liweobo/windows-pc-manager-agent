"""Structural guard that makes Stage 4D1 uninstall execution unrepresentable."""

from pydantic import BaseModel

from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.software_errors import (
    SoftwareAnalysisError,
    SoftwareAnalysisErrorCode,
)
from pc_manager_agent.tools.registry import ToolRegistry

STAGE_4D1_TOOL_NAMES = (
    "software.inventory",
    "software.resolve",
    "software.inspect",
    "software.uninstall_capability",
    "software.uninstall_preview",
)


class SoftwareZeroExecutionGuard:
    """Validate the exact R0 allow-list and every result's zero-execution assertion."""

    def validate_registry(self, registry: ToolRegistry) -> None:
        """Reject added, missing, writable, runtime-confirmed, or rollback-bearing tools."""
        if registry.names != tuple(sorted(STAGE_4D1_TOOL_NAMES)):
            raise SoftwareAnalysisError(
                SoftwareAnalysisErrorCode.ZERO_EXECUTION_VIOLATION,
                "Stage 4D1 tool registry differs from the exact read-only allow-list",
            )
        for name in STAGE_4D1_TOOL_NAMES:
            manifest = registry.manifest(name)
            if (
                manifest.risk_level is not RiskLevel.R0
                or not manifest.read_only
                or manifest.requires_runtime_confirmation
                or manifest.rollback_level is not RollbackLevel.NONE
            ):
                raise SoftwareAnalysisError(
                    SoftwareAnalysisErrorCode.ZERO_EXECUTION_VIOLATION,
                    f"Stage 4D1 tool is not strictly read-only R0: {name}",
                )

    def validate_result(self, result: BaseModel) -> None:
        """Fail if a tool omits or changes the mandatory execution_performed=false field."""
        if getattr(result, "execution_performed", None) is not False:
            raise SoftwareAnalysisError(
                SoftwareAnalysisErrorCode.ZERO_EXECUTION_VIOLATION,
                "Stage 4D1 tool result did not prove zero execution",
            )
