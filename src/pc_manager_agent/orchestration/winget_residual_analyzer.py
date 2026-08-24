"""Exact-path, non-deleting residual observation for winget removal."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from pc_manager_agent.domain.winget_uninstall import WingetResidualReport

_FILE_ATTRIBUTE_REPARSE_POINT = 0x0400


class WingetResidualAnalyzer:
    """Inspect only the known install path and never enumerate or delete it."""

    def analyze(self, install_location: Path | None) -> WingetResidualReport:
        """Return an exact ``lstat`` observation with explicit unknowns."""
        if install_location is None:
            return WingetResidualReport(
                checked_location=False,
                warnings=("No exact install location was available; residual state is unknown.",),
            )
        try:
            observed = os.lstat(install_location)
        except FileNotFoundError:
            return WingetResidualReport(
                checked_location=True,
                install_location_present=False,
                reparse_or_symlink=False,
            )
        except OSError as exc:
            return WingetResidualReport(
                checked_location=True,
                warnings=(f"Install-location check failed: {type(exc).__name__}.",),
            )
        attributes = getattr(observed, "st_file_attributes", 0)
        redirected = stat.S_ISLNK(observed.st_mode) or bool(
            attributes & _FILE_ATTRIBUTE_REPARSE_POINT
        )
        return WingetResidualReport(
            checked_location=True,
            install_location_present=True,
            reparse_or_symlink=redirected,
            warnings=("The exact path is redirected and was not followed.",) if redirected else (),
        )
