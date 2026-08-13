from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.security
def test_stage4a_process_sources_have_no_shell_elevation_or_command_line_collection() -> None:
    root = Path(__file__).parents[2] / "src" / "pc_manager_agent"
    sources = (
        root / "platform_support" / "windows" / "process_management.py",
        root / "tools" / "system_tools" / "process_actions.py",
        root / "orchestration" / "process_actions.py",
        root / "orchestration" / "process_action_planner.py",
    )
    combined = "\n".join(path.read_text(encoding="utf-8") for path in sources).casefold()
    forbidden = (
        "shell=true",
        "taskkill",
        "powershell",
        "cmd.exe",
        "subprocess.",
        "shellexecute",
        "runas",
        "debugprivilege",
        "adjusttokenprivileges",
        "process_cmdline",
        ".cmdline(",
    )
    assert not [value for value in forbidden if value in combined]


@pytest.mark.security
def test_process_action_domain_has_no_command_or_elevation_field() -> None:
    root = Path(__file__).parents[2] / "src" / "pc_manager_agent"
    source = (root / "domain" / "process_actions.py").read_text(encoding="utf-8").casefold()
    forbidden_fields = (
        "command_line:",
        "cmdline:",
        "shell_command:",
        "requires_admin:",
        "elevate:",
    )
    assert not [value for value in forbidden_fields if value in source]
