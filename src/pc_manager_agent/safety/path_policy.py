"""Windows-aware path authorization without following reparse points."""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Callable, Iterable
from pathlib import Path


class PathSecurityError(PermissionError):
    """Raised when a path violates an explicit safety boundary."""


def _absolute_lexical(path: Path) -> Path:
    """Remove dot segments and return an absolute path without resolving redirects."""
    return Path(os.path.abspath(os.path.normpath(os.fspath(path))))


def _is_within(path: Path, root: Path) -> bool:
    """Compare canonical components with Windows case folding, not string prefixes."""
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
    """Detect symbolic links, junctions, mount points, and other reparse points."""
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
    _RESERVED_WINDOWS_NAMES = frozenset({"con", "prn", "aux", "nul"})
    _INVALID_NAME_CHARACTERS = frozenset('<>:"/\\|?*')
    _RESERVED_PORT_PATTERN = re.compile(r"^(?:com|lpt)[1-9]$", re.IGNORECASE)

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

    def validate_operation_source(self, path: Path) -> Path:
        """Validate an existing regular file or directory immediately before mutation."""
        candidate = self._validate_operation_syntax(path)
        self._reject_reparse_components(candidate)
        try:
            resolved = candidate.resolve(strict=True)
        except PathSecurityError:
            raise
        except OSError as exc:
            raise PathSecurityError(f"Operation source is unavailable: {path}") from exc
        if not resolved.is_file() and not resolved.is_dir():
            raise PathSecurityError(f"Unsupported operation source type: {resolved}")
        if self.entry_rejection_reason(resolved) is not None:
            raise PathSecurityError(f"Operation source is outside approved scope: {resolved}")
        if self._network_path_detector(resolved):
            raise PathSecurityError(f"Network-backed operation sources are unavailable: {resolved}")
        return resolved

    def validate_operation_destination(self, path: Path) -> Path:
        """Validate a possibly absent target without resolving it through a redirect."""
        candidate = self._validate_operation_syntax(path)
        self.validate_windows_name(candidate.name)
        self._reject_reparse_components(candidate)
        # Validate the containing scope independently from whether the leaf is absent,
        # a file, or a directory. Callers need a safe canonical target in order to report
        # an existing leaf as NAME_CONFLICT rather than misclassifying it as an unsafe path.
        existing_ancestor = candidate.parent
        while not existing_ancestor.exists():
            parent = existing_ancestor.parent
            if parent == existing_ancestor:
                raise PathSecurityError(
                    f"Operation destination has no existing ancestor: {candidate}"
                )
            existing_ancestor = parent
        self._reject_reparse_components(existing_ancestor)
        try:
            resolved_ancestor = existing_ancestor.resolve(strict=True)
        except OSError as exc:
            raise PathSecurityError(
                f"Operation destination ancestor is unavailable: {existing_ancestor}"
            ) from exc
        if not resolved_ancestor.is_dir():
            raise PathSecurityError(
                f"Operation destination ancestor is not a directory: {resolved_ancestor}"
            )
        if not self.is_approved(resolved_ancestor):
            raise PathSecurityError(
                f"Operation destination ancestor is outside approved scope: {resolved_ancestor}"
            )
        if self._network_path_detector(resolved_ancestor):
            raise PathSecurityError(
                f"Network-backed operation destinations are unavailable: {resolved_ancestor}"
            )
        return candidate

    def validate_rename_destination(self, source: Path, destination: Path) -> Path:
        """Validate that rename changes only the final name inside the same directory."""
        source_candidate = self._validate_operation_syntax(source)
        destination_candidate = self.validate_operation_destination(destination)
        if _absolute_lexical(source_candidate.parent) != _absolute_lexical(
            destination_candidate.parent
        ):
            raise PathSecurityError("Rename cannot change the parent directory")
        if source_candidate.name == destination_candidate.name:
            raise PathSecurityError("Rename must change the name")
        return destination_candidate

    @classmethod
    def validate_windows_name(cls, name: str) -> str:
        """Reject traversal, reserved devices, controls, ambiguity, and invalid characters."""
        if not name or name in {".", ".."}:
            raise PathSecurityError("A file or directory name is required")
        if len(name) > 255:
            raise PathSecurityError("A file or directory name is longer than 255 characters")
        if name.rstrip(" .") != name:
            raise PathSecurityError("Windows names cannot end with a space or dot")
        if any(ord(character) < 32 for character in name):
            raise PathSecurityError("Windows names cannot contain control characters")
        if any(character in cls._INVALID_NAME_CHARACTERS for character in name):
            raise PathSecurityError("Windows name contains an invalid character")
        device_stem = name.split(".", maxsplit=1)[0].casefold()
        if device_stem in cls._RESERVED_WINDOWS_NAMES or cls._RESERVED_PORT_PATTERN.fullmatch(
            device_stem
        ):
            raise PathSecurityError(f"Windows reserved device name is unavailable: {name}")
        return name

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

    def _validate_operation_syntax(self, path: Path) -> Path:
        """Apply lexical Stage 2A checks without resolving an absent destination."""
        if not path.is_absolute() or ".." in path.parts:
            raise PathSecurityError(f"Operation path must be absolute without traversal: {path}")
        if _is_unc_or_device_path(path):
            raise PathSecurityError(f"UNC, network, and device paths are unavailable: {path}")
        if _has_ambiguous_segment(path):
            raise PathSecurityError(f"Operation path contains an ambiguous segment: {path}")
        candidate = _absolute_lexical(path.expanduser())
        if not self.is_approved(candidate):
            raise PathSecurityError(f"Operation path is outside approved scope: {candidate}")
        return candidate

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
