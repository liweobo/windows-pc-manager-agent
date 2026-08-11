from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import BaseModel

from pc_manager_agent.safety.path_policy import PathPolicy
from pc_manager_agent.tools.file_tools.create_directory import CreateDirectoryTool
from pc_manager_agent.tools.file_tools.move import MoveTool
from pc_manager_agent.tools.file_tools.operation_models import (
    CreateDirectoryRequest,
    MoveRequest,
    RemoveCreatedDirectoryRequest,
    RenameRequest,
)
from pc_manager_agent.tools.file_tools.remove_created_directory import RemoveCreatedDirectoryTool
from pc_manager_agent.tools.file_tools.rename import RenameTool
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.tools.registry import ToolRegistry, WriteAuthorizationError
from tests.stage2_support import FakeFileOperationPlatform


class EmptyModel(BaseModel):
    pass


def test_move_tool_success_conflict_change_volume_cancel_and_platform_error(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")
    destination = tmp_path / "destination.txt"
    platform = FakeFileOperationPlatform()
    policy = PathPolicy.for_scan_root(tmp_path)
    tool = MoveTool(policy, platform)
    state = platform.inspect(source)
    request = MoveRequest(
        operation_id=uuid4(),
        source=source,
        destination=destination,
        expected_source_state=state,
    )
    result = tool.execute(request, CancellationToken())
    assert result.verified
    assert destination.read_text(encoding="utf-8") == "source"

    with pytest.raises(TypeError):
        tool.execute(EmptyModel(), CancellationToken())
    cancelled = CancellationToken()
    cancelled.cancel()
    with pytest.raises(RuntimeError, match="cancelled"):
        tool.execute(
            request.model_copy(update={"source": destination, "destination": source}),
            cancelled,
        )

    source.write_text("new", encoding="utf-8")
    occupied = tmp_path / "occupied.txt"
    occupied.write_text("occupied", encoding="utf-8")
    current = platform.inspect(source)
    with pytest.raises(FileExistsError):
        tool.execute(
            request.model_copy(
                update={
                    "source": source,
                    "destination": occupied,
                    "expected_source_state": current,
                }
            ),
            CancellationToken(),
        )
    with pytest.raises(RuntimeError, match="changed"):
        tool.execute(
            request.model_copy(update={"source": source, "destination": tmp_path / "other.txt"}),
            CancellationToken(),
        )
    platform.volume_overrides[source] = 1
    platform.volume_overrides[tmp_path] = 2
    state = platform.inspect(source)
    with pytest.raises(RuntimeError, match="Cross-volume"):
        tool.execute(
            request.model_copy(
                update={
                    "source": source,
                    "destination": tmp_path / "other.txt",
                    "expected_source_state": state,
                }
            ),
            CancellationToken(),
        )


def test_rename_direct_case_only_conflict_and_temporary_failure(tmp_path: Path) -> None:
    source = tmp_path / "Report.txt"
    source.write_text("report", encoding="utf-8")
    platform = FakeFileOperationPlatform()
    policy = PathPolicy.for_scan_root(tmp_path)
    tool = RenameTool(policy, platform)
    direct = RenameRequest(
        operation_id=uuid4(),
        source=source,
        destination=tmp_path / "renamed.txt",
        expected_source_state=platform.inspect(source),
    )
    assert tool.execute(direct, CancellationToken()).verified
    renamed = tmp_path / "renamed.txt"

    occupied = tmp_path / "occupied.txt"
    occupied.write_text("occupied", encoding="utf-8")
    with pytest.raises(FileExistsError):
        tool.execute(
            direct.model_copy(
                update={
                    "source": renamed,
                    "destination": occupied,
                    "expected_source_state": platform.inspect(renamed),
                }
            ),
            CancellationToken(),
        )

    case_only = direct.model_copy(
        update={
            "source": renamed,
            "destination": tmp_path / "RENAMED.txt",
            "expected_source_state": platform.inspect(renamed),
            "internal_temporary_path": tmp_path / ".case-tmp",
        }
    )
    assert tool.execute(case_only, CancellationToken()).verified
    assert (tmp_path / "RENAMED.txt").exists()

    missing_temporary = case_only.model_copy(
        update={
            "source": tmp_path / "RENAMED.txt",
            "destination": tmp_path / "renamed.TXT",
            "expected_source_state": platform.inspect(tmp_path / "RENAMED.txt"),
            "internal_temporary_path": None,
        }
    )
    with pytest.raises(RuntimeError, match="temporary"):
        tool.execute(missing_temporary, CancellationToken())


def test_create_and_remove_created_directory_tools_are_strict(tmp_path: Path) -> None:
    platform = FakeFileOperationPlatform()
    policy = PathPolicy.for_scan_root(tmp_path)
    create = CreateDirectoryTool(policy, platform)
    destination = tmp_path / "created"
    request = CreateDirectoryRequest(operation_id=uuid4(), destination=destination)
    result = create.execute(request, CancellationToken())
    assert result.verified and destination.is_dir()
    with pytest.raises(FileExistsError):
        create.execute(request, CancellationToken())

    remove = RemoveCreatedDirectoryTool(policy, platform)
    remove_request = RemoveCreatedDirectoryRequest(
        operation_id=request.operation_id,
        path=destination,
        expected_state=platform.inspect(destination),
    )
    (destination / "new.txt").write_text("new", encoding="utf-8")
    remove_request = remove_request.model_copy(
        update={"expected_state": platform.inspect(destination)}
    )
    with pytest.raises(RuntimeError, match="no longer empty"):
        remove.execute(remove_request, CancellationToken())
    (destination / "new.txt").unlink()
    refreshed = remove_request.model_copy(update={"expected_state": platform.inspect(destination)})
    assert remove.execute(refreshed, CancellationToken()).verified
    assert not destination.exists()


def test_registry_never_executes_write_tool_without_persisted_capability(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("safe", encoding="utf-8")
    platform = FakeFileOperationPlatform()
    registry = ToolRegistry()
    registry.register(MoveTool(PathPolicy.for_scan_root(tmp_path), platform))
    request = MoveRequest(
        operation_id=uuid4(),
        source=source,
        destination=tmp_path / "destination.txt",
        expected_source_state=platform.inspect(source),
    )
    with pytest.raises(WriteAuthorizationError):
        registry.execute("file.move", request.model_dump(mode="json"))
    assert source.exists()
