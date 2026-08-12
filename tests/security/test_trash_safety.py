from __future__ import annotations

import ast
from pathlib import Path
from uuid import uuid4

import pytest
from tests.stage2b_support import FakeRecyclePlatform, FakeTrashIdentityPlatform

from pc_manager_agent.domain.file_operations import FileObjectKind, OperationType
from pc_manager_agent.domain.trash import TrashPlan, TrashPlanItem
from pc_manager_agent.orchestration.trash_planner import (
    TrashIntentDecision,
    classify_trash_intent,
)
from pc_manager_agent.safety.path_policy import PathPolicy, PathSecurityError
from pc_manager_agent.safety.trash_policy import TrashPathPolicy
from pc_manager_agent.safety.trash_preview import TrashPreviewEngine
from pc_manager_agent.safety.trash_validator import TrashSafetyValidator
from pc_manager_agent.tools.file_tools.trash import TrashTool
from pc_manager_agent.tools.registry import ToolRegistry


def _allow_test_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    protected = tmp_path / "protected-not-selected"
    monkeypatch.setenv("LOCALAPPDATA", str(protected / "local"))
    monkeypatch.setenv("APPDATA", str(protected / "roaming"))
    monkeypatch.setenv("SYSTEMROOT", str(protected / "windows"))
    monkeypatch.setenv("PROGRAMDATA", str(protected / "program-data"))
    monkeypatch.setenv("PROGRAMFILES", str(protected / "program-files"))
    monkeypatch.setenv("PROGRAMFILES(X86)", str(protected / "program-files-x86"))


@pytest.mark.security
@pytest.mark.parametrize(
    "text",
    [
        "永久删除这些文件",
        "彻底删除并跳过回收站",
        "清空回收站",
        "permanently delete selected files",
        "empty recycle bin",
    ],
)
def test_permanent_delete_intent_is_refused_deterministically(text: str) -> None:
    assert classify_trash_intent(text) is TrashIntentDecision.PROHIBITED_PERMANENT_DELETE
    assert (
        classify_trash_intent("move selected files to recycle bin")
        is TrashIntentDecision.RECYCLE_BIN
    )
    assert classify_trash_intent("删除选中的文件") is TrashIntentDecision.RECYCLE_BIN
    assert classify_trash_intent("delete selected files") is TrashIntentDecision.RECYCLE_BIN
    assert classify_trash_intent("find large files") is TrashIntentDecision.NONE


@pytest.mark.security
def test_trash_policy_blocks_system_and_authorized_root_itself(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "authorized"
    root.mkdir()
    system = tmp_path / "Windows"
    system.mkdir()
    monkeypatch.setenv("SYSTEMROOT", str(system))
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "ProgramData"))
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path / "Program Files"))
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path / "Program Files (x86)"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData/Local"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData/Roaming"))
    policy = TrashPathPolicy(PathPolicy.for_authorized_roots((root, system)))
    with pytest.raises(PathSecurityError, match="root itself"):
        policy.validate_source(root)
    protected_file = system / "config.txt"
    protected_file.write_text("system", encoding="utf-8")
    with pytest.raises(PathSecurityError, match="System and application-data"):
        policy.validate_source(protected_file)


@pytest.mark.security
def test_unavailable_capability_and_batch_limit_never_call_recycle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _allow_test_root(monkeypatch, tmp_path)
    source = tmp_path / "selected.txt"
    source.write_text("safe", encoding="utf-8")
    identity = FakeTrashIdentityPlatform()
    recycle = FakeRecyclePlatform(available=False)
    state = identity.inspect(source)
    plan = TrashPlan(
        summary="trash",
        user_goal="trash selected",
        authorized_root_ids=(uuid4(),),
        items=(
            TrashPlanItem(
                sequence=0,
                operation_type=OperationType.RECYCLE_FILE,
                source=state.path,
                expected_source_state=state,
            ),
        ),
    )
    policy = TrashPathPolicy(PathPolicy.for_scan_root(tmp_path))
    preview = TrashPreviewEngine(policy, identity, recycle, max_selected=1).generate(plan)
    assert preview.blocked_count == 1
    assert not recycle.calls


@pytest.mark.security
def test_validator_rejects_overlapping_parent_and_child(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _allow_test_root(monkeypatch, tmp_path)
    folder = tmp_path / "folder"
    folder.mkdir()
    child = folder / "child.txt"
    child.write_text("child", encoding="utf-8")
    identity = FakeTrashIdentityPlatform()
    recycle = FakeRecyclePlatform()
    policy = TrashPathPolicy(PathPolicy.for_scan_root(tmp_path))
    preview = TrashPreviewEngine(policy, identity, recycle)
    registry = ToolRegistry()
    registry.register(TrashTool(policy, identity, recycle, preview.snapshot))
    folder_state = identity.inspect(folder)
    child_state = identity.inspect(child)
    assert folder_state.kind is FileObjectKind.DIRECTORY
    plan = TrashPlan(
        summary="overlap",
        user_goal="trash parent and child",
        authorized_root_ids=(uuid4(),),
        items=(
            TrashPlanItem(
                sequence=0,
                operation_type=OperationType.RECYCLE_FILE,
                source=child_state.path,
                expected_source_state=child_state,
            ),
            TrashPlanItem(
                sequence=1,
                operation_type=OperationType.RECYCLE_DIRECTORY,
                source=folder_state.path,
                expected_source_state=folder_state,
            ),
        ),
    )
    review = TrashSafetyValidator(registry, policy).review(plan)
    assert "overlapping-sources" in {issue.code for issue in review.issues}


@pytest.mark.security
def test_production_code_contains_no_permanent_file_delete_calls() -> None:
    root = Path(__file__).parents[2] / "src" / "pc_manager_agent"
    forbidden_attributes = {"unlink", "rmtree"}
    forbidden_module_calls = {("os", "remove")}
    violations: list[str] = []
    for source_path in root.rglob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and (
                    node.func.attr in forbidden_attributes
                    or (
                        isinstance(node.func.value, ast.Name)
                        and (node.func.value.id, node.func.attr) in forbidden_module_calls
                    )
                )
            ):
                violations.append(f"{source_path}:{node.lineno}:{node.func.attr}")
    # Stage 2A rollback uses the explicit Win32 RemoveDirectoryW adapter, not any of these
    # permanent file deletion helpers. Stage 2B must not introduce them anywhere in production.
    assert not violations, violations
