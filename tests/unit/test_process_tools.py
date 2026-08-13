from __future__ import annotations

from pc_manager_agent.domain.process_actions import (
    ProcessActionRequest,
    ProcessActionType,
    ProcessMemberResultState,
)
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.tools.system_tools.process_actions import (
    ForceTerminateProcessTool,
    RequestProcessExitTool,
)
from tests.stage4a_support import FakeProcessPlatform, process_observation


def _request(action: ProcessActionType, count: int = 1) -> ProcessActionRequest:
    identities = tuple(
        process_observation(pid=4_001 + index, create_second=index + 1).identity
        for index in range(count)
    )
    from hashlib import sha256

    digest = sha256(
        "\n".join(sorted(identity.canonical_digest() for identity in identities)).encode()
    ).hexdigest()
    return ProcessActionRequest(
        action=action,
        identities=identities,
        target_set_digest=digest,
        timeout_seconds=1,
    )


def test_process_manifests_are_narrow_irreversible_and_double_confirmed() -> None:
    platform = FakeProcessPlatform((process_observation(),))
    graceful = RequestProcessExitTool(platform).manifest
    force = ForceTerminateProcessTool(platform).manifest
    assert graceful.name == "system.process.request_exit"
    assert graceful.risk_level is RiskLevel.R2
    assert force.name == "system.process.force_terminate"
    assert force.risk_level is RiskLevel.R2_HIGH_IMPACT
    for manifest in (graceful, force):
        assert not manifest.read_only
        assert manifest.requires_confirmation
        assert manifest.requires_runtime_confirmation
        assert manifest.rollback_level is RollbackLevel.NONE
        assert manifest.irreversible
        assert manifest.max_batch_size == 20


def test_graceful_cancel_does_not_claim_undo() -> None:
    platform = FakeProcessPlatform((process_observation(),))
    token = CancellationToken()
    token.cancel()
    result = RequestProcessExitTool(platform).execute(
        _request(ProcessActionType.REQUEST_GRACEFUL_EXIT), token
    )
    assert result.members[0].state is ProcessMemberResultState.CANCELLED_WAITING
    assert platform.graceful_calls == []


def test_force_stops_after_unexpected_failure_and_reports_remaining_members() -> None:
    platform = FakeProcessPlatform((process_observation(),))
    platform.force_state = ProcessMemberResultState.FAILED
    result = ForceTerminateProcessTool(platform).execute(
        _request(ProcessActionType.FORCE_TERMINATE, count=2), CancellationToken()
    )
    assert [item.state for item in result.members] == [
        ProcessMemberResultState.FAILED,
        ProcessMemberResultState.NOT_ATTEMPTED,
    ]
    assert len(result.members) == 2
