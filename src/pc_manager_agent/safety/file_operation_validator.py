"""Independent semantic validation for concrete Stage 2A file-operation plans."""

from __future__ import annotations

from pathlib import Path

from pc_manager_agent.domain.file_operations import FileOperationPlan, OperationType
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.safety.path_policy import PathPolicy, PathSecurityError, path_is_within
from pc_manager_agent.safety.plan_reviewer import ReviewIssue, SafetyReview
from pc_manager_agent.tools.registry import ToolRegistry, UnknownToolError


class FileOperationSafetyValidator:
    """Reject unknown tools, scope expansion, unsafe graphs, and false risk claims."""

    def __init__(
        self,
        registry: ToolRegistry,
        path_policy: PathPolicy,
        *,
        max_operations: int = 500,
    ) -> None:
        if max_operations <= 0:
            raise ValueError("Operation limit must be positive")
        self._registry = registry
        self._path_policy = path_policy
        self._max_operations = max_operations

    def review(self, plan: FileOperationPlan) -> SafetyReview:
        """Review a concrete plan without reading file contents or mutating anything."""
        issues: list[ReviewIssue] = []
        if len(plan.operations) > self._max_operations:
            issues.append(
                ReviewIssue(
                    code="batch-limit-exceeded",
                    message=f"Plan exceeds the Stage 2A limit of {self._max_operations} operations",
                )
            )
        if plan.risk_level is not RiskLevel.R1 or not plan.requires_confirmation:
            issues.append(
                ReviewIssue(
                    code="invalid-risk-contract",
                    message="Stage 2A plans must be R1 and require explicit confirmation",
                )
            )
        if plan.rollback_level is not RollbackLevel.FULL:
            issues.append(
                ReviewIssue(
                    code="invalid-rollback-contract",
                    message="Stage 2A plans must target FULL rollback",
                )
            )

        planned_directories: set[Path] = set()
        source_paths: list[tuple[Path, OperationType]] = []
        for operation in plan.operations:
            step_id = str(operation.operation_id)
            try:
                manifest = self._registry.manifest(operation.tool_name)
            except UnknownToolError:
                issues.append(
                    ReviewIssue(
                        code="unknown-tool",
                        message=f"Tool is not registered: {operation.tool_name}",
                        step_id=step_id,
                    )
                )
                continue
            if (
                manifest.risk_level is not RiskLevel.R1
                or manifest.read_only
                or not manifest.requires_confirmation
                or not manifest.supports_preview
                or manifest.rollback_level is not RollbackLevel.FULL
            ):
                issues.append(
                    ReviewIssue(
                        code="unsafe-tool-manifest",
                        message=(
                            "Registered tool does not satisfy the R1 write contract: "
                            f"{operation.tool_name}"
                        ),
                        step_id=step_id,
                    )
                )

            try:
                destination = self._path_policy.validate_operation_destination(
                    operation.destination
                )
                if operation.operation_type in {
                    OperationType.RENAME_FILE,
                    OperationType.RENAME_DIRECTORY,
                }:
                    if operation.source is None:
                        raise PathSecurityError("Rename operation is missing a source")
                    self._path_policy.validate_rename_destination(
                        operation.source, operation.destination
                    )
            except PathSecurityError as exc:
                issues.append(ReviewIssue(code="unsafe-path", message=str(exc), step_id=step_id))
                continue

            if operation.operation_type is OperationType.CREATE_DIRECTORY:
                if (
                    destination.parent != destination
                    and not destination.parent.exists()
                    and destination.parent not in planned_directories
                ):
                    issues.append(
                        ReviewIssue(
                            code="missing-planned-parent",
                            message=(
                                "Missing destination parent is not created by an earlier operation"
                            ),
                            step_id=step_id,
                        )
                    )
                planned_directories.add(destination)
                continue

            if operation.source is None:
                issues.append(
                    ReviewIssue(
                        code="missing-source",
                        message="A move or rename operation must name an exact source",
                        step_id=step_id,
                    )
                )
                continue
            try:
                source = self._path_policy.validate_operation_source(operation.source)
            except PathSecurityError as exc:
                issues.append(ReviewIssue(code="unsafe-source", message=str(exc), step_id=step_id))
                continue
            source_paths.append((source, operation.operation_type))
            if source == destination:
                issues.append(
                    ReviewIssue(
                        code="no-op-operation",
                        message="Source and destination are identical",
                        step_id=step_id,
                    )
                )
            if source.is_dir() and path_is_within(destination, source):
                issues.append(
                    ReviewIssue(
                        code="directory-self-move",
                        message="A directory cannot be moved into itself",
                        step_id=step_id,
                    )
                )

        for index, (source, source_type) in enumerate(source_paths):
            for other_source, _other_type in source_paths[index + 1 :]:
                if source == other_source:
                    issues.append(
                        ReviewIssue(
                            code="duplicate-source",
                            message=f"Source is scheduled more than once: {source}",
                        )
                    )
                if source_type in {
                    OperationType.MOVE_DIRECTORY,
                    OperationType.RENAME_DIRECTORY,
                } and path_is_within(other_source, source):
                    issues.append(
                        ReviewIssue(
                            code="overlapping-sources",
                            message=(
                                "A child is scheduled separately from its directory: "
                                f"{other_source}"
                            ),
                        )
                    )
        return SafetyReview(approved=not issues, issues=tuple(issues))
