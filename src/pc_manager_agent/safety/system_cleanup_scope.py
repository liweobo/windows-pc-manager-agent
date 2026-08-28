"""Fail-closed path policy for Stage 4E1 metadata-only storage analysis."""

from __future__ import annotations

import stat
from pathlib import Path

from pc_manager_agent.domain.system_optimization import ScanScopeDecision

_REPARSE_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_SENSITIVE_COMPONENTS = {
    ".ssh",
    "credentials",
    "credential manager",
    "cookies",
    "sessions",
    "login data",
    "personal vault",
    "wallets",
}
_SYSTEM_DATABASE_NAMES = {"sam", "security", "system"}


class SystemCleanupScopeError(PermissionError):
    """Raised when a proposed read would leave the Stage 4E1 boundary."""


class SystemCleanupScanScopePolicy:
    """Authorize exact known roots and Stage 1 roots without following reparse points."""

    def __init__(
        self,
        *,
        known_roots: tuple[Path, ...],
        authorized_user_roots: tuple[Path, ...],
        protected_roots: tuple[Path, ...] = (),
    ) -> None:
        self._known_roots = tuple(self._normalize(root) for root in known_roots)
        self._authorized_user_roots = tuple(self._normalize(root) for root in authorized_user_roots)
        self._protected_roots = tuple(self._normalize(root) for root in protected_roots)

    def classify_root(self, path: Path) -> ScanScopeDecision:
        """Classify one exact root without granting access to a parent or sibling."""
        normalized = self._normalize(path)
        self._reject_sensitive_text(normalized)
        if any(self._within(normalized, root) for root in self._protected_roots):
            return ScanScopeDecision.PROTECTED
        if normalized in self._known_roots:
            return ScanScopeDecision.METADATA_ONLY
        if normalized in self._authorized_user_roots:
            return ScanScopeDecision.AUTHORIZED_USER_PATH
        return ScanScopeDecision.UNSUPPORTED

    def validate_root(self, path: Path) -> Path:
        """Require an existing, non-reparse directory with an exact allowed decision."""
        normalized = self._normalize(path)
        decision = self.classify_root(normalized)
        if decision not in {
            ScanScopeDecision.METADATA_ONLY,
            ScanScopeDecision.AUTHORIZED_USER_PATH,
            ScanScopeDecision.SAFE_READ,
        }:
            raise SystemCleanupScopeError(f"Stage 4E1 root is not readable: {decision.value}")
        self._reject_reparse(normalized)
        if not normalized.is_dir():
            raise SystemCleanupScopeError("Stage 4E1 root is not an available directory")
        return normalized

    def validate_entry(self, path: Path, root: Path) -> Path:
        """Revalidate an entry at use time and keep it inside its exact root."""
        normalized = self._normalize(path)
        normalized_root = self._normalize(root)
        if not self._within(normalized, normalized_root):
            raise SystemCleanupScopeError("Entry escaped the approved root")
        self._reject_sensitive_text(normalized)
        self._reject_reparse(normalized)
        return normalized

    @staticmethod
    def _normalize(path: Path) -> Path:
        if not path.is_absolute() or ".." in path.parts:
            raise SystemCleanupScopeError("Stage 4E1 paths must be absolute and traversal-free")
        return path.absolute()

    @staticmethod
    def _within(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
        except ValueError:
            return False
        return True

    @staticmethod
    def _reject_sensitive_text(path: Path) -> None:
        components = {part.casefold() for part in path.parts}
        if components & _SENSITIVE_COMPONENTS:
            raise SystemCleanupScopeError("Sensitive or credential-bearing path is protected")
        if path.name.casefold() in _SYSTEM_DATABASE_NAMES and "config" in components:
            raise SystemCleanupScopeError("Windows security database is protected")

    @staticmethod
    def _reject_reparse(path: Path) -> None:
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise SystemCleanupScopeError("Path metadata is unavailable") from exc
        attributes = int(getattr(metadata, "st_file_attributes", 0))
        if path.is_symlink() or attributes & _REPARSE_ATTRIBUTE:
            raise SystemCleanupScopeError(
                "Symlinks, junctions, and reparse points are not followed"
            )
