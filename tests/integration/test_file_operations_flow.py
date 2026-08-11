from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.confirmation.file_operations import OperationConfirmationError
from pc_manager_agent.domain.file_operations import RenameRule, RenameRuleType
from pc_manager_agent.domain.transactions import OperationItemState, TransactionState
from pc_manager_agent.tools.manifest import CancellationToken


def _authorize(runtime: ApplicationRuntime, root: Path) -> tuple[UUID, ...]:
    record = runtime.authorized_paths.add_authorized(root)
    return (record.path_id,)


@pytest.mark.windows
def test_move_created_directories_and_reverse_order_rollback(
    runtime: ApplicationRuntime,
    tmp_path: Path,
) -> None:
    root = tmp_path / "authorized"
    source_directory = root / "Downloads"
    source_directory.mkdir(parents=True)
    source = source_directory / "报告 2026.pdf"
    source.write_bytes(b"verified-content")
    root_ids = _authorize(runtime, root)
    services = runtime.create_file_operation_services()
    destination_directory = root / "Documents" / "PDF"
    plan = services.compiler.compile_selected_move(
        "move one PDF",
        (source,),
        destination_directory,
        root_ids,
    )

    prepared = services.service.prepare(plan)
    assert prepared.preview.ready_count == 3
    assert prepared.preview.conflict_count == 0
    services.service.resolve_confirmation(prepared, True)
    report = services.service.execute(prepared)

    destination = destination_directory / source.name
    assert report.transaction.state is TransactionState.COMPLETED
    assert report.transaction.completed_count == 3
    assert not source.exists()
    assert destination.read_bytes() == b"verified-content"
    assert len(runtime.operation_repository.list_undo(report.transaction.transaction_id)) == 3

    rollback = services.rollback.prepare(report.transaction.transaction_id)
    assert [item.sequence for item in rollback.plan.items] == [2, 1, 0]
    assert all(item.status.value == "READY" for item in rollback.plan.items)
    services.rollback.resolve_confirmation(rollback, True)
    rolled_back = services.rollback.execute(rollback)

    assert rolled_back.state is TransactionState.ROLLED_BACK
    assert source.read_bytes() == b"verified-content"
    assert not destination_directory.exists()
    assert not (root / "Documents").exists()


@pytest.mark.windows
def test_rename_and_rollback_preserve_identity(
    runtime: ApplicationRuntime,
    tmp_path: Path,
) -> None:
    root = tmp_path / "authorized"
    root.mkdir()
    source = root / "照片.JPG"
    source.write_bytes(b"image")
    root_ids = _authorize(runtime, root)
    services = runtime.create_file_operation_services()
    plan = services.compiler.compile_selected_rename(
        "number image",
        (source,),
        RenameRule(rule_type=RenameRuleType.SEQUENCE, value="photo_", width=3),
        root_ids,
    )
    prepared = services.service.prepare(plan)
    services.service.resolve_confirmation(prepared, True)
    report = services.service.execute(prepared)
    renamed = root / "photo_001.JPG"

    assert report.transaction.state is TransactionState.COMPLETED
    assert renamed.exists()
    rollback = services.rollback.prepare(report.transaction.transaction_id)
    services.rollback.resolve_confirmation(rollback, True)
    result = services.rollback.execute(rollback)
    assert result.state is TransactionState.ROLLED_BACK
    assert source.read_bytes() == b"image"
    assert not renamed.exists()


@pytest.mark.windows
def test_target_appearing_after_confirmation_fails_closed_and_stops_batch(
    runtime: ApplicationRuntime,
    tmp_path: Path,
) -> None:
    root = tmp_path / "authorized"
    source_directory = root / "source"
    destination_directory = root / "destination"
    source_directory.mkdir(parents=True)
    destination_directory.mkdir()
    first = source_directory / "a.txt"
    second = source_directory / "b.txt"
    first.write_text("a", encoding="utf-8")
    second.write_text("b", encoding="utf-8")
    root_ids = _authorize(runtime, root)
    services = runtime.create_file_operation_services()
    plan = services.compiler.compile_selected_move(
        "move two files",
        (first, second),
        destination_directory,
        root_ids,
    )
    prepared = services.service.prepare(plan)
    services.service.resolve_confirmation(prepared, True)
    (destination_directory / "a.txt").write_text("new occupant", encoding="utf-8")

    report = services.service.execute(prepared)

    assert report.transaction.state is TransactionState.FAILED
    assert report.transaction.failed_count == 1
    assert first.exists() and second.exists()
    assert (destination_directory / "a.txt").read_text(encoding="utf-8") == "new occupant"
    assert [item.state for item in report.items] == [
        OperationItemState.FAILED,
        OperationItemState.PENDING,
    ]


