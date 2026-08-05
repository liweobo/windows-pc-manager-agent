"""Windows-aware path authorization without following reparse points."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterable
from pathlib import Path


class PathSecurityError(PermissionError):
    """Raised when a path violates an explicit safety boundary."""


def _absolute_lexical(path: Path) -> Path:
    """Normalize dot segments without resolving links or junctions."""
    return Path(os.path.abspath(os.path.normpath(os.fspath(path))))


def _is_within(path: Path, root: Path) -> bool:
    """Compare canonical path strings with Windows case folding."""
    candidate = os.path.normcase(os.fspath(_absolute_lexical(path)))
    boundary = os.path.normcase(os.fspath(_absolute_lexical(root)))
    try:
        return os.path.commonpath((candidate, boundary)) == boundary
    except ValueError:
        return False


def is_reparse_point(path: Path) -> bool:
    """Detect symlinks, junctions, and other Windows reparse points."""
    try:
        metadata = os.lstat(path)
    except OSError:
        return False
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(attributes & reparse_flag)


class PathPolicy:
    """Authorize explicit scan roots and deny sensitive or redirected paths."""

    _FORBIDDEN_NAMES = frozenset(
        {"$recycle.bin", ".ssh", "personal vault", "system volume information"}
    )

    def __init__(
        self,
        approved_roots: Iterable[Path],
        forbidden_roots: Iterable[Path] = (),
        *,
        current_user_root: Path | None = None,
    ) -> None:
        roots = tuple(_absolute_lexical(path) for path in approved_roots)
        if not roots:
            msg = "At least one approved root is required"
            raise ValueError(msg)
        self._approved_roots = roots
        self._forbidden_roots = tuple(_absolute_lexical(path) for path in forbidden_roots)
        self._current_user_root = _absolute_lexical(
            current_user_root or Path(os.environ.get("USERPROFILE", Path.home()))
        )
        self._users_root = self._current_user_root.parent

    @classmethod
    def for_scan_root(cls, root: Path, extra_forbidden: Iterable[Path] = ()) -> PathPolicy:
        """Create a policy with conservative per-user protected directories."""
        user_profile = Path(os.environ.get("USERPROFILE", Path.home()))
        local_app_data = Path(os.environ.get("LOCALAPPDATA", user_profile / "AppData/Local"))
        roaming_app_data = Path(os.environ.get("APPDATA", user_profile / "AppData/Roaming"))
        system_root = Path(os.environ.get("SYSTEMROOT", "C:/Windows"))
        protected = (
            user_profile / ".ssh",
            user_profile / "OneDrive" / "Personal Vault",
            local_app_data / "Google" / "Chrome" / "User Data",
            local_app_data / "Microsoft" / "Edge" / "User Data",
            roaming_app_data / "Mozilla" / "Firefox" / "Profiles",
            roaming_app_data / "1Password",
            roaming_app_data / "Bitwarden",
            system_root / "System32" / "config",
            *tuple(extra_forbidden),
        )
        return cls((root,), protected, current_user_root=user_profile)

    @property
    def approved_roots(self) -> tuple[Path, ...]:
        """Return immutable approved roots."""
        return self._approved_roots

    @property
    def forbidden_roots(self) -> tuple[Path, ...]:
        """Return immutable protected roots."""
        return self._forbidden_roots

    def is_forbidden(self, path: Path) -> bool:
        """Return whether a path is sensitive or outside the current profile."""
        candidate = _absolute_lexical(path)
        if candidate.name.casefold() in self._FORBIDDEN_NAMES:
            return True
        if any(_is_within(candidate, root) for root in self._forbidden_roots):
            return True
        inside_users = _is_within(candidate, self._users_root)
        inside_current_user = _is_within(candidate, self._current_user_root)
        return inside_users and not inside_current_user

    def is_approved(self, path: Path) -> bool:
        """Return whether a lexical path is inside an approved non-forbidden root."""
        candidate = _absolute_lexical(path)
        return any(
            _is_within(candidate, root) for root in self._approved_roots
        ) and not self.is_forbidden(candidate)

    def validate_scan_root(self, path: Path) -> Path:
        """Resolve an existing root once, reject traversal and reparse points."""
        if ".." in path.parts:
            msg = f"Parent traversal is not allowed: {path}"
            raise PathSecurityError(msg)
        try:
            candidate = path.expanduser().resolve(strict=True)
        except OSError as exc:
            msg = f"Scan root is unavailable: {path}"
            raise PathSecurityError(msg) from exc
        if not candidate.is_dir():
            msg = f"Scan root is not a directory: {candidate}"
            raise PathSecurityError(msg)
        if is_reparse_point(path.expanduser()) or is_reparse_point(candidate):
            msg = f"Scan root cannot be a link or reparse point: {candidate}"
            raise PathSecurityError(msg)
        if not self.is_approved(candidate):
            msg = f"Scan root is outside the approved scope or protected: {candidate}"
            raise PathSecurityError(msg)
        return candidate

    def entry_rejection_reason(self, path: Path) -> str | None:
        """Return a stable denial reason before scanner metadata access."""
        candidate = _absolute_lexical(path)
        if not any(_is_within(candidate, root) for root in self._approved_roots):
            return "outside-approved-root"
        if self.is_forbidden(candidate):
            return "forbidden-path"
        if is_reparse_point(candidate):
            return "reparse-point"
        return None
