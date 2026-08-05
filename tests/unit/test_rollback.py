from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from pc_manager_agent.domain.risk import RollbackLevel
from pc_manager_agent.rollback.base import OperationCommand, UndoRecord


def test_undo_record_is_immutable_and_truthful(tmp_path: Path) -> None:
    record = UndoRecord(
        original_path=tmp_path / "before.txt",
        new_path=tmp_path / "after.txt",
        rollback_level=RollbackLevel.FULL,
        valid_when=("destination unchanged",),
    )
    assert record.rollback_level is RollbackLevel.FULL
    with pytest.raises(ValidationError):
        record.rollback_result = "changed"  # type: ignore[misc]


def test_operation_command_is_abstract() -> None:
    with pytest.raises(TypeError):
        OperationCommand()  # type: ignore[abstract]
