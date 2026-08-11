"""Allow-listed Windows Explorer integration without shell command parsing."""

from __future__ import annotations

import ctypes
import os

# This module executes one Windows-owned binary with a validated argv list only.
import subprocess  # nosec B404
from pathlib import Path
from typing import Any, cast

from pc_manager_agent.authorization.service import AuthorizedPathService


class ExplorerOpenError(RuntimeError):
    """Raised when Explorer cannot safely select an authorized file."""


class WindowsExplorerService:
    """Open Explorer with a fixed executable and a validated argv list."""

    def __init__(self, authorization: AuthorizedPathService) -> None:
        self._authorization = authorization

    def select_file(self, path: Path) -> None:
        """Select one existing authorized file in Explorer without using a shell."""
        if os.name != "nt":
            raise ExplorerOpenError("Explorer integration is available only on Windows")
        canonical = self._authorization.require_authorized_file(path)
        try:
            # Both values are deterministic authority boundaries: Windows supplies the
            # executable path and authorization validates the selected regular file.
            completed = subprocess.run(  # nosec B603
                [str(self._explorer_path()), f"/select,{canonical}"],
                shell=False,
                check=False,
                capture_output=True,
                timeout=5.0,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ExplorerOpenError("Windows Explorer could not be opened") from exc
        if completed.returncode not in {0, 1}:
            raise ExplorerOpenError(f"Windows Explorer returned exit code {completed.returncode}")

    @staticmethod
    def _explorer_path() -> Path:
        """Resolve Explorer from the Windows directory instead of process PATH."""
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_windows_directory = cast(Any, kernel32.GetWindowsDirectoryW)
        buffer = ctypes.create_unicode_buffer(32_768)
        length = int(get_windows_directory(buffer, len(buffer)))
        if length <= 0 or length >= len(buffer):
            raise ExplorerOpenError("Windows directory could not be resolved")
        executable = Path(buffer.value) / "explorer.exe"
        if not executable.is_file():
            raise ExplorerOpenError("Windows Explorer executable is unavailable")
        return executable
