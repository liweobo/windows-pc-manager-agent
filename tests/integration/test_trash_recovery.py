from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from pc_manager_agent.domain.file_operations import FileObjectKind, FileState, OperationType
from pc_manager_agent.domain.transactions import OperationItemState, TransactionState
from pc_manager_agent.domain.trash import (
    RecycleBinCapability,
    TrashImpactLevel,
    TrashObjectSnapshot,
    TrashPlan,
    TrashPlanItem,
    TrashPreview,
    TrashPreviewItem,
    TrashPreviewStatus,
)
from pc_manager_agent.orchestration.trash_service import build_trash_arguments
from pc_manager_agent.persistence.file_operations import OperationRepository
from pc_manager_agent.recovery.models import RecoveryStatus, TrashRecoveryRecord


def _state(path: Path) -> FileState:
    return FileState(
        path=path,
        kind=FileObjectKind.FILE,
        volume_serial=1,
        file_id="01",
        size_bytes=1,
        created_ns=1,
        modified_ns=2,
        attributes=0,
    )


@pytest.mark.windows
def test_restart_marks_running_trash_item_and_recovery_unknown(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    source = tmp_path / "selected.txt"
    source.write_text("fixture", encoding="utf-8")
    state = _state(source)
    item = TrashPlanItem(
        sequence=0,
        operation_type=OperationType.RECYCLE_FILE,
        source=source,
        expected_source_state=state,
    )
    plan = TrashPlan(
        summary="trash",
        user_goal="trash selected",
        authorized_root_ids=(uuid4(),),
        items=(item,),
    )
    snapshot = TrashObjectSnapshot(
        source=source,
        root_state=state,
        tree_digest="a" * 64,
        object_count=1,
        total_size_bytes=1,
        largest_item_bytes=1,
        hidden_count=0,
        system_count=0,
        reparse_count=0,
        offline_count=0,
    )
    capability = RecycleBinCapability(
        available=True,
        volume_root=Path("C:/"),
        filesystem="NTFS",
        volume_serial=1,
        fixed_drive=True,
        hotplug=False,
        recycle_bin_query_succeeded=True,
    )
    preview_item = TrashPreviewItem(
        operation_id=item.operation_id,
        sequence=0,
        source=source,
        status=TrashPreviewStatus.READY,
        snapshot=snapshot,
        capability=capability,
    )
    preview = TrashPreview(
        transaction_id=uuid4(),
        plan_id=plan.plan_id,
        plan_digest=plan.canonical_digest(),
        items=(preview_item,),
        object_set_digest="b" * 64,
        selected_count=1,
        ready_count=1,
        blocked_count=0,
        contained_object_count=1,
        total_size_bytes=1,
        largest_item_bytes=1,
        impact_level=TrashImpactLevel.NORMAL,
    )
    repository = OperationRepository(database)
    repository.initialize()
    repository.create_trash_from_preview(plan, preview, build_trash_arguments(plan, preview))
    repository.transition(preview.transaction_id, TransactionState.AWAITING_CONFIRMATION)
    repository.transition(preview.transaction_id, TransactionState.AWAITING_RUNTIME_CONFIRMATION)
    repository.transition(preview.transaction_id, TransactionState.CONFIRMED)
    repository.transition(preview.transaction_id, TransactionState.RUNNING)
    recovery = TrashRecoveryRecord(
        transaction_id=preview.transaction_id,
        operation_id=item.operation_id,
        sequence=0,
        original_path=source,
        before_state=state,
    )
    repository.begin_trash_operation(preview.transaction_id, item.operation_id, recovery)
    repository.close()

    replacement = OperationRepository(database)
    interrupted = replacement.initialize()
    try:
        assert interrupted == (preview.transaction_id,)
        assert (
            replacement.get_transaction(preview.transaction_id).state
            is TransactionState.INTERRUPTED
        )
        assert replacement.get_item(item.operation_id).state is OperationItemState.UNKNOWN
        recovered = replacement.get_recovery(item.operation_id)
        assert recovered.status is RecoveryStatus.UNKNOWN
        assert "manual inspection" in recovered.instructions or recovered.result_message
    finally:
        replacement.close()
