"""Read-only live Preview generation for Stage 2A operation plans."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID, uuid4

from pc_manager_agent.domain.file_operations import (
    FileObjectKind,
    FileOperationPlan,
    FileOperationPreview,
    FileState,
    OperationPreviewIssue,
    OperationPreviewItem,
    OperationType,
    PreviewItemStatus,
)
from pc_manager_agent.domain.risk import RollbackLevel
from pc_manager_agent.platform_support.base import FileOperationPlatform
from pc_manager_agent.safety.path_policy import PathPolicy, PathSecurityError


class OperationPreviewEngine:
    """Inspect every source and target without performing a filesystem mutation."""

    def __init__(
        self,
        path_policy: PathPolicy,
        platform: FileOperationPlatform,
        *,
        max_objects: int = 500,
        max_total_bytes: int = 50 * 1024**3,
    ) -> None:
        if max_objects <= 0 or max_total_bytes <= 0:
            raise ValueError("Preview object and byte limits must be positive")
        self._path_policy = path_policy
        self._platform = platform
        self._max_objects = max_objects
        self._max_total_bytes = max_total_bytes

    def generate(
        self,
        plan: FileOperationPlan,
        transaction_id: UUID | None = None,
    ) -> FileOperationPreview:
        """Return a live immutable snapshot; conflicts remain visible and non-executable."""
        items: list[OperationPreviewItem] = []
        total_size = 0
        object_count = 0
        planned_directories: set[Path] = set()
        for operation in plan.operations:
            issues: list[OperationPreviewIssue] = []
            source_state = None
            destination_state = None
            status = PreviewItemStatus.READY
            try:
                destination = self._path_policy.validate_operation_destination(
                    operation.destination
                )
                if operation.operation_type in {
                    OperationType.RENAME_FILE,
                    OperationType.RENAME_DIRECTORY,
                }:
                    if operation.source is None:
                        raise ValueError("Rename operation is missing its exact source")
                    self._path_policy.validate_rename_destination(
                        operation.source, operation.destination
                    )
            except (PathSecurityError, OSError, ValueError) as exc:
                destination = operation.destination
                status = PreviewItemStatus.BLOCKED
                issues.append(OperationPreviewIssue(code="UNSAFE_DESTINATION", message=str(exc)))

            if operation.operation_type is OperationType.CREATE_DIRECTORY:
                if destination.exists():
                    try:
                        destination_state = self._platform.inspect(destination)
                    except OSError:
                        destination_state = None
                    status = PreviewItemStatus.CONFLICT
                    issues.append(
                        OperationPreviewIssue(
                            code="NAME_CONFLICT",
                            message="The directory destination already exists",
                        )
                    )
                elif (
                    not destination.parent.exists()
                    and destination.parent not in planned_directories
                ):
                    status = PreviewItemStatus.BLOCKED
                    issues.append(
                        OperationPreviewIssue(
                            code="MISSING_PARENT",
                            message="The destination parent does not exist or is not planned",
                        )
                    )
                planned_directories.add(destination)
            else:
                if operation.source is None or operation.expected_source_state is None:
                    status = PreviewItemStatus.BLOCKED
                    issues.append(
                        OperationPreviewIssue(
                            code="INVALID_PLAN",
                            message="The operation is missing a source or its expected state",
                        )
                    )
                    items.append(
                        OperationPreviewItem(
                            operation_id=operation.operation_id,
                            sequence=operation.sequence,
                            status=status,
                            operation_type=operation.operation_type,
                            source=operation.source,
                            destination=destination,
                            source_state=None,
                            destination_state=None,
                            issues=tuple(issues),
                            rollback_level=RollbackLevel.FULL,
                        )
                    )
                    continue
                try:
                    source = self._path_policy.validate_operation_source(operation.source)
                    source_state = self._platform.inspect(source)
                    if not source_state.unchanged_since(operation.expected_source_state):
                        status = PreviewItemStatus.BLOCKED
                        issues.append(
                            OperationPreviewIssue(
                                code="SOURCE_CHANGED",
                                message="The source identity or metadata changed before Preview",
                            )
                        )
                    impact_count, impact_bytes = self._impact(source, source_state.kind)
                    object_count += impact_count
                    total_size += impact_bytes
                    parent_state = self._inspect_nearest_parent(destination)
                    if source_state.volume_serial != parent_state.volume_serial:
                        status = PreviewItemStatus.BLOCKED
                        issues.append(
                            OperationPreviewIssue(
                                code="CROSS_VOLUME_MOVE",
                                message="Cross-volume move is unavailable in Stage 2A",
                            )
                        )
                except (OSError, PathSecurityError) as exc:
                    status = PreviewItemStatus.BLOCKED
                    issues.append(
                        OperationPreviewIssue(code="SOURCE_UNAVAILABLE", message=str(exc))
                    )

                if destination.exists():
                    try:
                        destination_state = self._platform.inspect(destination)
                    except OSError as exc:
                        status = PreviewItemStatus.BLOCKED
                        issues.append(
                            OperationPreviewIssue(code="TARGET_UNREADABLE", message=str(exc))
                        )
                    case_only_same_object = (
                        source_state is not None
                        and destination_state is not None
                        and source_state.identity_matches(destination_state)
                        and operation.source.parent == destination.parent
                        and operation.source.name.casefold() == destination.name.casefold()
                    )
                    if not case_only_same_object:
                        status = PreviewItemStatus.CONFLICT
                        issues.append(
                            OperationPreviewIssue(
                                code="NAME_CONFLICT",
                                message="The operation destination already exists",
                            )
                        )

            items.append(
                OperationPreviewItem(
                    operation_id=operation.operation_id,
                    sequence=operation.sequence,
                    status=status,
                    operation_type=operation.operation_type,
                    source=operation.source,
                    destination=destination,
                    source_state=source_state,
                    destination_state=destination_state,
                    issues=tuple(issues),
                    rollback_level=RollbackLevel.FULL,
                )
            )

        if object_count > self._max_objects or total_size > self._max_total_bytes:
            message = (
                f"Batch impact exceeds Stage 2A limits: {object_count} objects, {total_size} bytes"
            )
            items = [
                item.model_copy(
                    update={
                        "status": PreviewItemStatus.BLOCKED,
                        "issues": (
                            *item.issues,
                            OperationPreviewIssue(
                                code="BATCH_LIMIT_EXCEEDED",
                                message=message,
                            ),
                        ),
                    }
                )
                if item.status is PreviewItemStatus.READY
                else item
                for item in items
            ]

        return FileOperationPreview(
            transaction_id=transaction_id or uuid4(),
            plan_id=plan.plan_id,
            plan_digest=plan.canonical_digest(),
            items=tuple(items),
            total_size_bytes=total_size,
            ready_count=sum(item.status is PreviewItemStatus.READY for item in items),
            conflict_count=sum(item.status is PreviewItemStatus.CONFLICT for item in items),
            blocked_count=sum(item.status is PreviewItemStatus.BLOCKED for item in items),
            full_rollback_count=sum(item.status is PreviewItemStatus.READY for item in items),
        )

    def _impact(self, source: Path, kind: FileObjectKind) -> tuple[int, int]:
        """Count a bounded directory tree without following redirected entries."""
        if kind is FileObjectKind.FILE:
            return 1, self._platform.inspect(source).size_bytes
        count = 1
        size = 0
        stack = [source]
        while stack:
            directory = stack.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    path = Path(entry.path)
                    rejection = self._path_policy.entry_rejection_reason(path)
                    if rejection is not None:
                        raise PathSecurityError(
                            f"Directory contains an unavailable entry ({rejection}): {path}"
                        )
                    state = self._platform.inspect(path)
                    count += 1
                    if count > self._max_objects:
                        return count, size
                    if state.kind is FileObjectKind.DIRECTORY:
                        stack.append(path)
                    else:
                        size += state.size_bytes
                        if size > self._max_total_bytes:
                            return count, size
        return count, size

    def _inspect_nearest_parent(self, destination: Path) -> FileState:
        """Inspect the nearest existing parent when earlier mkdir operations are planned."""
        parent = destination.parent
        while not parent.exists():
            if parent == parent.parent:
                raise FileNotFoundError(f"No existing destination parent: {destination}")
            parent = parent.parent
        return self._platform.inspect(parent)
