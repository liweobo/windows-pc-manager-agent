from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from pc_manager_agent.confirmation.file_operations import (
    OperationConfirmationError,
    OperationConfirmationService,
    RollbackConfirmationService,
)
from pc_manager_agent.domain.file_operations import (
    FileObjectKind,
    FileOperationIntentDraft,
    FileOperationPlan,
    FileOperationPreview,
    FileSelectionRule,
    FileState,
    OperationPreviewItem,
    OperationType,
    PlannedFileOperation,
    PreviewItemStatus,
    RenameRule,
    RenameRuleType,
)
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.rollback.models import (
    RollbackItemStatus,
    RollbackPlan,
    RollbackPreviewItem,
)


def _state(path: Path, *, file_id: str = "01", size: int = 1) -> FileState:
    return FileState(
        path=path,
        kind=FileObjectKind.FILE,
        volume_serial=1,
        file_id=file_id,
        size_bytes=size,
        created_ns=1,
        modified_ns=2,
        attributes=0,
    )


def _plan_preview(tmp_path: Path) -> tuple[FileOperationPlan, FileOperationPreview]:
    source = tmp_path / "a.txt"
    destination = tmp_path / "b.txt"
    state = _state(source)
    operation = PlannedFileOperation(
        sequence=0,
        operation_type=OperationType.MOVE_FILE,
        tool_name="file.move",
        source=source,
        destination=destination,
        expected_source_state=state,
    )
    plan = FileOperationPlan(
        summary="move",
        user_goal="move file",
        authorized_root_ids=(uuid4(),),
        operations=(operation,),
    )
    preview = FileOperationPreview(
        transaction_id=uuid4(),
        plan_id=plan.plan_id,
        plan_digest=plan.canonical_digest(),
        items=(
            OperationPreviewItem(
                operation_id=operation.operation_id,
                sequence=0,
                status=PreviewItemStatus.READY,
                operation_type=operation.operation_type,
                source=source,
                destination=destination,
                source_state=state,
                rollback_level=RollbackLevel.FULL,
            ),
        ),
        total_size_bytes=1,
        ready_count=1,
        conflict_count=0,
        blocked_count=0,
        full_rollback_count=1,
    )
    return plan, preview


def test_file_state_rules_and_plan_shape_validation(tmp_path: Path) -> None:
    state = _state(tmp_path / "a.txt")
    assert state.identity_matches(state.model_copy(update={"path": tmp_path / "renamed.txt"}))
    assert not state.identity_matches(state.model_copy(update={"file_id": "02"}))
    assert not state.unchanged_since(state.model_copy(update={"size_bytes": 2}))

    with pytest.raises(ValidationError):
        RenameRule(rule_type=RenameRuleType.PREFIX)
    with pytest.raises(ValidationError):
        RenameRule(rule_type=RenameRuleType.REPLACE_TEXT, value="old")
    with pytest.raises(ValidationError):
        FileSelectionRule(root_ids=(), extensions=("*.txt",))
    selection = FileSelectionRule(root_ids=(uuid4(),), extensions=("TXT", ".txt"))
    assert selection.extensions == (".txt",)
    with pytest.raises(ValidationError):
        FileOperationIntentDraft(
            selection=selection,
            requested_operation=OperationType.MOVE_FILE,
        )
    with pytest.raises(ValidationError):
        PlannedFileOperation(
            sequence=0,
            operation_type=OperationType.CREATE_DIRECTORY,
            tool_name="file.move",
            destination=tmp_path / "new",
        )


def test_plan_and_preview_reject_false_contracts_and_totals(tmp_path: Path) -> None:
    plan, preview = _plan_preview(tmp_path)
    with pytest.raises(ValidationError):
        FileOperationPlan(
            summary="bad",
            user_goal="bad risk",
            authorized_root_ids=plan.authorized_root_ids,
            operations=plan.operations,
            risk_level=RiskLevel.R0,
        )
    with pytest.raises(ValidationError):
        FileOperationPreview(
            **preview.model_dump(exclude={"ready_count"}),
            ready_count=0,
        )
    changed = plan.model_copy(update={"summary": "changed"})
    assert changed.canonical_digest() != plan.canonical_digest()
    assert len(preview.canonical_digest()) == 64


