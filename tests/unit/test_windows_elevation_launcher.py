"""ShellExecuteEx UAC launcher tests with no real prompt."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
import pywintypes

from pc_manager_agent.platform_support.privileged_broker import (
    BrokerLaunchArguments,
    ElevatedProcessHandle,
    ElevationLaunchStatus,
)
from pc_manager_agent.platform_support.windows.elevation import (
    WindowsElevationError,
    WindowsUacBrokerLauncher,
    _winerror,
)


def _arguments() -> BrokerLaunchArguments:
    return BrokerLaunchArguments(
        broker_instance_id=uuid4(),
        rendezvous_id="a" * 43,
        protocol_version=1,
        expected_caller_process_id=123,
        agent_instance_id=uuid4(),
    )


def test_launcher_uses_fixed_absolute_exe_and_opaque_bootstrap_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "broker.exe"
    executable.touch()
    captured: dict[str, object] = {}

    def launch(**values: object) -> dict[str, object]:
        captured.update(values)
        return {"hProcess": 77}

    monkeypatch.setattr(
        "pc_manager_agent.platform_support.windows.elevation.shell.ShellExecuteEx", launch
    )
    monkeypatch.setattr(
        "pc_manager_agent.platform_support.windows.elevation.win32process.GetProcessId",
        lambda _handle: 456,
    )
    result = WindowsUacBrokerLauncher(executable).launch(executable, _arguments())
    assert result.status is ElevationLaunchStatus.STARTED
    assert result.process is not None and result.process.process_id == 456
    assert captured["lpVerb"] == "runas"
    assert captured["lpFile"] == str(executable)
    parameters = str(captured["lpParameters"])
    assert "--caller-pid 123" in parameters
    assert "--agent-instance" in parameters
    assert "secret" not in parameters.casefold()
    assert "request" not in parameters.casefold()


def test_uac_cancel_maps_to_cancelled_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "broker.exe"
    executable.touch()
    calls = 0

    def cancel(**values: object) -> None:
        nonlocal calls
        del values
        calls += 1
        raise pywintypes.error(1223, "ShellExecuteEx", "cancelled")

    monkeypatch.setattr(
        "pc_manager_agent.platform_support.windows.elevation.shell.ShellExecuteEx", cancel
    )
    result = WindowsUacBrokerLauncher(executable).launch(executable, _arguments())
    assert result.status is ElevationLaunchStatus.CANCELLED
    assert result.error_code == 1223
    assert calls == 1


def test_launcher_rejects_path_or_protocol_substitution_before_uac(tmp_path: Path) -> None:
    executable = tmp_path / "broker.exe"
    other = tmp_path / "other.exe"
    executable.touch()
    other.touch()
    launcher = WindowsUacBrokerLauncher(executable)
    with pytest.raises(WindowsElevationError, match="path"):
        launcher.launch(other, _arguments())
    with pytest.raises(WindowsElevationError, match="version"):
        launcher.launch(
            executable,
            _arguments().__class__(
                broker_instance_id=uuid4(),
                rendezvous_id="a" * 43,
                protocol_version=2,
                expected_caller_process_id=123,
                agent_instance_id=uuid4(),
            ),
        )
    with pytest.raises(WindowsElevationError, match="rendezvous"):
        launcher.launch(
            executable,
            BrokerLaunchArguments(
                broker_instance_id=uuid4(),
                rendezvous_id="bad",
                protocol_version=1,
                expected_caller_process_id=123,
                agent_instance_id=uuid4(),
            ),
        )
    with pytest.raises(WindowsElevationError, match="absolute"):
        WindowsUacBrokerLauncher(Path("broker.exe"))


def test_launcher_reports_missing_process_and_non_cancel_windows_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "broker.exe"
    executable.touch()
    monkeypatch.setattr(
        "pc_manager_agent.platform_support.windows.elevation.shell.ShellExecuteEx",
        lambda **_values: {},
    )
    launcher = WindowsUacBrokerLauncher(executable)
    assert launcher.launch(executable, _arguments()).status is ElevationLaunchStatus.FAILED

    def denied(**values: object) -> None:
        del values
        raise pywintypes.error(5, "ShellExecuteEx", "denied")

    monkeypatch.setattr(
        "pc_manager_agent.platform_support.windows.elevation.shell.ShellExecuteEx", denied
    )
    result = launcher.launch(executable, _arguments())
    assert result.status is ElevationLaunchStatus.FAILED
    assert result.error_code == 5


def test_launcher_close_and_error_code_fallbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    executable = Path("C:/broker.exe")
    launcher = WindowsUacBrokerLauncher(executable)
    closed: list[int] = []
    monkeypatch.setattr(
        "pc_manager_agent.platform_support.windows.elevation.win32api.CloseHandle",
        closed.append,
    )
    launcher.close_process_handle(ElevatedProcessHandle(1, 99))
    assert closed == [99]

    class _HresultError(Exception):
        hresult = 0x80070020

    assert _winerror(_HresultError()) == 0x20
    assert _winerror(Exception("none")) == 0


def test_launcher_waits_for_natural_exit_without_terminating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = WindowsUacBrokerLauncher(Path("C:/broker.exe"))
    process = ElevatedProcessHandle(1, 99)
    monkeypatch.setattr(
        "pc_manager_agent.platform_support.windows.elevation.win32event.WaitForSingleObject",
        lambda _handle, _timeout: 0,
    )
    monkeypatch.setattr(
        "pc_manager_agent.platform_support.windows.elevation.win32process.GetExitCodeProcess",
        lambda _handle: 25,
    )
    assert launcher.wait_for_exit(process, timeout_seconds=2) == 25

    monkeypatch.setattr(
        "pc_manager_agent.platform_support.windows.elevation.win32event.WaitForSingleObject",
        lambda _handle, _timeout: 258,
    )
    assert launcher.wait_for_exit(process, timeout_seconds=2) is None
