from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from pc_manager_agent.browser.fake import FakeBrowserAdapter
from pc_manager_agent.confirmation.browser import BrowserConfirmationService
from pc_manager_agent.domain.browser import (
    BrowserActionKind,
    BrowserActionRequest,
    BrowserDecision,
    BrowserPolicyResult,
)
from pc_manager_agent.domain.browser_plans import (
    BrowserConfirmationState,
    BrowserTaskPlan,
)
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.persistence.browser import (
    BrowserAuthorityError,
    BrowserConfirmationRepository,
    BrowserWriteExecutionGuard,
)
from pc_manager_agent.tools.browser_tools import build_browser_registry


def _plan() -> BrowserTaskPlan:
    action = BrowserActionRequest(
        session_id=uuid4(),
        kind=BrowserActionKind.NAVIGATE,
        url="https://example.com/",
        expected_origin="https://example.com",
    )
    return BrowserTaskPlan(
        summary="Navigate",
        user_goal_summary="Read example",
        action=action,
        policy=BrowserPolicyResult(
            decision=BrowserDecision.REQUIRE_CONFIRMATION,
            reason_code="BROWSER_EXACT_ACTION_PLAN_REQUIRED",
            risk_level=RiskLevel.R0,
            rollback_level=RollbackLevel.NONE,
            requires_plan_confirmation=True,
            requires_runtime_confirmation=False,
        ),
        allowed_origin="https://example.com",
        expected_effect="Read one page",
    )


def test_confirmation_is_digest_bound_expiring_and_single_use(tmp_path: Path) -> None:
    repository = BrowserConfirmationRepository(tmp_path / "state.db")
    repository.initialize()
    service = BrowserConfirmationService(repository, 60)
    now = datetime.now(UTC)
    plan = _plan()
    record = service.request(plan, now=now)
    service.resolve(record.confirmation_id, approved=True, now=now)
    service.consume(record.confirmation_id, plan, now=now)
    assert repository.state(record.confirmation_id) is BrowserConfirmationState.CONSUMED
    with pytest.raises(BrowserAuthorityError, match="BINDING_MISMATCH"):
        service.consume(record.confirmation_id, plan, now=now)
    expired = service.request(plan, now=now - timedelta(minutes=2))
    with pytest.raises(BrowserAuthorityError, match="EXPIRED"):
        service.resolve(expired.confirmation_id, approved=True, now=now)
    repository.close()


def test_restart_invalidates_pending_authority(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    first = BrowserConfirmationRepository(path)
    first.initialize()
    record = BrowserConfirmationService(first, 60).request(_plan())
    first.close()
    second = BrowserConfirmationRepository(path)
    second.initialize()
    assert second.state(record.confirmation_id) is BrowserConfirmationState.INVALIDATED
    second.close()


def test_registry_contains_only_five_finite_browser_tools() -> None:
    registry = build_browser_registry(
        FakeBrowserAdapter(),
        write_guard=BrowserWriteExecutionGuard(),
    )
    assert registry.names == (
        "browser.document.download",
        "browser.element.activate",
        "browser.page.navigate",
        "browser.page.observe",
        "browser.session.open",
    )
    assert all(
        "script" not in name and "click" not in name and "http" not in name
        for name in registry.names
    )


def test_invalid_confirmation_ttl_and_uninitialized_repository_fail_closed(tmp_path: Path) -> None:
    repository = BrowserConfirmationRepository(tmp_path / "state.db")
    with pytest.raises(ValueError, match="TTL"):
        BrowserConfirmationService(repository, 0)
    with pytest.raises(BrowserAuthorityError, match="STORAGE_UNAVAILABLE"):
        repository.state(uuid4())


def test_rejection_invalidation_missing_state_and_write_guard_bindings(tmp_path: Path) -> None:
    repository = BrowserConfirmationRepository(tmp_path / "state.db")
    repository.initialize()
    service = BrowserConfirmationService(repository, 60)
    plan = _plan()
    rejected = service.request(plan)
    service.resolve(rejected.confirmation_id, approved=False)
    assert repository.state(rejected.confirmation_id) is BrowserConfirmationState.REJECTED
    assert repository.invalidate_session(plan.action.session_id) == 0
    with pytest.raises(BrowserAuthorityError, match="NOT_FOUND"):
        repository.state(uuid4())

    guard = BrowserWriteExecutionGuard()
    arguments = {"value": "safe"}
    authorization = guard.issue(
        plan_id=plan.plan_id,
        operation_id=plan.action.action_id,
        preview_id=uuid4(),
        tool_name="browser.document.download",
        arguments=arguments,
    )
    with pytest.raises(BrowserAuthorityError, match="TOOL_BINDING"):
        guard.require(authorization, "browser.page.navigate", arguments)
    replay_guard = BrowserWriteExecutionGuard()
    replay = replay_guard.issue(
        plan_id=plan.plan_id,
        operation_id=uuid4(),
        preview_id=uuid4(),
        tool_name="browser.document.download",
        arguments=arguments,
    )
    with pytest.raises(BrowserAuthorityError, match="ARGUMENT_BINDING"):
        replay_guard.require(replay, replay.tool_name, {"value": "changed"})
    with pytest.raises(BrowserAuthorityError, match="REPLAYED"):
        replay_guard.require(replay, replay.tool_name, arguments)
    repository.close()
