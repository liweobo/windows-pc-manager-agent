"""Stricter Stage 2B path boundary for destructive-but-recoverable operations."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterable
from pathlib import Path

from pc_manager_agent.safety.path_policy import PathPolicy, PathSecurityError, path_is_within

_FILE_ATTRIBUTE_SYSTEM = getattr(stat, "FILE_ATTRIBUTE_SYSTEM", 0x4)
_FILE_ATTRIBUTE_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_FILE_ATTRIBUTE_OFFLINE = getattr(stat, "FILE_ATTRIBUTE_OFFLINE", 0x1000)


class TrashPathPolicy:
    """Compose authorization with R2-only system, attribute, and root protections."""

    def __init__(self, base_policy: PathPolicy, extra_protected_roots: Iterable[Path] = ()) -> None:
        self._base = base_policy
        user_profile = Path(os.environ.get("USERPROFILE", Path.home()))
        system_root = Path(os.environ.get("SYSTEMROOT", "C:/Windows"))
        program_data = Path(os.environ.get("PROGRAMDATA", "C:/ProgramData"))
        program_files = Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
        program_files_x86 = Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)"))
        local_app_data = Path(os.environ.get("LOCALAPPDATA", user_profile / "AppData/Local"))
        roaming_app_data = Path(os.environ.get("APPDATA", user_profile / "AppData/Roaming"))
        self._protected = tuple(
            Path(os.path.abspath(path))
            for path in (
                system_root,
                program_data,
                program_files,
                program_files_x86,
                local_app_data,
                roaming_app_data,
                *tuple(extra_protected_roots),
            )
        )

    @property
    def approved_roots(self) -> tuple[Path, ...]:
        """Return the exact authorized roots inherited from the base policy."""
        return self._base.approved_roots

    def validate_source(self, path: Path) -> Path:
        """Validate one selected object against authorization and destructive exclusions."""
        candidate = self._base.validate_operation_source(path)
        if any(path_is_within(candidate, root) for root in self._protected):
            raise PathSecurityError(f"System and application-data paths cannot be recycled: {path}")
        attributes = int(getattr(os.lstat(candidate), "st_file_attributes", 0))
        if attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
            raise PathSecurityError(f"Reparse points cannot be recycled: {candidate}")
        if attributes & _FILE_ATTRIBUTE_SYSTEM:
            raise PathSecurityError(f"System objects cannot be recycled: {candidate}")
        if attributes & _FILE_ATTRIBUTE_OFFLINE:
            raise PathSecurityError(f"Offline placeholders cannot be recycled: {candidate}")
        if any(candidate == root for root in self._base.approved_roots):
            raise PathSecurityError("An authorized root itself cannot be recycled")
        return candidate

    def entry_rejection_reason(self, path: Path) -> str | None:
        """Return why a directory descendant makes its selected parent ineligible."""
        try:
            self.validate_source(path)
        except (OSError, PathSecurityError) as exc:
            return str(exc)
        return None
