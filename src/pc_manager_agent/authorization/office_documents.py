"""Session-local exact-file grants. Choosing a file never grants its parent directory."""

from __future__ import annotations

import os
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from threading import RLock
from uuid import UUID, uuid4

from pydantic import Field

from pc_manager_agent.domain.office_documents import DocumentFormat, OfficeError, office_digest
from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.platform_support.windows.path_info import is_network_path
from pc_manager_agent.safety.path_policy import PathPolicy, path_is_within


class OfficeGrantKind(StrEnum):
    """Read and output grants are independent and never implicitly upgrade."""

    READ = "READ"
    OUTPUT = "OUTPUT"


class OfficePathGrant(FrozenModel):
    """Exact local path decision created only by explicit user selection."""

    grant_id: UUID = Field(default_factory=uuid4)
    path: Path
    kind: OfficeGrantKind
    format: DocumentFormat

    def canonical_digest(self) -> str:
        """Bind one path, format, grant kind and identifier."""
        return office_digest(self)


class OfficePathGrants:
    """Resolve opaque IDs under current protected paths and revocable local decisions."""

    def __init__(self, forbidden_roots: Callable[[], tuple[Path, ...]]) -> None:
        self._forbidden_roots = forbidden_roots
        self._grants: dict[UUID, OfficePathGrant] = {}
        self._lock = RLock()

    def select(self, path: Path, kind: OfficeGrantKind) -> OfficePathGrant:
        """Validate an exact user-selected file/output, without reading document contents."""
        try:
            format_ = DocumentFormat(path.suffix.lower().lstrip("."))
        except ValueError as exc:
            raise OfficeError("UNSUPPORTED_DOCUMENT_FORMAT") from exc
        self._validate(path, kind)
        grant = OfficePathGrant(path=path.resolve(strict=False), kind=kind, format=format_)
        with self._lock:
            self._grants[grant.grant_id] = grant
        return grant

    def get(self, grant_id: UUID, kind: OfficeGrantKind) -> OfficePathGrant:
        """Recheck scope, forbidden roots, redirects and existence on every use."""
        with self._lock:
            grant = self._grants.get(grant_id)
        if grant is None or grant.kind is not kind:
            raise OfficeError("DOCUMENT_GRANT_MISSING")
        self._validate(grant.path, kind)
        return grant

    def revoke(self, grant_id: UUID) -> None:
        """Revoke one exact grant; every later read/commit must fail."""
        with self._lock:
            self._grants.pop(grant_id, None)

    def _validate(self, path: Path, kind: OfficeGrantKind) -> None:
        """Use existing deny rules and additionally exclude system/application roots."""
        policy = PathPolicy.for_authorized_roots(
            (path.parent,),
            extra_forbidden=self._forbidden_roots(),
            network_path_detector=is_network_path,
        )
        try:
            policy.validate_windows_name(path.name)
            if kind is OfficeGrantKind.READ:
                policy.validate_file(path)
            else:
                policy.validate_operation_destination(path)
                policy.validate_scan_root(path.parent)
            for variable in (
                "SYSTEMROOT",
                "PROGRAMFILES",
                "PROGRAMFILES(X86)",
                "PROGRAMDATA",
                "LOCALAPPDATA",
                "APPDATA",
            ):
                root = os.environ.get(variable)
                if root and path_is_within(path, Path(root)):
                    raise OfficeError("PROTECTED_DOCUMENT_PATH")
        except OSError as exc:
            raise OfficeError("DOCUMENT_PATH_BLOCKED") from exc
