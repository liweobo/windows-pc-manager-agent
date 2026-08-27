"""Static Stage 4X3 guards against a generic privileged execution surface."""

from __future__ import annotations

import ast
from pathlib import Path

from pc_manager_agent.domain.privileged_actions import (
    MachineMsiUninstallPayload,
    ServiceStartupTypeChangePayload,
    ServiceStartupTypeRestorePayload,
    StartupMachineDisablePayload,
    StartupMachineRestorePayload,
)

ROOT = Path(__file__).resolve().parents[2]
BROKER_ACTION_SOURCES = (
    ROOT / "src/pc_manager_agent/privileged/dispatcher.py",
    ROOT / "src/pc_manager_agent/privileged/service_startup_handler.py",
    ROOT / "src/pc_manager_agent/privileged/machine_startup_handler.py",
    ROOT / "src/pc_manager_agent/privileged/machine_msi_handler.py",
    ROOT / "src/pc_manager_agent/platform_support/windows/startup_management.py",
)


def test_privileged_payloads_have_no_command_or_generic_registry_value() -> None:
    forbidden = {
        "command",
        "script",
        "shell",
        "executable",
        "args",
        "arguments",
        "registry_key",
        "registry_value",
        "registry_data",
    }
    for model in (
        ServiceStartupTypeChangePayload,
        ServiceStartupTypeRestorePayload,
        StartupMachineDisablePayload,
        StartupMachineRestorePayload,
        MachineMsiUninstallPayload,
    ):
        assert not forbidden & set(model.model_fields)
        assert model.model_config.get("extra") == "forbid"


def test_non_msi_privileged_sources_do_not_spawn_processes_or_import_shells() -> None:
    forbidden_imports = {"subprocess", "pickle", "marshal"}
    forbidden_names = {"eval", "exec", "compile", "system", "popen", "run", "call"}
    for path in BROKER_ACTION_SOURCES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        names = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert not imports & forbidden_imports
        assert not names & forbidden_names


def test_machine_msi_adapter_is_the_only_stage4x3_process_boundary() -> None:
    source = (ROOT / "src/pc_manager_agent/platform_support/windows/msi_uninstall.py").read_text(
        encoding="utf-8"
    )
    assert "shell=False" in source
    assert "sanitized_windows_child_environment" in source
    assert "cmd.exe" not in source.casefold()
    assert "powershell" not in source.casefold()
    assert "runas" not in source.casefold()


def test_broker_rejects_system_integrity() -> None:
    source = (ROOT / "src/pc_manager_agent/broker/main.py").read_text(encoding="utf-8")
    assert 'identity.integrity_level != "HIGH"' in source
    assert "identity.elevated" in source
