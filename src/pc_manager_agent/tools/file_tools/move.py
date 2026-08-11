"""Registered same-volume, no-overwrite file and directory move tool."""

from __future__ import annotations

from pydantic import BaseModel

from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.platform_support.base import FileOperationPlatform
from pc_manager_agent.safety.path_policy import PathPolicy
from pc_manager_agent.tools.file_tools.operation_models import FileOperationToolResult, MoveRequest
from pc_manager_agent.tools.manifest import CancellationToken, ToolManifest


class MoveTool:
    """Move one identified object between authorized paths on the same volume."""

    def __init__(self, path_policy: PathPolicy, platform: FileOperationPlatform) -> None:
        self._path_policy = path_policy
        self._platform = platform
        self._manifest = ToolManifest(
            name="file.move",
            description="Move one identified file or directory on the same volume",
            input_model=MoveRequest,
            output_model=FileOperationToolResult,
            risk_level=RiskLevel.R1,
            required_permissions=("ordinary-user:move",),
            read_only=False,
            idempotent=False,
            supports_cancellation=False,
            rollback_level=RollbackLevel.FULL,
            preconditions=("source unchanged", "destination absent", "same volume"),
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
        """Revalidate TOCTOU state, move once, and verify identity at the destination."""
        if not isinstance(request, MoveRequest):
            raise TypeError("file.move received an invalid request model")
        if cancellation.is_cancelled:
            raise RuntimeError("Move cancelled before execution")
        source = self._path_policy.validate_operation_source(request.source)
        destination = self._path_policy.validate_operation_destination(request.destination)
        before = self._platform.inspect(source)
        if not before.unchanged_since(request.expected_source_state):
            raise RuntimeError("Source changed after Preview")
        if destination.exists():
            raise FileExistsError(f"Move destination already exists: {destination}")
        parent = self._platform.inspect(destination.parent)
        if before.volume_serial != parent.volume_serial:
            raise RuntimeError("Cross-volume move is unavailable in Stage 2A")
        self._platform.move_same_volume(source, destination)
        if source.exists():
            raise RuntimeError("Move verification failed because the source still exists")
        after = self._platform.inspect(destination)
        if not before.identity_matches(after):
            raise RuntimeError("Move verification failed because identity changed")
        return FileOperationToolResult(
            operation_id=request.operation_id,
            before_state=before,
            after_state=after,
            verified=True,
        )
