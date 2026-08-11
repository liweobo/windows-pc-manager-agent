"""Registered rollback-only tool for one unchanged empty created directory."""

from __future__ import annotations

from pydantic import BaseModel

from pc_manager_agent.domain.file_operations import FileObjectKind
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.platform_support.base import FileOperationPlatform
from pc_manager_agent.safety.path_policy import PathPolicy
from pc_manager_agent.tools.file_tools.operation_models import (
    EmptyDirectoryRollbackResult,
    RemoveCreatedDirectoryRequest,
)
from pc_manager_agent.tools.manifest import CancellationToken, ToolManifest


class RemoveCreatedDirectoryTool:
    """Remove only an unchanged, empty directory created by the same transaction."""

    def __init__(self, path_policy: PathPolicy, platform: FileOperationPlatform) -> None:
        self._path_policy = path_policy
        self._platform = platform
        self._manifest = ToolManifest(
            name="file.rollback.rmdir-empty",
            description="Rollback one transaction-created directory only while unchanged and empty",
            input_model=RemoveCreatedDirectoryRequest,
            output_model=EmptyDirectoryRollbackResult,
            risk_level=RiskLevel.R1,
            required_permissions=("ordinary-user:remove-empty-created-directory",),
            read_only=False,
            idempotent=False,
            supports_cancellation=False,
            rollback_level=RollbackLevel.FULL,
            preconditions=("directory identity unchanged", "directory empty", "authorized path"),
            postconditions=("created empty directory absent",),
            timeout_seconds=30.0,
            max_batch_size=1,
            audit_fields=("operation_id", "path"),
            supported_platforms=("windows",),
            requires_confirmation=True,
            supports_preview=True,
            scope_argument_names=("path",),
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return the immutable rollback-only R1 tool manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Revalidate identity and emptiness immediately before removing one directory."""
        if not isinstance(request, RemoveCreatedDirectoryRequest):
            raise TypeError("file.rollback.rmdir-empty received an invalid request model")
        if cancellation.is_cancelled:
            raise RuntimeError("Empty-directory rollback cancelled before execution")
        path = self._path_policy.validate_operation_source(request.path)
        current = self._platform.inspect(path)
        if current.kind is not FileObjectKind.DIRECTORY:
            raise RuntimeError("Rollback target is not a directory")
        if not current.unchanged_since(request.expected_state):
            raise RuntimeError("Created directory changed after rollback Preview")
        if next(path.iterdir(), None) is not None:
            raise RuntimeError("Created directory is no longer empty")
        self._platform.remove_empty_directory(path)
        if path.exists():
            raise RuntimeError("Empty-directory rollback verification failed")
        return EmptyDirectoryRollbackResult(
            operation_id=request.operation_id,
            removed_path=path,
            verified=True,
        )
