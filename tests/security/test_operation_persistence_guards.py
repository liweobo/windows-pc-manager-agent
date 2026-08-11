from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.domain.transactions import TransactionState
from pc_manager_agent.orchestration.transaction_executor import (
    build_all_operation_arguments,
    build_operation_arguments,
)
from pc_manager_agent.persistence.file_operations import OperationRepository, OperationStoreError
from pc_manager_agent.rollback.models import UndoRecord
from pc_manager_agent.tools.execution import ExecutionAuthorization, arguments_digest


def _prepared(runtime: ApplicationRuntime, tmp_path: Path):  # type: ignore[no-untyped-def]
    root = tmp_path / "root"
    source_directory = root / "source"
    destination_directory = root / "destination"
    source_directory.mkdir(parents=True)
    destination_directory.mkdir()
    source = source_directory / "file.txt"
    source.write_text("content", encoding="utf-8")
    record = runtime.authorized_paths.add_authorized(root)
    services = runtime.create_file_operation_services()
    plan = services.compiler.compile_selected_move(
        "move", (source,), destination_directory, (record.path_id,)
    )
    prepared = services.service.prepare(plan)
    return services, plan, prepared


@pytest.mark.security
def test_repository_fails_closed_before_initialization_and_for_unknown_records(
    tmp_path: Path,
) -> None:
    repository = OperationRepository(tmp_path / "state.db")
    with pytest.raises(OperationStoreError, match="not initialized"):
        repository.list_recent()
    repository.initialize()
    try:
        with pytest.raises(OperationStoreError, match="Unknown"):
            repository.get_transaction(uuid4())
        with pytest.raises(OperationStoreError, match="Unknown"):
            repository.get_item(uuid4())
        with pytest.raises(OperationStoreError, match="Unknown"):
            repository.get_operation(uuid4())
        with pytest.raises(OperationStoreError, match="missing"):
            repository.get_undo(uuid4())
    finally:
        repository.close()


@pytest.mark.security
def test_repository_rejects_duplicate_preview_invalid_transitions_and_wrong_undo(
    runtime: ApplicationRuntime,
    tmp_path: Path,
) -> None:
    _services, plan, prepared = _prepared(runtime, tmp_path)
    repository = runtime.operation_repository
    arguments = build_all_operation_arguments(plan, prepared.preview)
    with pytest.raises(OperationStoreError, match="collided"):
        repository.create_from_preview(plan, prepared.preview, arguments)
    with pytest.raises(OperationStoreError, match="Invalid"):
        repository.transition(prepared.preview.transaction_id, TransactionState.COMPLETED)
    operation = plan.operations[0]
    wrong_undo = UndoRecord(
        operation_id=uuid4(),
        transaction_id=prepared.preview.transaction_id,
        sequence=0,
        operation_type=operation.operation_type,
        original_path=operation.source,
        resulting_path=operation.destination,
        before_state=operation.expected_source_state,
    )
    with pytest.raises(OperationStoreError, match="does not match"):
        repository.begin_operation(
            prepared.preview.transaction_id,
            operation.operation_id,
            wrong_undo,
        )
    with pytest.raises(OperationStoreError, match="not RUNNING"):
        repository.fail_operation(
            operation.operation_id,
            error_code="TEST",
            error_message="not started",
        )


@pytest.mark.security
def test_persisted_capability_rejects_wrong_digests_states_and_tampered_undo(
    runtime: ApplicationRuntime,
    tmp_path: Path,
) -> None:
    services, plan, prepared = _prepared(runtime, tmp_path)
    operation = plan.operations[0]
    preview_item = prepared.preview.items[0]
    payload = build_operation_arguments(operation, preview_item)
    authorization = ExecutionAuthorization(
        transaction_id=prepared.preview.transaction_id,
        operation_id=operation.operation_id,
        plan_id=plan.plan_id,
        preview_id=prepared.preview.preview_id,
        tool_name=operation.tool_name,
        arguments_digest=arguments_digest(payload),
    )
    with pytest.raises(OperationStoreError, match="stale"):
        runtime.operation_repository.require_execution_authorization(
            authorization,
            operation.tool_name,
            payload,
        )
    services.service.resolve_confirmation(prepared, True)
    report = services.service.execute(prepared)
    undo = runtime.operation_repository.get_undo(operation.operation_id)
    with runtime.operation_repository._engine.begin() as connection:
        connection.execute(
            text("UPDATE operation_undo_records SET record_digest = :digest WHERE undo_id = :id"),
            {"digest": "0" * 64, "id": str(undo.undo_id)},
        )
    with pytest.raises(OperationStoreError, match="integrity"):
        runtime.operation_repository.get_undo(operation.operation_id)
    with pytest.raises(OperationStoreError, match="not ROLLING_BACK"):
        runtime.operation_repository.begin_rollback_operation(
            operation.operation_id,
            tool_name="file.move",
            argument_payload=payload,
        )
    assert report.transaction.state is TransactionState.COMPLETED
