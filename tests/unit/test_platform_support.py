from __future__ import annotations

import platform as standard_library_platform
import subprocess
from pathlib import Path

import pytest

import pc_manager_agent
from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.platform_support.windows.single_instance import (
    QtSingleInstanceGuard,
)


def test_platform_support_does_not_shadow_standard_library() -> None:
    package_root = Path(pc_manager_agent.__file__).resolve().parent
    standard_library_file = Path(standard_library_platform.__file__).resolve()

    assert not standard_library_file.is_relative_to(package_root)
    assert QtSingleInstanceGuard.__module__.startswith("pc_manager_agent.platform_support.")


def test_explorer_uses_absolute_windows_binary_and_authorized_file(
    runtime: ApplicationRuntime,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    file = root / "safe.txt"
    file.write_text("safe", encoding="utf-8")
    runtime.authorized_paths.add_authorized(root)
    calls: list[list[str]] = []

    def complete(arguments: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(
        "pc_manager_agent.platform_support.windows.explorer.subprocess.run", complete
    )

    runtime.explorer.select_file(file)

    assert Path(calls[0][0]).is_absolute()
    assert Path(calls[0][0]).name.casefold() == "explorer.exe"
    assert str(file.resolve()) in calls[0][1]
