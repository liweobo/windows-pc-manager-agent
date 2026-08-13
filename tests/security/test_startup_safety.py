"""Static and dynamic Stage 4B boundaries against scope expansion and shell fallback."""

from __future__ import annotations

from pathlib import Path

import pytest

from pc_manager_agent.domain.startup_actions import StartupActionRequest, StartupActionType
from pc_manager_agent.tools.registry import ToolRegistry, UnknownToolError


def test_hallucinated_startup_and_registry_tools_are_not_registered() -> None:
    registry = ToolRegistry()
    for name in (
        "registry.delete",
        "registry.set",
        "startup.disable_all",
        "startup.delete",
        "startup.manage_arbitrary",
    ):
        with pytest.raises(UnknownToolError):
            registry.manifest(name)


def test_startup_request_schema_has_no_arbitrary_registry_or_path_argument() -> None:
    fields = set(StartupActionRequest.model_fields)
    assert fields == {
        "action",
        "identity",
        "expected_state_digest",
        "backup_id",
        "backup_digest",
    }
    assert {item.value for item in StartupActionType} == {"DISABLE", "RESTORE"}


def test_startup_production_code_has_no_shell_or_generic_registry_fallback() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "pc_manager_agent"
    selected = (
        root / "platform_support" / "windows" / "startup_management.py",
        root / "tools" / "system_tools" / "startup_actions.py",
        root / "orchestration" / "startup_actions.py",
    )
    forbidden = (
        "subprocess",
        "shell=true",
        "powershell",
        "cmd.exe",
        "reg.exe",
        "createkey",
        "deletekey",
        "winreg.setvalue",
        "winreg.deletevalue",
    )
    for path in selected:
        content = path.read_text(encoding="utf-8").casefold()
        assert all(value not in content for value in forbidden), path


def test_platform_write_location_is_compile_time_fixed_to_hkcu_run() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "pc_manager_agent"
        / "platform_support"
        / "windows"
        / "startup_management.py"
    )
    content = path.read_text(encoding="utf-8")
    assert 'detail.hive != "HKCU"' in content
    assert "detail.key_path != _RUN_KEY" in content
    assert "RegOpenKeyTransactedW" in content
    assert "RegCreateKeyTransactedW" not in content


def test_exact_backup_fields_are_not_public_tool_or_audit_inputs() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "pc_manager_agent"
    tool = (root / "tools" / "system_tools" / "startup_actions.py").read_text(encoding="utf-8")
    audit = (root / "audit" / "startup_actions.py").read_text(encoding="utf-8")
    for field in ("registry_value_data_b64", "approval_data_b64", "shortcut_data_b64"):
        assert field not in tool
        assert field not in audit
