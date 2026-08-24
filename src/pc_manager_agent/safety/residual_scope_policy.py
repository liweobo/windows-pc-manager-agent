"""Independent exact-path scope for Stage 4D3 residual metadata scans."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from pc_manager_agent.domain.software_residuals import ContextPathEvidence, UninstallContext
from pc_manager_agent.domain.software_uninstall_analysis import canonical_digest
from pc_manager_agent.safety.path_policy import PathPolicy, is_reparse_point, path_is_within


class ResidualScopeError(PermissionError):
    """Raised when residual discovery would exceed durable uninstall evidence."""


class ResidualScanScopePolicy:
    """Validate only exact context paths without broadening normal authorized roots."""

    def __init__(
        self,
        *,
        max_roots: int = 16,
        network_path_detector: Callable[[Path], bool] | None = None,
        extra_forbidden: tuple[Path, ...] = (),
    ) -> None:
        if not 1 <= max_roots <= 32:
            raise ValueError("Residual root limit must be between 1 and 32")
        self._max_roots = max_roots
        self._network_path_detector = network_path_detector or (lambda _path: False)
        self._forbidden = (*PathPolicy.default_forbidden_roots(), *extra_forbidden)
        profile_value = os.environ.get("USERPROFILE")
        profile = Path(profile_value) if profile_value else Path.home()
        configured_broad_roots = tuple(
            Path(value)
            for value in (
                os.environ.get("WINDIR"),
                os.environ.get("PROGRAMDATA"),
                os.environ.get("PROGRAMFILES"),
                os.environ.get("PROGRAMFILES(X86)"),
                os.environ.get("LOCALAPPDATA"),
                os.environ.get("APPDATA"),
                profile_value,
            )
            if value
        )
        self._broad_roots = (
            *configured_broad_roots,
            *(profile / name for name in ("Desktop", "Documents", "Downloads")),
        )

    def validated_roots(self, context: UninstallContext) -> tuple[ContextPathEvidence, ...]:
        """Return deduplicated exact roots or fail before filesystem access."""
        if not context.eligible_for_analysis:
            raise ResidualScopeError("Uninstall context is not eligible for residual analysis")
        if not context.known_paths:
            raise ResidualScopeError("Uninstall context contains no exact residual paths")
        if len(context.known_paths) > self._max_roots:
            raise ResidualScopeError("Uninstall context exceeds the residual root limit")
        seen: set[str] = set()
        validated: list[ContextPathEvidence] = []
        for evidence in context.known_paths:
            candidate = self._validate_syntax(evidence.path)
            key = os.path.normcase(os.fspath(candidate))
            if key in seen:
                continue
            if self._is_forbidden(candidate):
                raise ResidualScopeError("Residual root intersects a protected location")
            if self._is_broad_root(candidate):
                raise ResidualScopeError(
                    "Residual root is too broad for transaction-bound analysis"
                )
            if self._network_path_detector(candidate):
                raise ResidualScopeError("Network-backed residual roots are not supported")
            seen.add(key)
            validated.append(evidence.model_copy(update={"path": candidate}))
        return tuple(validated)

    def validate_existing_root(self, root: Path) -> Path:
        """Revalidate an existing root immediately before bounded enumeration."""
        candidate = self._validate_syntax(root)
        if self._is_forbidden(candidate):
            raise ResidualScopeError("Residual root became protected")
        if self._is_broad_root(candidate):
            raise ResidualScopeError("Residual root became too broad")
        if self._network_path_detector(candidate):
            raise ResidualScopeError("Residual root became network-backed")
        self._reject_reparse_components(candidate)
        try:
            metadata = os.lstat(candidate)
        except OSError as exc:
            raise ResidualScopeError("Residual root is unavailable") from exc
        if metadata.st_ino < 0 or metadata.st_dev < 0:
            raise ResidualScopeError("Residual root identity is invalid")
        return candidate

    def entry_rejection_reason(self, path: Path, root: Path) -> str | None:
        """Return a stable denial code for one discovered entry."""
        try:
            candidate = self._validate_syntax(path)
        except ResidualScopeError:
            return "unsafe-path-syntax"
        if not path_is_within(candidate, root):
            return "scope-escape"
        if self._is_forbidden(candidate):
            return "protected-path"
        if self._network_path_detector(candidate):
            return "network-path"
        if is_reparse_point(candidate):
            return "reparse-point"
        return None

    def scope_digest(self, context: UninstallContext) -> str:
        """Hash the exact roots and their source/depth constraints."""
        roots = self.validated_roots(context)
        return canonical_digest([item.model_dump(mode="json") for item in roots])

    def _is_forbidden(self, path: Path) -> bool:
        policy = PathPolicy(
            (path,),
            self._forbidden,
            # Network locations have their own stable denial reason and are checked
            # separately by this policy; do not collapse them into "protected".
            network_path_detector=lambda _path: False,
        )
        return policy.is_forbidden(path)

    def _is_broad_root(self, path: Path) -> bool:
        normalized = os.path.normcase(os.path.abspath(os.fspath(path)))
        if path.anchor and normalized == os.path.normcase(os.path.abspath(path.anchor)):
            return True
        return any(
            normalized == os.path.normcase(os.path.abspath(os.fspath(root)))
            for root in self._broad_roots
        )

    @staticmethod
    def _validate_syntax(path: Path) -> Path:
        if not path.is_absolute():
            raise ResidualScopeError("Residual paths must be absolute")
        if ".." in path.parts:
            raise ResidualScopeError("Parent traversal is not allowed")
        raw = os.fspath(path)
        if raw.startswith(("\\\\", "//")):
            raise ResidualScopeError("UNC and device paths are not supported")
        if any(
            part not in {path.anchor, path.drive} and part.rstrip(" .") != part
            for part in path.parts
        ):
            raise ResidualScopeError("Ambiguous trailing spaces or dots are not allowed")
        return Path(os.path.abspath(os.path.normpath(raw)))

    @staticmethod
    def _reject_reparse_components(path: Path) -> None:
        existing = path
        while not existing.exists():
            if existing.parent == existing:
                return
            existing = existing.parent
        chain: list[Path] = []
        current = existing
        while current.parent != current:
            chain.append(current)
            current = current.parent
        chain.append(current)
        for component in reversed(chain):
            if is_reparse_point(component):
                raise ResidualScopeError("Residual path contains a reparse component")
