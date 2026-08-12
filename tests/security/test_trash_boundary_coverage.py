from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from tests.stage2b_support import FakeRecyclePlatform, FakeTrashIdentityPlatform

from pc_manager_agent.domain.file_operations import OperationType
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.trash import TrashPlan, TrashPlanItem
from pc_manager_agent.safety.path_policy import PathPolicy
from pc_manager_agent.safety.trash_policy import (
    _FILE_ATTRIBUTE_OFFLINE,
    _FILE_ATTRIBUTE_REPARSE_POINT,
    _FILE_ATTRIBUTE_SYSTEM,
    TrashPathPolicy,
)
from pc_manager_agent.safety.trash_preview import TrashPreviewEngine
from pc_manager_agent.safety.trash_validator import TrashSafetyValidator
from pc_manager_agent.tools.file_tools.move import MoveTool
from pc_manager_agent.tools.file_tools.trash import TrashTool
from pc_manager_agent.tools.registry import ToolRegistry


def _allow_test_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    protected = tmp_path / "protected-not-selected"
    for name, leaf in (
        ("LOCALAPPDATA", "local"),
        ("APPDATA", "roaming"),
        ("SYSTEMROOT", "windows"),
        ("PROGRAMDATA", "program-data"),
        ("PROGRAMFILES", "program-files"),
        ("PROGRAMFILES(X86)", "program-files-x86"),
    ):
        monkeypatch.setenv(name, str(protected / leaf))


def _plan(paths: tuple[Path, ...], identity: FakeTrashIdentityPlatform) -> TrashPlan:
    items = []
    for sequence, path in enumerate(paths):
        state = identity.inspect(path)
        operation = OperationType.RECYCLE_DIRECTORY if path.is_dir() else OperationType.RECYCLE_FILE
        items.append(
            TrashPlanItem(
                sequence=sequence,
                operation_type=operation,
                source=state.path,
                expected_source_state=state,
            )
        )
    return TrashPlan(
        summary="trash boundaries",
        user_goal="trash selected",
        authorized_root_ids=(uuid4(),),
        items=tuple(items),
    )


@pytest.mark.security
@pytest.mark.parametrize(
    "attribute, message",
    [
        (_FILE_ATTRIBUTE_REPARSE_POINT, "Reparse"),
        (_FILE_ATTRIBUTE_SYSTEM, "System objects"),
        (_FILE_ATTRIBUTE_OFFLINE, "Offline"),
    ],
)
def test_trash_policy_rejects_destructive_file_attributes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    attribute: int,
    message: str,
) -> None:
    _allow_test_root(monkeypatch, tmp_path)
    source = tmp_path / "selected.txt"
    source.write_text("fixture", encoding="utf-8")
    policy = TrashPathPolicy(PathPolicy.for_scan_root(tmp_path))
    assert policy.approved_roots == (tmp_path,)
    monkeypatch.setattr(
        "pc_manager_agent.safety.trash_policy.os.lstat",
        lambda _path: SimpleNamespace(st_file_attributes=attribute),
    )
    expected = "link or reparse|Reparse" if attribute == _FILE_ATTRIBUTE_REPARSE_POINT else message
    with pytest.raises(PermissionError, match=expected):
        policy.validate_source(source)
    assert policy.entry_rejection_reason(source) is not None


@pytest.mark.security
def test_trash_policy_safe_entry_has_no_rejection_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _allow_test_root(monkeypatch, tmp_path)
    source = tmp_path / "selected.txt"
    source.write_text("fixture", encoding="utf-8")
    policy = TrashPathPolicy(PathPolicy.for_scan_root(tmp_path))
    assert policy.entry_rejection_reason(source) is None


