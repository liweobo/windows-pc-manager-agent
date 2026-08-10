"""Windows-aware path authorization without following reparse points."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable, Iterable
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


def path_is_within(path: Path, root: Path) -> bool:
    """Return whether a normalized path is equal to or below a root."""
    return _is_within(path, root)


def _has_ambiguous_segment(path: Path) -> bool:
    """Reject Windows segments whose trailing characters are normalized away."""
    return any(
        part not in {path.anchor, path.drive} and part.rstrip(" .") != part for part in path.parts
    )


def _is_unc_or_device_path(path: Path) -> bool:
    """Recognize UNC and extended device syntax before any resolution occurs."""
    raw = os.fspath(path)
    return raw.startswith(("\\\\", "//"))


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
        network_path_detector: Callable[[Path], bool] | None = None,
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
        self._network_path_detector = network_path_detector or (lambda _path: False)

    @staticmethod
    def default_forbidden_roots() -> tuple[Path, ...]:
        """Return conservative credential, browser, wallet, and system roots."""
        user_profile = Path(os.environ.get("USERPROFILE", Path.home()))
        local_app_data = Path(os.environ.get("LOCALAPPDATA", user_profile / "AppData/Local"))
        roaming_app_data = Path(os.environ.get("APPDATA", user_profile / "AppData/Roaming"))
        system_root = Path(os.environ.get("SYSTEMROOT", "C:/Windows"))
        return (
            user_profile / ".ssh",
            user_profile / ".bitcoin",
            user_profile / "AppData" / "Roaming" / "Ethereum",
            user_profile / "OneDrive" / "Personal Vault",
            local_app_data / "Google" / "Chrome" / "User Data",
            local_app_data / "Microsoft" / "Edge" / "User Data",
            roaming_app_data / "Mozilla" / "Firefox" / "Profiles",
            roaming_app_data / "1Password",
            roaming_app_data / "Bitwarden",
            roaming_app_data / "Exodus",
            roaming_app_data / "Ledger Live",
            roaming_app_data / "Microsoft" / "Credentials",
            roaming_app_data / "Microsoft" / "Protect",
            local_app_data / "Microsoft" / "Credentials",
            local_app_data / "Microsoft" / "Vault",
            system_root / "System32" / "config",
        )

    @classmethod
    def for_scan_root(cls, root: Path, extra_forbidden: Iterable[Path] = ()) -> PathPolicy:
        """Create a policy with conservative per-user protected directories."""
        return cls.for_authorized_roots((root,), extra_forbidden=extra_forbidden)

    @classmethod
    def for_authorized_roots(
        cls,
        roots: Iterable[Path],
        *,
        extra_forbidden: Iterable[Path] = (),
        network_path_detector: Callable[[Path], bool] | None = None,
    ) -> PathPolicy:
        """Create one policy spanning exactly the explicitly authorized roots."""
        user_profile = Path(os.environ.get("USERPROFILE", Path.home()))
        protected = (*cls.default_forbidden_roots(), *tuple(extra_forbidden))
        return cls(
            roots,
            protected,
            current_user_root=user_profile,
            network_path_detector=network_path_detector,
        )

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
        if any(part.casefold() in self._FORBIDDEN_NAMES for part in candidate.parts):
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
        """Resolve an existing local root and reject ambiguous or redirected paths."""
        if not path.is_absolute():
            msg = f"Relative scan roots are not allowed: {path}"
            raise PathSecurityError(msg)
        if ".." in path.parts:
            msg = f"Parent traversal is not allowed: {path}"
            raise PathSecurityError(msg)
        if _is_unc_or_device_path(path):
            msg = f"UNC, network, and device paths are unavailable in Stage 1: {path}"
            raise PathSecurityError(msg)
        if _has_ambiguous_segment(path):
            msg = f"Path contains an ambiguous trailing space or dot: {path}"
            raise PathSecurityError(msg)
        try:
            expanded = path.expanduser()
            self._reject_reparse_components(expanded)
            candidate = expanded.resolve(strict=True)
        except PathSecurityError:
            # Preserve the precise security reason. PathSecurityError derives from
            # PermissionError/OSError, so the generic filesystem branch must not
            # accidentally relabel a detected reparse point as merely unavailable.
            raise
        except OSError as exc:
            msg = f"Scan root is unavailable: {path}"
            raise PathSecurityError(msg) from exc
        if not candidate.is_dir():
            msg = f"Scan root is not a directory: {candidate}"
            raise PathSecurityError(msg)
        if is_reparse_point(path.expanduser()) or is_reparse_point(candidate):
            msg = f"Scan root cannot be a link or reparse point: {candidate}"
            raise PathSecurityError(msg)
        if self._network_path_detector(candidate):
            msg = f"Network-backed paths are unavailable in Stage 1: {candidate}"
            raise PathSecurityError(msg)
        if not self.is_approved(candidate):
            msg = f"Scan root is outside the approved scope or protected: {candidate}"
            raise PathSecurityError(msg)
        return candidate

    def validate_file(self, path: Path) -> Path:
        """Validate an existing regular file immediately before content reading."""
        if not path.is_absolute() or ".." in path.parts:
            raise PathSecurityError(f"File path must be absolute without traversal: {path}")
        if _is_unc_or_device_path(path) or _has_ambiguous_segment(path):
            raise PathSecurityError(f"File path is ambiguous or network-backed: {path}")
        self._reject_reparse_components(path)
        try:
            candidate = path.resolve(strict=True)
        except OSError as exc:
            raise PathSecurityError(f"File is unavailable: {path}") from exc
        if not candidate.is_file():
            raise PathSecurityError(f"Path is not a regular file: {candidate}")
        if self.entry_rejection_reason(candidate) is not None:
            raise PathSecurityError(f"File is outside approved scope or protected: {candidate}")
        if self._network_path_detector(candidate):
            raise PathSecurityError(f"Network-backed files are unavailable: {candidate}")
        return candidate

    @classmethod
    def canonicalize_authorization_root(
        cls,
        path: Path,
        *,
        extra_forbidden: Iterable[Path] = (),
        network_path_detector: Callable[[Path], bool] | None = None,
    ) -> Path:
        """Validate a directory before it is persisted as an authorized root."""
        policy = cls.for_authorized_roots(
            (path,),
            extra_forbidden=extra_forbidden,
            network_path_detector=network_path_detector,
        )
        return policy.validate_scan_root(path)

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

    @staticmethod
    def _reject_reparse_components(path: Path) -> None:
        """Reject any existing component that redirects traversal before resolve()."""
        absolute = _absolute_lexical(path)
        anchor = Path(absolute.anchor)
        current = anchor
        parts = absolute.parts[1:] if absolute.anchor else absolute.parts
        for part in parts:
            current /= part
            if not current.exists():
                break
            if is_reparse_point(current):
                msg = f"Path component cannot be a link or reparse point: {current}"
                raise PathSecurityError(msg)
