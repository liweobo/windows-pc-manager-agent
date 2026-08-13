"""Static source boundary checks for Stage 4C1 forbidden fallbacks."""

from pathlib import Path

import pytest


@pytest.mark.security
def test_service_implementation_has_no_shell_elevation_or_process_kill_fallback() -> None:
    roots = (
        Path("src/pc_manager_agent/platform_support/windows/service_control.py"),
        Path("src/pc_manager_agent/tools/system_tools/service_actions.py"),
        Path("src/pc_manager_agent/orchestration/service_actions.py"),
    )
    source = "\n".join(path.read_text(encoding="utf-8").casefold() for path in roots)
    forbidden = (
        "subprocess",
        "powershell",
        "cmd.exe",
        "sc.exe",
        "shell=true",
        "terminateprocess",
        "taskkill",
        "process.kill",
        "runas",
        "changeserviceconfig",
        "deleteservice",
    )
    assert all(value not in source for value in forbidden)
