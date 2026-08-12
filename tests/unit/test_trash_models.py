from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from pc_manager_agent.domain.file_operations import FileObjectKind, FileState, OperationType
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.trash import (
    RecycleBinCapability,
    TrashPlan,
    TrashPlanItem,
)


def _state(path: Path, kind: FileObjectKind = FileObjectKind.FILE) -> FileState:
    return FileState(
        path=path,
        kind=kind,
        volume_serial=1,
        file_id="01",
        size_bytes=1,
        created_ns=1,
        modified_ns=2,
        attributes=0,
    )


def test_trash_plan_requires_r2_two_confirmations_and_manual_recovery(tmp_path: Path) -> None:
    source = tmp_path / "a.txt"
    item = TrashPlanItem(
        sequence=0,
        operation_type=OperationType.RECYCLE_FILE,
        source=source,
        expected_source_state=_state(source),
    )
    plan = TrashPlan(
        summary="trash",
        user_goal="move selected file to recycle bin",
        authorized_root_ids=(uuid4(),),
        items=(item,),
    )
    assert plan.risk_level is RiskLevel.R2
    assert plan.requires_plan_confirmation
    assert plan.requires_runtime_confirmation
    assert plan.rollback_level is RollbackLevel.MANUAL
    assert len(plan.canonical_digest()) == 64
    with pytest.raises(ValidationError):
        TrashPlan(
            summary="bad",
            user_goal="bad",
            authorized_root_ids=(uuid4(),),
            items=(item,),
            risk_level=RiskLevel.R1,
        )


def test_trash_item_rejects_kind_mismatch_and_capability_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "folder"
    with pytest.raises(ValidationError):
        TrashPlanItem(
            sequence=0,
            operation_type=OperationType.RECYCLE_FILE,
            source=source,
            expected_source_state=_state(source, FileObjectKind.DIRECTORY),
        )
    with pytest.raises(ValidationError):
        RecycleBinCapability(available=False)
    with pytest.raises(ValidationError):
        RecycleBinCapability(
            available=True,
            volume_root=Path("C:/"),
            filesystem="FAT32",
            fixed_drive=True,
            hotplug=False,
            recycle_bin_query_succeeded=True,
        )
