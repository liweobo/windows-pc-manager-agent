from __future__ import annotations

import platform as standard_library_platform
from pathlib import Path

import pc_manager_agent
from pc_manager_agent.platform_support.windows.single_instance import (
    QtSingleInstanceGuard,
)


def test_platform_support_does_not_shadow_standard_library() -> None:
    package_root = Path(pc_manager_agent.__file__).resolve().parent
    standard_library_file = Path(standard_library_platform.__file__).resolve()

    assert not standard_library_file.is_relative_to(package_root)
    assert QtSingleInstanceGuard.__module__.startswith("pc_manager_agent.platform_support.")
