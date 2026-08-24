"""Safe Explorer selection for an already reported Stage 4D3 candidate."""

from __future__ import annotations

import ctypes
import os

# Security: this module permits only a fixed, identity-checked explorer.exe.
import subprocess  # nosec B404
from pathlib import Path

from pc_manager_agent.domain.software_residuals import ResidualCandidate
from pc_manager_agent.safety.path_policy import is_reparse_point, path_is_within


class ResidualExplorerError(RuntimeError):
    """Raised when Explorer cannot safely select a reported candidate."""


class WindowsResidualExplorerService:
    """Open a fixed Explorer executable for one context-bound local candidate."""

    def select_candidate(self, candidate: ResidualCandidate) -> None:
        """Select an existing candidate without executing files or resolving links."""
        if os.name != "nt":
            raise ResidualExplorerError("Explorer integration is available only on Windows")
        if not path_is_within(candidate.path, candidate.scan_root):
            raise ResidualExplorerError("Candidate is outside its recorded scan root")
        if candidate.reparse_or_symlink or is_reparse_point(candidate.path):
            raise ResidualExplorerError("Link and reparse candidates are not opened")
        try:
            metadata = os.lstat(candidate.path)
        except OSError as exc:
            raise ResidualExplorerError("Candidate is no longer available") from exc
        if (metadata.st_dev, metadata.st_ino) != (
            candidate.identity.device_id,
            candidate.identity.file_id,
        ):
            raise ResidualExplorerError("Candidate identity changed after the report")
        # Security: executable and argv shape are fixed and shell execution is disabled.
        completed = subprocess.run(  # nosec B603
            [str(self._explorer_path()), f"/select,{candidate.path}"],
            check=False,
            timeout=10,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
        if completed.returncode != 0:
            raise ResidualExplorerError(
                f"Windows Explorer returned exit code {completed.returncode}"
            )

    @staticmethod
    def _explorer_path() -> Path:
        buffer = ctypes.create_unicode_buffer(32_768)
        length = ctypes.windll.kernel32.GetWindowsDirectoryW(buffer, len(buffer))
        if length <= 0 or length >= len(buffer):
            raise ResidualExplorerError("Windows directory could not be resolved")
        executable = Path(buffer.value) / "explorer.exe"
        if not executable.is_file():
            raise ResidualExplorerError("Windows Explorer executable is unavailable")
        return executable
