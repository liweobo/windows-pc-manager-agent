"""Registered same-parent, no-overwrite file and directory rename tool."""

from __future__ import annotations

from pydantic import BaseModel

from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.platform_support.base import FileOperationPlatform
from pc_manager_agent.safety.path_policy import PathPolicy
from pc_manager_agent.tools.file_tools.operation_models import (
    FileOperationToolResult,
    RenameRequest,
)
from pc_manager_agent.tools.manifest import CancellationToken, ToolManifest


class RenameTool:
    """Rename one identified object without allowing a parent-directory change."""

    def __init__(self, path_policy: PathPolicy, platform: FileOperationPlatform) -> None:
        self._path_policy = path_policy
        self._platform = platform
        self._manifest = ToolManifest(
            name="file.rename",
            description="Rename one identified file or directory inside its current parent",
            input_model=RenameRequest,
            output_model=FileOperationToolResult,
            risk_level=RiskLevel.R1,
            required_permissions=("ordinary-user:rename",),
            read_only=False,
            idempotent=False,
            supports_cancellation=False,
            rollback_level=RollbackLevel.FULL,
            preconditions=("source unchanged", "destination absent", "same parent"),
            postconditions=("source absent", "destination identity preserved"),
            timeout_seconds=30.0,
            max_batch_size=1,
            audit_fields=("operation_id", "source", "destination"),
            supported_platforms=("windows",),
            requires_confirmation=True,
            supports_preview=True,
            scope_argument_names=("source", "destination"),
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return the immutable R1 tool manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Revalidate, perform a direct or case-only rename, then verify identity."""
        if not isinstance(request, RenameRequest):
            raise TypeError("file.rename received an invalid request model")
        if cancellation.is_cancelled:
            raise RuntimeError("Rename cancelled before execution")
        source = self._path_policy.validate_operation_source(request.source)
        destination = self._path_policy.validate_rename_destination(source, request.destination)
        before = self._platform.inspect(source)
        if not before.unchanged_since(request.expected_source_state):
            raise RuntimeError("Source changed after Preview")

        case_only = source.name.casefold() == destination.name.casefold()
        if destination.exists() and not case_only:
            raise FileExistsError(f"Rename destination already exists: {destination}")
        if case_only:
            temporary = request.internal_temporary_path
            if temporary is None:
                raise RuntimeError("Case-only rename requires a Preview-bound temporary path")
            temporary = self._path_policy.validate_rename_destination(source, temporary)
            if temporary.exists():
                raise FileExistsError(f"Case-only temporary path already exists: {temporary}")
            self._platform.move_same_volume(source, temporary)
            temporary_state = self._platform.inspect(temporary)
            if not before.identity_matches(temporary_state):
                raise RuntimeError("Case-only temporary rename changed identity")
            try:
                self._platform.move_same_volume(temporary, destination)
            except OSError:
                if not source.exists() and temporary.exists():
                    self._platform.move_same_volume(temporary, source)
                raise
        else:
            self._platform.move_same_volume(source, destination)

        if not case_only and source.exists():
            raise RuntimeError("Rename verification failed because the source still exists")
        after = self._platform.inspect(destination)
        if not before.identity_matches(after):
            raise RuntimeError("Rename verification failed because identity changed")
        return FileOperationToolResult(
            operation_id=request.operation_id,
            before_state=before,
            after_state=after,
            verified=True,
        )
