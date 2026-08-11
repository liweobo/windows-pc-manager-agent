"""Strict input and output schemas for Stage 2A registered file tools."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from pc_manager_agent.domain.file_operations import FileState
from pc_manager_agent.domain.plans import FrozenModel


class CreateDirectoryRequest(FrozenModel):
    """Validated input for creating one ordinary directory."""

    operation_id: UUID
    destination: Path


class MoveRequest(FrozenModel):
    """Validated input for moving one identified object on the same volume."""

    operation_id: UUID
    source: Path
    destination: Path
    expected_source_state: FileState


class RenameRequest(FrozenModel):
    """Validated input for renaming one identified object within its parent."""

    operation_id: UUID
    source: Path
    destination: Path
    expected_source_state: FileState
    internal_temporary_path: Path | None = None


class FileOperationToolResult(FrozenModel):
    """Observed and verified result of one registered mutation tool."""

    operation_id: UUID
    before_state: FileState | None
    after_state: FileState
    verified: bool


class RemoveCreatedDirectoryRequest(FrozenModel):
    """Validated rollback-only request for one unchanged empty created directory."""

    operation_id: UUID
    path: Path
    expected_state: FileState


class EmptyDirectoryRollbackResult(FrozenModel):
    """Verified result after removing one transaction-created empty directory."""

    operation_id: UUID
    removed_path: Path
    verified: bool
