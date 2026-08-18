"""Security boundary tests for Stage 4C2 service startup configuration."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.stage4c1_support import service_observation

from pc_manager_agent.domain.service_actions import (
    ServiceRelation,
    ServiceStartupConfiguration,
    ServiceStartupType,
    ServiceState,
)
from pc_manager_agent.domain.service_startup_actions import (
    ServiceStartupActionType,
    ServiceStartupErrorCode,
)
from pc_manager_agent.safety.service_policy import ServiceSafetyPolicy
from pc_manager_agent.safety.service_startup_policy import ServiceStartupSafetyPolicy
from pc_manager_agent.tools.registry import ToolRegistry, UnknownToolError


@pytest.mark.parametrize(
    ("start_type", "delayed", "expected"),
    [
        (4, False, ServiceStartupErrorCode.DISABLED_TRANSITION_BLOCKED),
        (2, True, ServiceStartupErrorCode.DELAYED_AUTO_UNSUPPORTED),
        (0, False, ServiceStartupErrorCode.DRIVER_OR_BOOT_TYPE_BLOCKED),
        (1, False, ServiceStartupErrorCode.DRIVER_OR_BOOT_TYPE_BLOCKED),
        (99, False, ServiceStartupErrorCode.UNKNOWN_CONFIGURATION),
    ],
)
def test_unsupported_source_types_fail_closed(
    tmp_path: Path,
    start_type: int,
    delayed: bool,
    expected: ServiceStartupErrorCode,
) -> None:
    binary = tmp_path / "vendor.exe"
    binary.write_bytes(b"test")
    observation = service_observation(
        binary,
        start_type=start_type,
        delayed_auto_start=delayed,
    )
    policy = ServiceStartupSafetyPolicy(
        ServiceSafetyPolicy(
            current_username=r"DESKTOP\alice",
            agent_root=tmp_path / "agent",
            windows_directory=tmp_path / "Windows",
        )
    )
    assessment = policy.assess(
        observation,
        ServiceStartupActionType.SET_MANUAL,
        ServiceStartupConfiguration(
            startup_type=ServiceStartupType.MANUAL,
            delayed_auto_start=False,
        ),
    )
    assert not assessment.allowed
    assert expected in assessment.reason_codes


def test_dependency_impact_blocks_write(tmp_path: Path) -> None:
    binary = tmp_path / "vendor.exe"
    binary.write_bytes(b"test")
    observation = service_observation(
        binary,
        start_type=2,
        dependencies=(
            ServiceRelation(
                service_name="Other",
                display_name="Other",
                state=ServiceState.RUNNING,
            ),
        ),
    )
    policy = ServiceStartupSafetyPolicy(
        ServiceSafetyPolicy(
            current_username=r"DESKTOP\alice",
            agent_root=tmp_path / "agent",
            windows_directory=tmp_path / "Windows",
        )
    )
    assessment = policy.assess(
        observation,
        ServiceStartupActionType.SET_MANUAL,
        ServiceStartupConfiguration(
            startup_type=ServiceStartupType.MANUAL,
            delayed_auto_start=False,
        ),
    )
    assert ServiceStartupErrorCode.DEPENDENCY_IMPACT_BLOCKED in assessment.reason_codes


def test_model_invented_generic_service_config_tool_is_not_registered() -> None:
    registry = ToolRegistry()
    with pytest.raises(UnknownToolError):
        registry.manifest("system.service.config.change")


def test_windows_adapter_source_contains_no_shell_or_runtime_control() -> None:
    source = Path("src/pc_manager_agent/platform_support/windows/service_startup.py").read_text(
        encoding="utf-8"
    )
    forbidden = (
        "subprocess",
        "shell=True",
        "powershell",
        "sc.exe",
        "StartService(",
        "ControlService(",
        "ChangeServiceConfig2(",
    )
    assert all(token not in source for token in forbidden)
