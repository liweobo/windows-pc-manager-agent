from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from pc_manager_agent.domain.file_operations import FileObjectKind, FileState, OperationType
from pc_manager_agent.domain.risk import RollbackLevel
from pc_manager_agent.rollback.base import OperationCommand, UndoRecord


def test_undo_record_is_immutable_and_truthful(tmp_path: Path) -> None:
    original = tmp_path / "before.txt"
    resulting = tmp_path / "after.txt"
    record = UndoRecord(
        operation_id=uuid4(),
        transaction_id=uuid4(),
        sequence=0,
        operation_type=OperationType.MOVE_FILE,
        original_path=original,
        resulting_path=resulting,
        before_state=FileState(
            path=original,
            kind=FileObjectKind.FILE,
            volume_serial=1,
            file_id="01",
            size_bytes=1,
            created_ns=1,
            modified_ns=1,
            attributes=0,
        ),
        rollback_level=RollbackLevel.FULL,
        valid_when=("destination unchanged",),
    )
    assert record.rollback_level is RollbackLevel.FULL
    with pytest.raises(ValidationError):
        record.rollback_result = "changed"  # type: ignore[misc]


def test_operation_command_is_abstract() -> None:
    with pytest.raises(TypeError):
        OperationCommand()  # type: ignore[abstract]
