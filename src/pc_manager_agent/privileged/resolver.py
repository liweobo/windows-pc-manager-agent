"""Deterministic privilege routing that always runs after safety review."""

from __future__ import annotations

from pydantic import Field

from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.privileged_actions import (
    PrivilegedActionType,
    PrivilegeRequirement,
    PrivilegeResolution,
    PrivilegeResolutionStatus,
)


class PrivilegeAssessmentInput(FrozenModel):
    """Action-specific evidence supplied to the privilege resolver."""

    action_type: PrivilegedActionType
    safety_allowed: bool
    preflight_complete: bool
    current_process_elevated: bool
    current_token_has_required_access: bool
    declared_requirement: PrivilegeRequirement
    access_failure_code: int | None = Field(default=None, ge=0)


class PrivilegeRequirementResolver:
    """Separate permission routing from deterministic safety classification."""

    def resolve(self, evidence: PrivilegeAssessmentInput) -> PrivilegeResolution:
        """Resolve one exact action without treating AccessDenied as elevation authority."""
        if not evidence.safety_allowed:
            return PrivilegeResolution(
                status=PrivilegeResolutionStatus.BLOCKED,
                requirement=evidence.declared_requirement,
                safety_allowed=False,
                preflight_complete=evidence.preflight_complete,
                access_failure_code=evidence.access_failure_code,
                reason_code="SAFETY_BLOCKED",
                explanation=(
                    "Deterministic safety policy blocked the target before privilege routing"
                ),
            )
        if evidence.current_process_elevated:
            return PrivilegeResolution(
                status=PrivilegeResolutionStatus.BLOCKED,
                requirement=evidence.declared_requirement,
                safety_allowed=True,
                preflight_complete=evidence.preflight_complete,
                access_failure_code=evidence.access_failure_code,
                reason_code="ELEVATED_AGENT_BLOCKED",
                explanation="The main Agent must run as a standard user",
            )
        if evidence.declared_requirement in {
            PrivilegeRequirement.SYSTEM_REQUIRED,
            PrivilegeRequirement.TRUSTED_INSTALLER_OR_UNSUPPORTED,
        }:
            return PrivilegeResolution(
                status=PrivilegeResolutionStatus.UNSUPPORTED,
                requirement=evidence.declared_requirement,
                safety_allowed=True,
                preflight_complete=evidence.preflight_complete,
                access_failure_code=evidence.access_failure_code,
                reason_code="PRIVILEGE_LEVEL_UNSUPPORTED",
                explanation=(
                    "SYSTEM and TrustedInstaller requirements are not obtainable by the Agent"
                ),
            )
        if evidence.current_token_has_required_access:
            return PrivilegeResolution(
                status=PrivilegeResolutionStatus.NOT_REQUIRED,
                requirement=PrivilegeRequirement.STANDARD_USER,
                safety_allowed=True,
                preflight_complete=evidence.preflight_complete,
                reason_code="STANDARD_USER_SUFFICIENT",
                explanation="The existing ordinary-user access is sufficient",
            )
        if (
            evidence.declared_requirement is PrivilegeRequirement.ELEVATED_ADMIN_REQUIRED
            and evidence.preflight_complete
        ):
            return PrivilegeResolution(
                status=PrivilegeResolutionStatus.REQUIRED,
                requirement=PrivilegeRequirement.ELEVATED_ADMIN_REQUIRED,
                safety_allowed=True,
                preflight_complete=True,
                access_failure_code=evidence.access_failure_code,
                reason_code="ADMINISTRATOR_REQUIRED",
                explanation=(
                    "Action-specific evidence requires Administrator; this does not guarantee "
                    "that policy or the future Broker will allow execution"
                ),
            )
        return PrivilegeResolution(
            status=PrivilegeResolutionStatus.UNKNOWN,
            requirement=PrivilegeRequirement.UNKNOWN,
            safety_allowed=True,
            preflight_complete=evidence.preflight_complete,
            access_failure_code=evidence.access_failure_code,
            reason_code="PRIVILEGE_UNCERTAIN",
            explanation=(
                "Access failure alone cannot distinguish Administrator, policy, ACL, or an "
                "unsupported operation"
            ),
        )
