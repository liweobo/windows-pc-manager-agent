"""Static and model-level guards against turning Stage 4X2 into an admin shell."""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from pc_manager_agent.broker.main import _harden_process_environment, build_parser
from pc_manager_agent.domain.privileged_actions import (
    MachineMsiUninstallPayload,
    ServiceRestartPayload,
    ServiceStartPayload,
    ServiceStartupTypeChangePayload,
    ServiceStopPayload,
    StartupMachineDisablePayload,
    StartupMachineRestorePayload,
)

ROOT = Path(__file__).resolve().parents[2]
BROKER_SOURCES = (
    ROOT / "src/pc_manager_agent/broker/main.py",
    ROOT / "src/pc_manager_agent/privileged/elevated_broker.py",
    ROOT / "src/pc_manager_agent/privileged/service_handler.py",
    ROOT / "src/pc_manager_agent/platform_support/windows/elevation.py",
)


def test_broker_sources_have_no_generic_execution_primitive() -> None:
    forbidden_calls = {"eval", "exec", "compile", "system", "popen", "run", "call"}
    forbidden_imports = {"subprocess", "pickle", "marshal"}
    for path in BROKER_SOURCES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        attributes = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert not imports & forbidden_imports
        assert not calls & forbidden_calls
        assert not attributes & forbidden_calls


def test_payload_schemas_never_accept_command_script_or_arbitrary_executable() -> None:
    forbidden = {"command", "script", "shell", "executable", "args", "arguments"}
    for model in (
        ServiceStartPayload,
        ServiceStopPayload,
        ServiceRestartPayload,
        ServiceStartupTypeChangePayload,
        StartupMachineDisablePayload,
        StartupMachineRestorePayload,
        MachineMsiUninstallPayload,
    ):
        assert not forbidden & set(model.model_fields)


def test_broker_cli_contains_only_opaque_routing_and_process_binding() -> None:
    parser = build_parser()
    destinations = {action.dest for action in parser._actions}
    assert destinations == {
        "broker_instance",
        "rendezvous",
        "protocol_version",
        "caller_pid",
        "agent_instance",
    }
    with pytest.raises(SystemExit):
        parser.parse_args(["--command", "whoami"])


def test_broker_environment_removes_provider_secrets_and_python_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Kernel:
        def SetDefaultDllDirectories(self, value: int) -> None:
            del value

        def SetDllDirectoryW(self, value: str) -> None:
            del value

    class _Windll:
        kernel32 = _Kernel()

    original_environment = dict(os.environ)
    original_cwd = Path.cwd()
    monkeypatch.setattr("pc_manager_agent.broker.main.ctypes.windll", _Windll())
    try:
        os.environ["OPENAI_API_KEY"] = "must-not-survive"
        os.environ["OTHER_ACCESS_TOKEN"] = "must-not-survive"
        os.environ["PYTHONPATH"] = "untrusted-module-path"
        os.chdir(tmp_path.parent)
        _harden_process_environment(tmp_path)
        assert "OPENAI_API_KEY" not in os.environ
        assert "OTHER_ACCESS_TOKEN" not in os.environ
        assert "PYTHONPATH" not in os.environ
        assert Path.cwd() == tmp_path
    finally:
        os.chdir(original_cwd)
        os.environ.clear()
        os.environ.update(original_environment)
