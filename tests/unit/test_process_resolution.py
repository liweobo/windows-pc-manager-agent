from __future__ import annotations

from pathlib import Path

import pytest

from pc_manager_agent.domain.process_actions import (
    ProcessActionErrorCode,
    ProcessTargetQuery,
    ProcessTargetQueryType,
)
from pc_manager_agent.domain.process_errors import ProcessTargetResolutionError
from pc_manager_agent.orchestration.process_target_resolver import ProcessTargetResolver
from tests.stage4a_support import FakeProcessPlatform, process_observation


def test_resolver_groups_same_executable_and_resolves_selected_pid() -> None:
    platform = FakeProcessPlatform(
        (
            process_observation(pid=101),
            process_observation(pid=102, create_second=2),
        )
    )
    resolver = ProcessTargetResolver(platform)
    targets = resolver.resolve(
        ProcessTargetQuery(query_type=ProcessTargetQueryType.NAME, text="demo")
    )
    assert [member.identity.pid for member in targets[0].members] == [101, 102]
    selected = resolver.resolve(
        ProcessTargetQuery(
            query_type=ProcessTargetQueryType.SELECTED_PROCESS,
            pid=101,
            include_application_group=False,
        )
    )
    assert len(selected[0].members) == 1


def test_name_collision_across_executables_is_ambiguous() -> None:
    platform = FakeProcessPlatform(
        (
            process_observation(pid=101, path=Path("C:/Apps/A/demo.exe")),
            process_observation(pid=102, path=Path("C:/Apps/B/demo.exe"), create_second=2),
        )
    )
    with pytest.raises(ProcessTargetResolutionError) as captured:
        ProcessTargetResolver(platform).resolve(
            ProcessTargetQuery(query_type=ProcessTargetQueryType.NAME, text="demo")
        )
    assert captured.value.code is ProcessActionErrorCode.TARGET_AMBIGUOUS


def test_pid_reuse_and_group_membership_change_invalidate_resolution() -> None:
    original = process_observation(pid=101)
    platform = FakeProcessPlatform((original,))
    resolver = ProcessTargetResolver(platform)
    target = resolver.resolve(
        ProcessTargetQuery(query_type=ProcessTargetQueryType.NAME, text="demo")
    )[0]
    platform.observations = (process_observation(pid=101, create_second=2),)
    with pytest.raises(ProcessTargetResolutionError) as captured:
        resolver.re_resolve(target)
    assert captured.value.code is ProcessActionErrorCode.TARGET_GROUP_CHANGED


def test_resolution_returns_not_found_for_inaccessible_target() -> None:
    resolver = ProcessTargetResolver(FakeProcessPlatform(()))
    with pytest.raises(ProcessTargetResolutionError) as captured:
        resolver.resolve(ProcessTargetQuery(query_type=ProcessTargetQueryType.PID, pid=404))
    assert captured.value.code is ProcessActionErrorCode.TARGET_NOT_FOUND