@pytest.mark.security
def test_preview_enforces_constructor_tree_batch_and_byte_limits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _allow_test_root(monkeypatch, tmp_path)
    identity = FakeTrashIdentityPlatform()
    recycle = FakeRecyclePlatform()
    policy = TrashPathPolicy(PathPolicy.for_scan_root(tmp_path))
    with pytest.raises(ValueError, match="positive"):
        TrashPreviewEngine(policy, identity, recycle, max_selected=0)

    first = tmp_path / "first.bin"
    second = tmp_path / "second.bin"
    first.write_bytes(b"aaa")
    second.write_bytes(b"bbb")
    plan = _plan((first, second), identity)
    selected = TrashPreviewEngine(policy, identity, recycle, max_selected=1).generate(plan)
    assert selected.blocked_count == 2
    contained = TrashPreviewEngine(policy, identity, recycle, max_contained_objects=1).generate(
        plan
    )
    assert contained.blocked_count == 2
    byte_limited = TrashPreviewEngine(policy, identity, recycle, max_total_bytes=5).generate(plan)
    assert byte_limited.blocked_count == 2
    high = TrashPreviewEngine(
        policy, identity, recycle, high_impact_objects=1, high_impact_bytes=100
    ).generate(plan)
    assert high.impact_level.value == "HIGH"

    folder = tmp_path / "folder"
    folder.mkdir()
    (folder / "child.txt").write_text("child", encoding="utf-8")
    folder_plan = _plan((folder,), identity)
    tree_limited = TrashPreviewEngine(policy, identity, recycle, max_contained_objects=1).generate(
        folder_plan
    )
    assert tree_limited.blocked_count == 1
    single_byte_limited = TrashPreviewEngine(policy, identity, recycle, max_total_bytes=1).generate(
        _plan((first,), identity)
    )
    assert single_byte_limited.blocked_count == 1


@pytest.mark.security
def test_validator_reports_every_defense_in_depth_contract_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _allow_test_root(monkeypatch, tmp_path)
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("one", encoding="utf-8")
    second.write_text("two", encoding="utf-8")
    identity = FakeTrashIdentityPlatform()
    recycle = FakeRecyclePlatform()
    policy = TrashPathPolicy(PathPolicy.for_scan_root(tmp_path))
    snapshotter = TrashPreviewEngine(policy, identity, recycle).snapshot
    registry = ToolRegistry()
    registry.register(TrashTool(policy, identity, recycle, snapshotter))
    plan = _plan((first, second), identity)
    with pytest.raises(ValueError, match="positive"):
        TrashSafetyValidator(registry, policy, max_selected=0)
    limited = TrashSafetyValidator(registry, policy, max_selected=1).review(plan)
    assert "batch-limit-exceeded" in {issue.code for issue in limited.issues}

    invalid_risk = plan.model_copy(update={"risk_level": RiskLevel.R1})
    assert "invalid-risk-contract" in {
        issue.code for issue in TrashSafetyValidator(registry, policy).review(invalid_risk).issues
    }
    unknown_item = plan.items[0].model_copy(update={"tool_name": "file.unknown"})
    unknown = plan.model_copy(update={"items": (unknown_item,)})
    assert "unknown-tool" in {
        issue.code for issue in TrashSafetyValidator(registry, policy).review(unknown).issues
    }

    registry.register(MoveTool(policy._base, identity))
    wrong_manifest_item = plan.items[0].model_copy(update={"tool_name": "file.move"})
    wrong_manifest = plan.model_copy(update={"items": (wrong_manifest_item,)})
    codes = {
        issue.code for issue in TrashSafetyValidator(registry, policy).review(wrong_manifest).issues
    }
    assert "unsafe-tool-manifest" in codes

    wrong_operation_item = plan.items[0].model_copy(
        update={"operation_type": OperationType.MOVE_FILE}
    )
    wrong_operation = plan.model_copy(update={"items": (wrong_operation_item,)})
    assert "invalid-operation-type" in {
        issue.code
        for issue in TrashSafetyValidator(registry, policy).review(wrong_operation).issues
    }

    unsafe_item = plan.items[0].model_copy(update={"source": tmp_path})
    unsafe = plan.model_copy(update={"items": (unsafe_item,)})
    assert "unsafe-source" in {
        issue.code for issue in TrashSafetyValidator(registry, policy).review(unsafe).issues
    }

    duplicate_item = plan.items[1].model_copy(
        update={
            "source": plan.items[0].source,
            "expected_source_state": plan.items[0].expected_source_state,
        }
    )
    duplicate = plan.model_copy(update={"items": (plan.items[0], duplicate_item)})
    assert "duplicate-source" in {
        issue.code for issue in TrashSafetyValidator(registry, policy).review(duplicate).issues
    }
