"""Deterministic authorization service; models never grant filesystem scope."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from uuid import UUID

from pc_manager_agent.authorization.models import AuthorizedPath, AuthorizedPathKind
from pc_manager_agent.domain.errors import PathNotAuthorizedError
from pc_manager_agent.persistence.authorized_paths import AuthorizedPathRepository
from pc_manager_agent.safety.path_policy import PathPolicy


class AuthorizedPathService:
    """Manage and enforce explicit directory authorization decisions."""

    def __init__(
        self,
        repository: AuthorizedPathRepository,
        *,
        network_path_detector: Callable[[Path], bool] | None = None,
        on_change: Callable[[str, AuthorizedPath], None] | None = None,
    ) -> None:
        self._repository = repository
        self._network_path_detector = network_path_detector
        self._on_change = on_change

    def add_authorized(
        self,
        path: Path,
        *,
        label: str | None = None,
        favorite: bool = False,
    ) -> AuthorizedPath:
        """Validate and persist one root without authorizing any parent directory."""
        canonical = PathPolicy.canonicalize_authorization_root(
            path,
            extra_forbidden=self.forbidden_roots(),
            network_path_detector=self._network_path_detector,
        )
        record = AuthorizedPath(
            path=canonical,
            label=(label or canonical.name or str(canonical)).strip(),
            kind=AuthorizedPathKind.AUTHORIZED,
            favorite=favorite,
        )
        stored = self._repository.add(record)
        self._notify("added", stored)
        return stored

    def add_forbidden(self, path: Path, *, label: str | None = None) -> AuthorizedPath:
        """Persist an existing local directory as an additional denied subtree."""
        canonical = PathPolicy.canonicalize_authorization_root(
            path,
            network_path_detector=self._network_path_detector,
        )
        record = AuthorizedPath(
            path=canonical,
            label=(label or canonical.name or str(canonical)).strip(),
            kind=AuthorizedPathKind.FORBIDDEN,
        )
        stored = self._repository.add(record)
        self._notify("added", stored)
        return stored

    def remove(self, path_id: UUID) -> bool:
        """Remove one explicit authorization decision."""
        record = self._repository.get(path_id)
        removed = self._repository.remove(path_id)
        if removed and record is not None:
            self._notify("removed", record)
        return removed

    def restore(self, record: AuthorizedPath) -> AuthorizedPath:
        """Restore an exact prior record for deterministic configuration rollback."""
        if record.kind is AuthorizedPathKind.AUTHORIZED:
            canonical = PathPolicy.canonicalize_authorization_root(
                record.path,
                extra_forbidden=self.forbidden_roots(),
                network_path_detector=self._network_path_detector,
            )
        else:
            canonical = PathPolicy.canonicalize_authorization_root(
                record.path,
                network_path_detector=self._network_path_detector,
            )
        if canonical != record.path:
            raise ValueError("Authorization rollback path identity changed")
        restored = self._repository.add(record)
        self._notify("restored", restored)
        return restored

    def list_authorized(self) -> tuple[AuthorizedPath, ...]:
        """List roots that may be selected in a plan."""
        return self._repository.list(AuthorizedPathKind.AUTHORIZED)

    def list_forbidden(self) -> tuple[AuthorizedPath, ...]:
        """List additional user-protected roots."""
        return self._repository.list(AuthorizedPathKind.FORBIDDEN)

    def forbidden_roots(self) -> tuple[Path, ...]:
        """Return only canonical custom denied paths."""
        return tuple(record.path for record in self.list_forbidden())

    def build_policy(self, root_ids: tuple[UUID, ...]) -> PathPolicy:
        """Build a policy for exactly the authorized identifiers in a plan."""
        if not root_ids:
            raise PathNotAuthorizedError("No authorized root was selected")
        roots: list[Path] = []
        for path_id in root_ids:
            record = self._repository.get(path_id)
            if record is None or record.kind is not AuthorizedPathKind.AUTHORIZED:
                raise PathNotAuthorizedError(f"Unknown authorized root identifier: {path_id}")
            roots.append(record.path)
        policy = PathPolicy.for_authorized_roots(
            roots,
            extra_forbidden=self.forbidden_roots(),
            network_path_detector=self._network_path_detector,
        )
        for root in roots:
            policy.validate_scan_root(root)
        return policy

    def resolve_authorized(self, root_ids: tuple[UUID, ...]) -> tuple[AuthorizedPath, ...]:
        """Resolve identifiers to authorized records without accepting path text."""
        records: list[AuthorizedPath] = []
        for path_id in root_ids:
            record = self._repository.get(path_id)
            if record is None or record.kind is not AuthorizedPathKind.AUTHORIZED:
                raise PathNotAuthorizedError(f"Unknown authorized root identifier: {path_id}")
            records.append(record)
        return tuple(records)

    def require_authorized(self, path: Path) -> Path:
        """Return a canonical path only when it belongs to a configured root."""
        records = self.list_authorized()
        if not records:
            raise PathNotAuthorizedError("No authorized directories are configured")
        policy = PathPolicy.for_authorized_roots(
            (record.path for record in records),
            extra_forbidden=self.forbidden_roots(),
            network_path_detector=self._network_path_detector,
        )
        try:
            return policy.validate_scan_root(path)
        except PermissionError as exc:
            raise PathNotAuthorizedError(str(exc)) from exc

    def require_authorized_file(self, path: Path) -> Path:
        """Return a canonical regular file inside configured authorized roots."""
        records = self.list_authorized()
        if not records:
            raise PathNotAuthorizedError("No authorized directories are configured")
        policy = PathPolicy.for_authorized_roots(
            (record.path for record in records),
            extra_forbidden=self.forbidden_roots(),
            network_path_detector=self._network_path_detector,
        )
        try:
            return policy.validate_file(path)
        except PermissionError as exc:
            raise PathNotAuthorizedError(str(exc)) from exc

    def _notify(self, action: str, record: AuthorizedPath) -> None:
        if self._on_change is not None:
            self._on_change(action, record)
