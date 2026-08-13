"""Security regression tests for protected services and write capabilities."""

from pathlib import Path

import pytest
from tests.stage4c1_support import FakeServicePlatform, service_observation

from pc_manager_agent.domain.service_actions import ServiceActionType, ServiceState
from pc_manager_agent.orchestration.service_target_resolver import ServiceTargetResolver
from pc_manager_agent.safety.service_policy import ServiceSafetyPolicy
from pc_manager_agent.tools.registry import ToolRegistry, UnknownToolError, WriteAuthorizationError
from pc_manager_agent.tools.system_tools.service_actions import StopServiceTool


@pytest.mark.security
def test_display_name_collision_is_never_execution_identity(tmp_path: Path) -> None:
    binary = tmp_path / "demo.exe"
    binary.touch()
    first = service_observation(binary, service_name="One", display_name="Shared")
    second = service_observation(binary, service_name="Two", display_name="Shared")
    platform = FakeServicePlatform(first)
    platform.list_services = lambda max_items=5000: (first, second)[:max_items]  # type: ignore[method-assign]
    resolver = ServiceTargetResolver(platform)
    with pytest.raises(RuntimeError, match="Multiple services"):
        resolver.resolve_query("Shared")
    assert resolver.resolve_query("One").identity.service_name == "One"


@pytest.mark.security
@pytest.mark.parametrize(
    ("name", "service_type", "account", "publisher", "verified"),
    (
        ("WinDefend", 0x10, r"DESKTOP\alice", "Example", True),
        ("DriverDemo", 0x1, r"DESKTOP\alice", "Example", True),
        ("SystemDemo", 0x10, "LocalSystem", "Example", True),
        ("UnknownDemo", 0x10, r"DESKTOP\alice", None, False),
    ),
)
def test_protected_or_unknown_services_are_blocked(
    tmp_path: Path,
    name: str,
    service_type: int,
    account: str,
    publisher: str | None,
    verified: bool,
) -> None:
    binary = tmp_path / "demo.exe"
    binary.touch()
    policy = ServiceSafetyPolicy(
        current_username=r"DESKTOP\alice",
        agent_root=tmp_path / "agent",
        windows_directory=tmp_path / "Windows",
    )
    result = policy.assess(
        service_observation(
            binary,
            service_name=name,
            service_type=service_type,
            account=account,
            publisher=publisher,
            publisher_verified=verified,
        ),
        ServiceActionType.STOP,
    )
    assert result.decision.value == "BLOCK"


@pytest.mark.security
def test_service_tool_cannot_execute_without_durable_confirmation(tmp_path: Path) -> None:
    binary = tmp_path / "demo.exe"
    binary.touch()
    observation = service_observation(binary, state=ServiceState.RUNNING)
    platform = FakeServicePlatform(observation)
    registry = ToolRegistry()
    registry.register(StopServiceTool(platform))
    arguments = {
        "transaction_id": "d29d59ae-b270-4a50-bd76-a020f14cfe70",
        "step": "STOP",
        "identity": observation.identity.model_dump(mode="json"),
        "expected_configuration_digest": observation.identity.canonical_digest(),
        "expected_state": "RUNNING",
        "timeout_seconds": 5,
    }
    with pytest.raises(WriteAuthorizationError):
        registry.execute("system.service.stop", arguments)
    assert platform.calls == []


@pytest.mark.security
@pytest.mark.parametrize(
    "name",
    (
        "service.disable",
        "service.force_stop",
        "service.delete",
        "service.kill",
        "service.stop_dependents",
    ),
)
def test_hallucinated_service_tools_are_not_registered(name: str) -> None:
    with pytest.raises(UnknownToolError):
        ToolRegistry().manifest(name)
