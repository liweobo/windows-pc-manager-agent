from __future__ import annotations

import os
from pathlib import Path

import pytest
from tests.stage4a_support import process_observation

from pc_manager_agent.domain.process_actions import (
    ProcessActionErrorCode,
    ProcessActionType,
    ProcessObservation,
    ProcessSafetyClass,
    ProcessSafetyDecision,
)
from pc_manager_agent.safety.process_policy import ProcessSafetyPolicy


def _policy(*, agent_pids: frozenset[int] = frozenset()) -> ProcessSafetyPolicy:
    return ProcessSafetyPolicy(
        current_owner_sid="S-1-5-21-1000",
        current_session_id=1,
        agent_pids=agent_pids,
        windows_directory=Path("C:/Windows"),
    )


def test_policy_allows_only_current_user_ordinary_processes() -> None:
    result = _policy().assess(
        process_observation(),
        ProcessActionType.REQUEST_GRACEFUL_EXIT,
    )
    assert result.decision is ProcessSafetyDecision.ALLOW
    assert result.safety_class is ProcessSafetyClass.USER_APPLICATION


@pytest.mark.security
@pytest.mark.parametrize(
    ("observation", "expected"),
    [
        (process_observation(pid=os.getpid()), ProcessActionErrorCode.BLOCKED_AGENT_PROCESS),
        (
            process_observation(owner_sid="S-1-5-18"),
            ProcessActionErrorCode.BLOCKED_SYSTEM_PROCESS,
        ),
        (
            process_observation(owner_sid="S-1-5-21-9999"),
            ProcessActionErrorCode.BLOCKED_DIFFERENT_USER,
        ),
        (process_observation(session_id=2), ProcessActionErrorCode.BLOCKED_DIFFERENT_SESSION),
        (
            process_observation(name="lsass.exe", critical=True),
            ProcessActionErrorCode.BLOCKED_SYSTEM_PROCESS,
        ),
        (
            process_observation(service_names=("DemoService",)),
            ProcessActionErrorCode.BLOCKED_SERVICE_PROCESS,
        ),
        (
            process_observation(name="MsMpEng.exe"),
            ProcessActionErrorCode.BLOCKED_SECURITY_PROCESS,
        ),
        (
            process_observation(protection_level=3),
            ProcessActionErrorCode.BLOCKED_UNKNOWN_SENSITIVE,
        ),
    ],
)
def test_policy_blocks_protected_categories(
    observation: ProcessObservation,
    expected: ProcessActionErrorCode,
) -> None:
    agent_pids = frozenset({os.getpid()})
    result = _policy(agent_pids=agent_pids).assess(
        observation,
        ProcessActionType.FORCE_TERMINATE,
    )
    assert result.decision is ProcessSafetyDecision.BLOCK
    assert expected in result.reason_codes


def test_graceful_requires_an_application_window_but_force_is_separate() -> None:
    background = process_observation(window_count=0)
    graceful = _policy().assess(background, ProcessActionType.REQUEST_GRACEFUL_EXIT)
    force = _policy().assess(background, ProcessActionType.FORCE_TERMINATE)
    assert graceful.reason_codes == (ProcessActionErrorCode.UNSUPPORTED_GRACEFUL_EXIT,)
    assert force.decision is ProcessSafetyDecision.ALLOW


def test_application_group_window_support_applies_to_helper_members() -> None:
    helper = process_observation(window_count=0)
    result = _policy().assess(
        helper,
        ProcessActionType.REQUEST_GRACEFUL_EXIT,
        application_has_window=True,
    )
    assert result.decision is ProcessSafetyDecision.ALLOW


def test_windows_directory_and_unknown_owner_are_blocked() -> None:
    system_binary = _policy().assess(
        process_observation(path=Path("C:/Windows/System32/not-listed.exe")),
        ProcessActionType.FORCE_TERMINATE,
    )
    assert system_binary.safety_class is ProcessSafetyClass.SYSTEM_PROCESS
    assert system_binary.decision is ProcessSafetyDecision.BLOCK
    different_owner = _policy().assess(
        process_observation(owner_sid="S-1-5-21-2000"),
        ProcessActionType.FORCE_TERMINATE,
    )
    assert different_owner.safety_class is ProcessSafetyClass.UNKNOWN_SENSITIVE
