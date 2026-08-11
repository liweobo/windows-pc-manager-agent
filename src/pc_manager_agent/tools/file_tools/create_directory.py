"""Registered fail-if-exists ordinary directory creation tool."""

from __future__ import annotations

from pydantic import BaseModel

from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.platform_support.base import FileOperationPlatform
from pc_manager_agent.safety.path_policy import PathPolicy
from pc_manager_agent.tools.file_tools.operation_models import (
    CreateDirectoryRequest,
    FileOperationToolResult,
)
from pc_manager_agent.tools.manifest import CancellationToken, ToolManifest


class CreateDirectoryTool:
    """Create exactly one authorized directory and verify its stable identity."""

    def __init__(self, path_policy: PathPolicy, platform: FileOperationPlatform) -> None:
        self._path_policy = path_policy
        self._platform = platform
        self._manifest = ToolManifest(
            name="file.mkdir",
            description="Create one ordinary directory without parents or overwrite",
            input_model=CreateDirectoryRequest,
            output_model=FileOperationToolResult,
            risk_level=RiskLevel.R1,
            required_permissions=("ordinary-user:create-directory",),
            read_only=False,
            idempotent=False,
            supports_cancellation=False,
            rollback_level=RollbackLevel.FULL,
            preconditions=("destination authorized", "destination absent", "parent exists"),
            postconditions=("directory exists", "directory identity recorded"),
            timeout_seconds=30.0,
            max_batch_size=1,
            audit_fields=("operation_id", "destination"),
            supported_platforms=("windows",),
            requires_confirmation=True,
            supports_preview=True,
            scope_argument_names=("destination",),
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return the immutable R1 tool manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Revalidate, create, and verify one directory; cancellation applies before start."""
        if not isinstance(request, CreateDirectoryRequest):
            raise TypeError("file.mkdir received an invalid request model")
        if cancellation.is_cancelled:
            raise RuntimeError("Directory creation cancelled before execution")
        destination = self._path_policy.validate_operation_destination(request.destination)
        if destination.exists():
            raise FileExistsError(f"Directory destination already exists: {destination}")
        if not destination.parent.is_dir():
            raise FileNotFoundError(f"Directory parent does not exist: {destination.parent}")
        self._platform.create_directory(destination)
        after = self._platform.inspect(destination)
        if after.path != destination:
            raise RuntimeError("Directory verification returned an unexpected path")
        return FileOperationToolResult(
            operation_id=request.operation_id,
            before_state=None,
            after_state=after,
            verified=True,
        )
