from pc_manager_agent.domain.privileged_actions import (
    PrivilegedActionType,
    PrivilegeRequirement,
    PrivilegeResolutionStatus,
)
from pc_manager_agent.privileged.resolver import (
    PrivilegeAssessmentInput,
    PrivilegeRequirementResolver,
)


def _evidence(**updates: object) -> PrivilegeAssessmentInput:
    values: dict[str, object] = {
        "action_type": PrivilegedActionType.SERVICE_STOP,
        "safety_allowed": True,
        "preflight_complete": True,
        "current_process_elevated": False,
        "current_token_has_required_access": False,
        "declared_requirement": PrivilegeRequirement.ELEVATED_ADMIN_REQUIRED,
        "access_failure_code": 5,
    }
    values.update(updates)
    return PrivilegeAssessmentInput.model_validate(values)


def test_safety_block_wins_over_privilege() -> None:
    result = PrivilegeRequirementResolver().resolve(_evidence(safety_allowed=False))
    assert result.status is PrivilegeResolutionStatus.BLOCKED


def test_standard_user_access_uses_existing_path() -> None:
    result = PrivilegeRequirementResolver().resolve(
        _evidence(current_token_has_required_access=True)
    )
    assert result.status is PrivilegeResolutionStatus.NOT_REQUIRED
    assert result.requirement is PrivilegeRequirement.STANDARD_USER


def test_complete_admin_preflight_routes_to_protocol() -> None:
    result = PrivilegeRequirementResolver().resolve(_evidence())
    assert result.status is PrivilegeResolutionStatus.REQUIRED


def test_access_denied_alone_remains_unknown() -> None:
    result = PrivilegeRequirementResolver().resolve(
        _evidence(
            preflight_complete=False,
            declared_requirement=PrivilegeRequirement.UNKNOWN,
        )
    )
    assert result.status is PrivilegeResolutionStatus.UNKNOWN


def test_system_and_elevated_agent_are_blocked() -> None:
    resolver = PrivilegeRequirementResolver()
    system = resolver.resolve(_evidence(declared_requirement=PrivilegeRequirement.SYSTEM_REQUIRED))
    elevated = resolver.resolve(_evidence(current_process_elevated=True))
    assert system.status is PrivilegeResolutionStatus.UNSUPPORTED
    assert elevated.status is PrivilegeResolutionStatus.BLOCKED
