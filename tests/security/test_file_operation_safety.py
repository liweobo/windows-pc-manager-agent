from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from tests.stage2_support import FakeFileOperationPlatform

from pc_manager_agent.domain.file_operations import (
    FileOperationPlan,
    OperationType,
    PlannedFileOperation,
    PreviewItemStatus,
)
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.safety.file_operation_validator import FileOperationSafetyValidator
from pc_manager_agent.safety.operation_preview import OperationPreviewEngine
from pc_manager_agent.safety.path_policy import PathPolicy, PathSecurityError
from pc_manager_agent.tools.file_tools.create_directory import CreateDirectoryTool
from pc_manager_agent.tools.file_tools.move import MoveTool
from pc_manager_agent.tools.file_tools.remove_created_directory import RemoveCreatedDirectoryTool
from pc_manager_agent.tools.file_tools.rename import RenameTool
from pc_manager_agent.tools.registry import ToolRegistry


def _registry(
    policy: PathPolicy,
    platform: FakeFileOperationPlatform,
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(CreateDirectoryTool(policy, platform))
    registry.register(MoveTool(policy, platform))
    registry.register(RenameTool(policy, platform))
    registry.register(RemoveCreatedDirectoryTool(policy, platform))
    return registry


def _move_plan(
    source: Path,
    destination: Path,
    platform: FakeFileOperationPlatform,
) -> FileOperationPlan:
    operation = PlannedFileOperation(
        sequence=0,
        operation_type=(
            OperationType.MOVE_DIRECTORY if source.is_dir() else OperationType.MOVE_FILE
        ),
        tool_name="file.move",
        source=source,
        destination=destination,
        expected_source_state=platform.inspect(source),
    )
    return FileOperationPlan(
        summary="move",
        user_goal="move selected object",
        authorized_root_ids=(uuid4(),),
        operations=(operation,),
    )


@pytest.mark.security
def test_preview_reports_conflict_changed_source_and_unreadable_target(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("one", encoding="utf-8")
    target = tmp_path / "target.txt"
    target.write_text("occupied", encoding="utf-8")
    platform = FakeFileOperationPlatform()
    plan = _move_plan(source, target, platform)
    policy = PathPolicy.for_scan_root(tmp_path)
    engine = OperationPreviewEngine(policy, platform)
    conflict = engine.generate(plan)
    assert conflict.items[0].status is PreviewItemStatus.CONFLICT
    assert conflict.items[0].issues[0].code == "NAME_CONFLICT"

    target.unlink()
    source.write_text("changed", encoding="utf-8")
    changed = engine.generate(plan)
    assert changed.items[0].status is PreviewItemStatus.BLOCKED
    assert any(issue.code == "SOURCE_CHANGED" for issue in changed.items[0].issues)

    platform.inspect_errors[source] = PermissionError("locked")
    unavailable = engine.generate(plan)
    assert any(issue.code == "SOURCE_UNAVAILABLE" for issue in unavailable.items[0].issues)


@pytest.mark.security
def test_preview_blocks_cross_volume_limits_and_unsafe_directory_content(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    destination_root = tmp_path / "destination"
    source_root.mkdir()
    destination_root.mkdir()
    source = source_root / "one.txt"
    source.write_bytes(b"1234")
    platform = FakeFileOperationPlatform()
    platform.volume_overrides[source_root] = 1
    platform.volume_overrides[destination_root] = 2
    policy = PathPolicy.for_scan_root(tmp_path)
    plan = _move_plan(source, destination_root / source.name, platform)
    cross_volume = OperationPreviewEngine(policy, platform).generate(plan)
    assert any(issue.code == "CROSS_VOLUME_MOVE" for issue in cross_volume.items[0].issues)

    platform.volume_overrides[destination_root] = 1
    limited = OperationPreviewEngine(policy, platform, max_objects=1, max_total_bytes=2).generate(
        plan
    )
    assert any(issue.code == "BATCH_LIMIT_EXCEEDED" for issue in limited.items[0].issues)

    directory = source_root / "folder"
    directory.mkdir()
    (directory / "child.txt").write_text("child", encoding="utf-8")
    directory_plan = _move_plan(directory, destination_root / "folder", platform)
    rejected_policy = PathPolicy.for_scan_root(tmp_path)
    original = rejected_policy.entry_rejection_reason
    rejected_policy.entry_rejection_reason = (  # type: ignore[method-assign]
        lambda path: "reparse-point" if path.name == "child.txt" else original(path)
    )
    rejected = OperationPreviewEngine(rejected_policy, platform).generate(directory_plan)
    assert any(issue.code == "SOURCE_UNAVAILABLE" for issue in rejected.items[0].issues)


@pytest.mark.security
def test_preview_handles_planned_parent_and_existing_directory(tmp_path: Path) -> None:
    platform = FakeFileOperationPlatform()
    policy = PathPolicy.for_scan_root(tmp_path)
    parent = tmp_path / "new"
    child = parent / "child"
    operations = (
        PlannedFileOperation(
            sequence=0,
            operation_type=OperationType.CREATE_DIRECTORY,
            tool_name="file.mkdir",
            destination=parent,
        ),
        PlannedFileOperation(
            sequence=1,
            operation_type=OperationType.CREATE_DIRECTORY,
            tool_name="file.mkdir",
            destination=child,
        ),
    )
    plan = FileOperationPlan(
        summary="mkdir",
        user_goal="create folders",
        authorized_root_ids=(uuid4(),),
        operations=operations,
    )
    preview = OperationPreviewEngine(policy, platform).generate(plan)
    assert preview.ready_count == 2
    parent.mkdir()
    existing = OperationPreviewEngine(policy, platform).generate(plan)
    assert existing.items[0].status is PreviewItemStatus.CONFLICT
    platform.inspect_errors[parent] = PermissionError("cannot inspect target")
    unreadable_existing = OperationPreviewEngine(policy, platform).generate(plan)
    assert unreadable_existing.items[0].destination_state is None

    only_child = plan.model_copy(
        update={
            "operations": (
                operations[1].model_copy(
                    update={"sequence": 0, "destination": tmp_path / "missing" / "child"}
                ),
            )
        }
    )
    missing = OperationPreviewEngine(policy, platform).generate(only_child)
    assert any(issue.code == "MISSING_PARENT" for issue in missing.items[0].issues)


@pytest.mark.security
def test_preview_rejects_invalid_limits_unsafe_target_and_unreadable_conflict(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")
    target = tmp_path / "target.txt"
    target.write_text("target", encoding="utf-8")
    platform = FakeFileOperationPlatform()
    policy = PathPolicy.for_scan_root(tmp_path)
    with pytest.raises(ValueError):
        OperationPreviewEngine(policy, platform, max_objects=0)
    base = _move_plan(source, target, platform)
    outside = base.operations[0].model_copy(update={"destination": tmp_path.parent / "outside"})
    unsafe = OperationPreviewEngine(policy, platform).generate(
        base.model_copy(update={"operations": (outside,)})
    )
    assert any(issue.code == "UNSAFE_DESTINATION" for issue in unsafe.items[0].issues)

    platform.inspect_errors[target] = PermissionError("target locked")
    unreadable = OperationPreviewEngine(policy, platform).generate(base)
    assert any(issue.code == "TARGET_UNREADABLE" for issue in unreadable.items[0].issues)


@pytest.mark.security
def test_preview_counts_directory_tree_and_accepts_same_identity_case_rename(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "folder"
    nested = directory / "nested"
    nested.mkdir(parents=True)
    (directory / "one.txt").write_bytes(b"1")
    (nested / "two.txt").write_bytes(b"22")
    destination_parent = tmp_path / "destination"
    destination_parent.mkdir()
    platform = FakeFileOperationPlatform()
    policy = PathPolicy.for_scan_root(tmp_path)
    directory_plan = _move_plan(directory, destination_parent / "folder", platform)
    preview = OperationPreviewEngine(policy, platform).generate(directory_plan)
    assert preview.ready_count == 1
    assert preview.total_size_bytes == 3

    source = tmp_path / "Report.txt"
    source.write_text("report", encoding="utf-8")
    state = platform.inspect(source)
    rename_operation = PlannedFileOperation(
        sequence=0,
        operation_type=OperationType.RENAME_FILE,
        tool_name="file.rename",
        source=source,
        destination=tmp_path / "REPORT.txt",
        expected_source_state=state,
        internal_temporary_path=tmp_path / ".temporary",
    )
    rename_plan = FileOperationPlan(
        summary="case rename",
        user_goal="change case",
        authorized_root_ids=(uuid4(),),
        operations=(rename_operation,),
    )
    case_preview = OperationPreviewEngine(policy, platform).generate(rename_plan)
    assert case_preview.items[0].status is PreviewItemStatus.READY


@pytest.mark.security
def test_validator_rejects_unknown_tool_false_contract_and_overlapping_sources(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "folder"
    directory.mkdir()
    child = directory / "child.txt"
    child.write_text("child", encoding="utf-8")
    platform = FakeFileOperationPlatform()
    policy = PathPolicy.for_scan_root(tmp_path)
    registry = _registry(policy, platform)
    validator = FileOperationSafetyValidator(registry, policy, max_operations=1)
    base = _move_plan(child, tmp_path / "child-moved.txt", platform)

    unknown_operation = base.operations[0].model_copy(update={"tool_name": "file.force_move"})
    unknown = validator.review(base.model_copy(update={"operations": (unknown_operation,)}))
    assert "unknown-tool" in {issue.code for issue in unknown.issues}
    false_risk = validator.review(base.model_copy(update={"risk_level": RiskLevel.R0}))
    assert "invalid-risk-contract" in {issue.code for issue in false_risk.issues}

    directory_operation = PlannedFileOperation(
        sequence=0,
        operation_type=OperationType.MOVE_DIRECTORY,
        tool_name="file.move",
        source=directory,
        destination=tmp_path / "folder-moved",
        expected_source_state=platform.inspect(directory),
    )
    child_operation = base.operations[0].model_copy(
        update={"sequence": 1, "destination": tmp_path / "child-moved.txt"}
    )
    overlap_plan = base.model_copy(update={"operations": (directory_operation, child_operation)})
    overlap = FileOperationSafetyValidator(registry, policy).review(overlap_plan)
    assert "overlapping-sources" in {issue.code for issue in overlap.issues}


@pytest.mark.security
def test_validator_rejects_batch_missing_parent_noop_self_move_and_duplicate_source(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "folder"
    directory.mkdir()
    source = directory / "child.txt"
    source.write_text("child", encoding="utf-8")
    platform = FakeFileOperationPlatform()
    policy = PathPolicy.for_scan_root(tmp_path)
    registry = _registry(policy, platform)
    state = platform.inspect(source)
    first = PlannedFileOperation(
        sequence=0,
        operation_type=OperationType.MOVE_FILE,
        tool_name="file.move",
        source=source,
        destination=source,
        expected_source_state=state,
    )
    second = first.model_copy(
        update={
            "operation_id": uuid4(),
            "sequence": 1,
            "destination": tmp_path / "other.txt",
        }
    )
    plan = FileOperationPlan(
        summary="unsafe graph",
        user_goal="unsafe",
        authorized_root_ids=(uuid4(),),
        operations=(first, second),
    )
    review = FileOperationSafetyValidator(registry, policy, max_operations=1).review(plan)
    codes = {issue.code for issue in review.issues}
    assert {"batch-limit-exceeded", "no-op-operation", "duplicate-source"} <= codes

    directory_operation = PlannedFileOperation(
        sequence=0,
        operation_type=OperationType.MOVE_DIRECTORY,
        tool_name="file.move",
        source=directory,
        destination=directory / "nested",
        expected_source_state=platform.inspect(directory),
    )
    self_move = plan.model_copy(update={"operations": (directory_operation,)})
    assert "directory-self-move" in {
        issue.code
        for issue in FileOperationSafetyValidator(registry, policy).review(self_move).issues
    }

    missing_parent = PlannedFileOperation(
        sequence=0,
        operation_type=OperationType.CREATE_DIRECTORY,
        tool_name="file.mkdir",
        destination=tmp_path / "missing" / "child",
    )
    missing_plan = plan.model_copy(update={"operations": (missing_parent,)})
    assert "missing-planned-parent" in {
        issue.code
        for issue in FileOperationSafetyValidator(registry, policy).review(missing_plan).issues
    }


@pytest.mark.security
def test_validator_rejects_unsafe_destination_and_rename_parent_change(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")
    platform = FakeFileOperationPlatform()
    policy = PathPolicy.for_scan_root(tmp_path)
    registry = _registry(policy, platform)
    base = _move_plan(source, tmp_path / "target.txt", platform)
    outside = base.operations[0].model_copy(update={"destination": tmp_path.parent / "outside"})
    outside_plan = base.model_copy(update={"operations": (outside,)})
    assert "unsafe-path" in {
        issue.code
        for issue in FileOperationSafetyValidator(registry, policy).review(outside_plan).issues
    }

    rename = base.operations[0].model_copy(
        update={
            "operation_type": OperationType.RENAME_FILE,
            "tool_name": "file.rename",
            "destination": tmp_path / "sub" / "renamed.txt",
        }
    )
    rename_plan = base.model_copy(update={"operations": (rename,)})
    assert "unsafe-path" in {
        issue.code
        for issue in FileOperationSafetyValidator(registry, policy).review(rename_plan).issues
    }


@pytest.mark.security
def test_validator_and_preview_block_a_tampered_operation_without_source_state(
    tmp_path: Path,
) -> None:
    """A deserialized or model-copied plan cannot bypass source invariants."""
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")
    platform = FakeFileOperationPlatform()
    policy = PathPolicy.for_scan_root(tmp_path)
    registry = _registry(policy, platform)
    base = _move_plan(source, tmp_path / "target.txt", platform)
    tampered = base.operations[0].model_copy(update={"source": None, "expected_source_state": None})
    tampered_plan = base.model_copy(update={"operations": (tampered,)})

    review = FileOperationSafetyValidator(registry, policy).review(tampered_plan)
    assert "missing-source" in {issue.code for issue in review.issues}

    preview = OperationPreviewEngine(policy, platform).generate(tampered_plan)
    assert preview.items[0].status is PreviewItemStatus.BLOCKED
    assert "INVALID_PLAN" in {issue.code for issue in preview.items[0].issues}


@pytest.mark.security
@pytest.mark.parametrize("name", ["CON", "LPT1.txt", "bad?.txt", "trail. ", ".."])
def test_windows_name_and_destination_boundaries_are_denied(tmp_path: Path, name: str) -> None:
    policy = PathPolicy.for_scan_root(tmp_path)
    with pytest.raises(PathSecurityError):
        policy.validate_windows_name(name)
    with pytest.raises(PathSecurityError):
        policy.validate_operation_destination(tmp_path.parent / name)


@pytest.mark.security
def test_operation_path_rejects_missing_network_ambiguous_and_unchanged_rename(
    tmp_path: Path,
) -> None:
    network_policy = PathPolicy.for_authorized_roots(
        (tmp_path,),
        network_path_detector=lambda _path: True,
    )
    existing = tmp_path / "file.txt"
    existing.write_text("file", encoding="utf-8")
    with pytest.raises(PathSecurityError, match="Network-backed operation sources"):
        network_policy.validate_operation_source(existing)
    with pytest.raises(PathSecurityError, match="Network-backed operation destinations"):
        network_policy.validate_operation_destination(tmp_path / "target.txt")

    policy = PathPolicy.for_scan_root(tmp_path)
    with pytest.raises(PathSecurityError, match="unavailable"):
        policy.validate_operation_source(tmp_path / "missing.txt")
    with pytest.raises(PathSecurityError, match="must change"):
        policy.validate_rename_destination(existing, existing)
    with pytest.raises(PathSecurityError, match="longer"):
        policy.validate_windows_name("x" * 256)
    with pytest.raises(PathSecurityError, match="control"):
        policy.validate_windows_name("bad\x01name")
    with pytest.raises(PathSecurityError, match="UNC"):
        policy.validate_operation_destination(Path(r"\\server\share\file.txt"))