@pytest.mark.windows
def test_changed_source_confirmation_replay_and_rollback_conflict_are_denied(
    runtime: ApplicationRuntime,
    tmp_path: Path,
) -> None:
    root = tmp_path / "authorized"
    source_directory = root / "source"
    destination_directory = root / "destination"
    source_directory.mkdir(parents=True)
    destination_directory.mkdir()
    source = source_directory / "item.txt"
    source.write_text("before", encoding="utf-8")
    root_ids = _authorize(runtime, root)
    services = runtime.create_file_operation_services()
    plan = services.compiler.compile_selected_move(
        "move one file",
        (source,),
        destination_directory,
        root_ids,
    )
    prepared = services.service.prepare(plan)
    services.service.resolve_confirmation(prepared, True)
    source.write_text("changed after preview", encoding="utf-8")
    failed = services.service.execute(prepared)
    assert failed.transaction.state is TransactionState.FAILED
    with pytest.raises(OperationConfirmationError):
        services.service.execute(prepared)

    fresh_plan = services.compiler.compile_selected_move(
        "move changed file",
        (source,),
        destination_directory,
        root_ids,
    )
    fresh = services.service.prepare(fresh_plan)
    services.service.resolve_confirmation(fresh, True)
    completed = services.service.execute(fresh)
    source.write_text("new file at original path", encoding="utf-8")
    rollback = services.rollback.prepare(completed.transaction.transaction_id)
    assert rollback.plan.items[0].status.value == "CONFLICT"
    assert any(issue.code == "ROLLBACK_CONFLICT" for issue in rollback.plan.items[0].issues)
    with pytest.raises(RuntimeError, match="no safe"):
        services.rollback.resolve_confirmation(rollback, True)
    with pytest.raises(RuntimeError, match="no safe"):
        services.rollback.execute(rollback)


@pytest.mark.windows
def test_rollback_stops_on_target_appearing_after_its_preview(
    runtime: ApplicationRuntime,
    tmp_path: Path,
) -> None:
    root = tmp_path / "authorized"
    source_directory = root / "source"
    destination_directory = root / "destination"
    source_directory.mkdir(parents=True)
    destination_directory.mkdir()
    files = tuple(source_directory / name for name in ("a.txt", "b.txt"))
    for path in files:
        path.write_text(path.stem, encoding="utf-8")
    root_ids = _authorize(runtime, root)
    services = runtime.create_file_operation_services()
    plan = services.compiler.compile_selected_move(
        "move two",
        files,
        destination_directory,
        root_ids,
    )
    prepared = services.service.prepare(plan)
    services.service.resolve_confirmation(prepared, True)
    report = services.service.execute(prepared)
    rollback = services.rollback.prepare(report.transaction.transaction_id)
    services.rollback.resolve_confirmation(rollback, True)
    files[1].write_text("new occupant", encoding="utf-8")

    result = services.rollback.execute(rollback)
    assert result.state is TransactionState.ROLLBACK_FAILED
    assert files[1].read_text(encoding="utf-8") == "new occupant"
    assert (destination_directory / "b.txt").exists()


@pytest.mark.windows
def test_rollback_cancel_stops_future_reverse_steps_and_extra_content_blocks_mkdir_undo(
    runtime: ApplicationRuntime,
    tmp_path: Path,
) -> None:
    root = tmp_path / "authorized"
    source_directory = root / "source"
    source_directory.mkdir(parents=True)
    source = source_directory / "a.txt"
    source.write_text("a", encoding="utf-8")
    root_ids = _authorize(runtime, root)
    services = runtime.create_file_operation_services()
    target_directory = root / "created" / "nested"
    plan = services.compiler.compile_selected_move(
        "move with mkdir",
        (source,),
        target_directory,
        root_ids,
    )
    prepared = services.service.prepare(plan)
    with pytest.raises(RuntimeError, match="cannot be rolled back"):
        services.rollback.prepare(prepared.preview.transaction_id)
    services.service.resolve_confirmation(prepared, True)
    report = services.service.execute(prepared)

    extra = target_directory / "unmanaged.txt"
    extra.write_text("keep", encoding="utf-8")
    conflicted = services.rollback.prepare(report.transaction.transaction_id)
    assert any(item.status.value == "CONFLICT" for item in conflicted.plan.items)
    extra.unlink()

    rollback = services.rollback.prepare(report.transaction.transaction_id)
    services.rollback.resolve_confirmation(rollback, True)
    cancellation = CancellationToken()
    cancellation.cancel()
    cancelled = services.rollback.execute(rollback, cancellation)
    assert cancelled.state is TransactionState.PARTIALLY_ROLLED_BACK
    assert (target_directory / source.name).exists()


@pytest.mark.windows
def test_rollback_revalidates_result_after_confirmation(
    runtime: ApplicationRuntime,
    tmp_path: Path,
) -> None:
    root = tmp_path / "authorized"
    source_directory = root / "source"
    destination_directory = root / "destination"
    source_directory.mkdir(parents=True)
    destination_directory.mkdir()
    source = source_directory / "a.txt"
    source.write_text("a", encoding="utf-8")
    root_ids = _authorize(runtime, root)
    services = runtime.create_file_operation_services()
    plan = services.compiler.compile_selected_move(
        "move",
        (source,),
        destination_directory,
        root_ids,
    )
    prepared = services.service.prepare(plan)
    services.service.resolve_confirmation(prepared, True)
    report = services.service.execute(prepared)
    destination = destination_directory / source.name
    rollback = services.rollback.prepare(report.transaction.transaction_id)
    services.rollback.resolve_confirmation(rollback, True)
    destination.write_text("changed after rollback preview", encoding="utf-8")
    result = services.rollback.execute(rollback)
    assert result.state is TransactionState.ROLLBACK_FAILED
    assert destination.exists() and not source.exists()