def test_operation_confirmation_expiry_mismatch_replay_and_rejection(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    current = [now]
    service = OperationConfirmationService(30, now=lambda: current[0])
    with pytest.raises(ValueError):
        OperationConfirmationService(0)
    plan, preview = _plan_preview(tmp_path)
    request = service.request(plan, preview)
    approved = service.resolve(request.confirmation_id, True, plan, preview)
    assert approved.confirmed_at == now
    consumed = service.consume(request.confirmation_id, plan, preview)
    assert consumed.state.value == "CONSUMED"
    with pytest.raises(OperationConfirmationError):
        service.consume(request.confirmation_id, plan, preview)

    rejected_request = service.request(plan, preview)
    rejected = service.resolve(rejected_request.confirmation_id, False, plan, preview)
    assert rejected.confirmed_at is None
    with pytest.raises(OperationConfirmationError):
        service.resolve(rejected_request.confirmation_id, True, plan, preview)

    expiring = service.request(plan, preview)
    current[0] = now + timedelta(seconds=30)
    with pytest.raises(OperationConfirmationError, match="expired"):
        service.resolve(expiring.confirmation_id, True, plan, preview)
    with pytest.raises(OperationConfirmationError):
        service.resolve(uuid4(), True, plan, preview)
    with pytest.raises(OperationConfirmationError, match="Unknown"):
        service.consume(uuid4(), plan, preview)

    current[0] = now
    consume_expiring = service.request(plan, preview)
    service.resolve(consume_expiring.confirmation_id, True, plan, preview)
    current[0] += timedelta(seconds=30)
    with pytest.raises(OperationConfirmationError, match="expired"):
        service.consume(consume_expiring.confirmation_id, plan, preview)


def test_confirmation_rejects_changed_preview_and_empty_preview(tmp_path: Path) -> None:
    plan, preview = _plan_preview(tmp_path)
    service = OperationConfirmationService()
    request = service.request(plan, preview)
    stale = preview.model_copy(update={"total_size_bytes": 2})
    with pytest.raises(OperationConfirmationError, match="stale"):
        service.resolve(request.confirmation_id, True, plan, stale)
    empty = preview.model_copy(
        update={
            "items": (preview.items[0].model_copy(update={"status": PreviewItemStatus.CONFLICT}),),
            "ready_count": 0,
            "conflict_count": 1,
            "full_rollback_count": 0,
        }
    )
    with pytest.raises(OperationConfirmationError, match="no safe"):
        service.request(plan, empty)
    with pytest.raises(OperationConfirmationError, match="does not match"):
        service.request(plan, preview.model_copy(update={"plan_id": uuid4()}))
    with pytest.raises(OperationConfirmationError, match="count"):
        service.request(plan, preview.model_copy(update={"items": ()}))


def test_rollback_confirmation_binding_expiry_rejection_and_replay(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    current = [now]
    item = RollbackPreviewItem(
        undo_id=uuid4(),
        operation_id=uuid4(),
        sequence=0,
        status=RollbackItemStatus.READY,
        current_path=tmp_path / "new.txt",
        restore_path=tmp_path / "old.txt",
    )
    plan = RollbackPlan(transaction_id=uuid4(), items=(item,))
    service = RollbackConfirmationService(30, now=lambda: current[0])
    with pytest.raises(ValueError):
        RollbackConfirmationService(0)
    request = service.request(plan)
    service.resolve(request.confirmation_id, True, plan)
    assert service.consume(request.confirmation_id, plan).state.value == "CONSUMED"
    with pytest.raises(OperationConfirmationError):
        service.consume(request.confirmation_id, plan)

    rejected_request = service.request(plan)
    service.resolve(rejected_request.confirmation_id, False, plan)
    with pytest.raises(OperationConfirmationError):
        service.resolve(rejected_request.confirmation_id, True, plan)

    expiring = service.request(plan)
    current[0] += timedelta(seconds=30)
    with pytest.raises(OperationConfirmationError, match="expired"):
        service.resolve(expiring.confirmation_id, True, plan)
    with pytest.raises(OperationConfirmationError):
        service.resolve(uuid4(), True, plan)

    current[0] = now
    consume_expiring = service.request(plan)
    service.resolve(consume_expiring.confirmation_id, True, plan)
    current[0] += timedelta(seconds=30)
    with pytest.raises(OperationConfirmationError, match="expired"):
        service.consume(consume_expiring.confirmation_id, plan)

    current[0] = now
    stale_request = service.request(plan)
    stale_plan = plan.model_copy(update={"created_at": now + timedelta(seconds=1)})
    with pytest.raises(OperationConfirmationError, match="stale"):
        service.resolve(stale_request.confirmation_id, True, stale_plan)

    blocked_plan = RollbackPlan(
        transaction_id=uuid4(),
        items=(item.model_copy(update={"status": RollbackItemStatus.BLOCKED}),),
    )
    with pytest.raises(OperationConfirmationError, match="no safe"):
        service.request(blocked_plan)
