from __future__ import annotations

from pathlib import Path

import pytest

from pc_manager_agent.app.runtime import ApplicationRuntime, FileOperationServices
from pc_manager_agent.domain.transactions import OperationItemState, TransactionState
from pc_manager_agent.orchestration.file_operation_service import PreparedFileOperation
from pc_manager_agent.persistence.file_operations import OperationRepository
from pc_manager_agent.tools.manifest import CancellationToken


def _prepared_move(
    runtime: ApplicationRuntime,
    tmp_path: Path,
) -> tuple[FileOperationServices, PreparedFileOperation, Path, Path]:
    root = tmp_path / "root"
    source_directory = root / "source"
    destination_directory = root / "destination"
    source_directory.mkdir(parents=True)
    destination_directory.mkdir()
    source = source_directory / "file.txt"
    destination = destination_directory / source.name
    source.write_text("content", encoding="utf-8")
    record = runtime.authorized_paths.add_authorized(root)
    services = runtime.create_file_operation_services()
    plan = services.compiler.compile_selected_move(
        "move",
        (source,),
        destination_directory,
        (record.path_id,),
    )
    prepared = services.service.prepare(plan)
    services.service.resolve_confirmation(prepared, True)
    return services, prepared, source, destination


@pytest.mark.windows
def test_cancelled_transaction_stops_before_first_write(
    runtime: ApplicationRuntime,
    tmp_path: Path,
) -> None:
    services, prepared, source, destination = _prepared_move(runtime, tmp_path)
    cancellation = CancellationToken()
    cancellation.cancel()
    report = services.service.execute(prepared, cancellation)
    assert report.transaction.state is TransactionState.CANCELLED
    assert report.items[0].state is OperationItemState.PENDING
    assert source.exists() and not destination.exists()


@pytest.mark.windows
def test_startup_marks_running_transaction_interrupted_without_resuming(
    runtime: ApplicationRuntime,
    tmp_path: Path,
) -> None:
    _services, prepared, source, destination = _prepared_move(runtime, tmp_path)
    transaction_id = prepared.preview.transaction_id
    runtime.operation_repository.transition(transaction_id, TransactionState.RUNNING)
    runtime.operation_repository.close()
    replacement = OperationRepository(runtime.settings.database_path)
    interrupted = replacement.initialize()
    try:
        assert interrupted == (transaction_id,)
        assert replacement.get_transaction(transaction_id).state is TransactionState.INTERRUPTED
        assert source.exists() and not destination.exists()
    finally:
        replacement.close()


@pytest.mark.windows
def test_rollback_preview_blocks_modified_result_and_audit_is_separate(
    runtime: ApplicationRuntime,
    tmp_path: Path,
) -> None:
    services, prepared, source, destination = _prepared_move(runtime, tmp_path)
    report = services.service.execute(prepared)
    destination.write_text("modified after move", encoding="utf-8")
    rollback = services.rollback.prepare(report.transaction.transaction_id)
    assert rollback.confirmation is None
    assert rollback.plan.items[0].status.value == "CONFLICT"
    assert any(issue.code == "RESULT_CHANGED" for issue in rollback.plan.items[0].issues)
    events = runtime.audit.list_recent(20)
    event_types = {event.event_type for event in events}
    assert "file_operation.previewed" in event_types
    assert "file_operation.completed" in event_types
    assert runtime.operation_repository.list_undo(report.transaction.transaction_id)
    assert not source.exists()
