from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from pc_manager_agent.confirmation.trash import (
    TrashConfirmationError,
    TrashConfirmationService,
    TrashConfirmationState,
)
from pc_manager_agent.domain.file_operations import FileObjectKind, OperationType
from pc_manager_agent.domain.trash import TrashPlan, TrashPlanItem
from pc_manager_agent.safety.path_policy import PathPolicy
from pc_manager_agent.safety.trash_policy import TrashPathPolicy
from pc_manager_agent.safety.trash_preview import TrashPreviewEngine
from tests.stage2b_support import FakeRecyclePlatform, FakeTrashIdentityPlatform


def _allow_test_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    protected = tmp_path / "protected-not-selected"
    monkeypatch.setenv("LOCALAPPDATA", str(protected / "local"))
    monkeypatch.setenv("APPDATA", str(protected / "roaming"))
    monkeypatch.setenv("SYSTEMROOT", str(protected / "windows"))
    monkeypatch.setenv("PROGRAMDATA", str(protected / "program-data"))
    monkeypatch.setenv("PROGRAMFILES", str(protected / "program-files"))
    monkeypatch.setenv("PROGRAMFILES(X86)", str(protected / "program-files-x86"))


def _plan_preview(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _allow_test_root(monkeypatch, tmp_path)
    source = tmp_path / "a.txt"
    source.write_text("data", encoding="utf-8")
    identity = FakeTrashIdentityPlatform()
    state = identity.inspect(source)
    assert state.kind is FileObjectKind.FILE
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
    engine = TrashPreviewEngine(policy, identity, FakeRecyclePlatform())
    return plan, engine, engine.generate(plan)


def test_trash_confirmation_requires_both_levels_and_rejects_replay(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    service = TrashConfirmationService(300, 60, now=lambda: now)
    plan, engine, preview = _plan_preview(monkeypatch, tmp_path)
    first = service.request_plan(plan, preview)
    with pytest.raises(TrashConfirmationError, match="approved plan"):
        service.request_runtime(first.confirmation_id, plan, preview)
    service.resolve_plan(first.confirmation_id, True, plan, preview)
    current = engine.require_unchanged(plan, preview)
    second = service.request_runtime(first.confirmation_id, plan, current)
    service.resolve_runtime(second.confirmation_id, True, plan, current)
    assert service.consume_runtime(second.confirmation_id, plan, current).state.value == "CONSUMED"
    with pytest.raises(TrashConfirmationError):
        service.consume_runtime(second.confirmation_id, plan, current)


def test_runtime_confirmation_expires_and_changes_invalidate_binding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    current_time = [datetime(2026, 1, 1, tzinfo=UTC)]
    service = TrashConfirmationService(300, 30, now=lambda: current_time[0])
    plan, engine, preview = _plan_preview(monkeypatch, tmp_path)
    first = service.request_plan(plan, preview)
    service.resolve_plan(first.confirmation_id, True, plan, preview)
    live = engine.require_unchanged(plan, preview)
    runtime = service.request_runtime(first.confirmation_id, plan, live)
    current_time[0] += timedelta(seconds=30)
    with pytest.raises(TrashConfirmationError, match="expired"):
        service.resolve_runtime(runtime.confirmation_id, True, plan, live)
    stale = live.model_copy(update={"total_size_bytes": live.total_size_bytes + 1})
    with pytest.raises(TrashConfirmationError):
        service.request_runtime(first.confirmation_id, plan, stale)


def test_directory_change_invalidates_runtime_revalidation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _allow_test_root(monkeypatch, tmp_path)
    folder = tmp_path / "folder"
    folder.mkdir()
    identity = FakeTrashIdentityPlatform()
    state = identity.inspect(folder)
    plan = TrashPlan(
        summary="trash folder",
        user_goal="trash selected folder",
        authorized_root_ids=(uuid4(),),
        items=(
            TrashPlanItem(
                sequence=0,
                operation_type=OperationType.RECYCLE_DIRECTORY,
                source=state.path,
                expected_source_state=state,
            ),
        ),
    )
    engine = TrashPreviewEngine(
        TrashPathPolicy(PathPolicy.for_scan_root(tmp_path)), identity, FakeRecyclePlatform()
    )
    preview = engine.generate(plan)
    (folder / "new.txt").write_text("changed", encoding="utf-8")
    with pytest.raises(PermissionError, match="changed"):
        engine.require_unchanged(plan, preview)


def test_confirmation_rejects_invalid_ttl_unknown_ids_and_wrong_tiers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="positive"):
        TrashConfirmationService(0, 1)
    service = TrashConfirmationService()
    plan, _engine, preview = _plan_preview(monkeypatch, tmp_path)
    first = service.request_plan(plan, preview)
    with pytest.raises(TrashConfirmationError, match="Runtime confirmation"):
        service.consume_runtime(first.confirmation_id, plan, preview)
    with pytest.raises(TrashConfirmationError, match="Missing"):
        service._get(None)
    with pytest.raises(TrashConfirmationError, match="Unknown"):
        service._get(uuid4())
    service.resolve_plan(first.confirmation_id, False, plan, preview)
    with pytest.raises(TrashConfirmationError, match="already resolved"):
        service.resolve_plan(first.confirmation_id, True, plan, preview)


def test_confirmation_rejects_every_changed_binding_and_expired_parent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    clock = [datetime(2026, 1, 1, tzinfo=UTC)]
    service = TrashConfirmationService(10, 30, now=lambda: clock[0])
    plan, engine, preview = _plan_preview(monkeypatch, tmp_path)
    first = service.request_plan(plan, preview)
    service.resolve_plan(first.confirmation_id, True, plan, preview)
    live = engine.require_unchanged(plan, preview)
    runtime = service.request_runtime(first.confirmation_id, plan, live)
    service.resolve_runtime(runtime.confirmation_id, True, plan, live)

    with pytest.raises(TrashConfirmationError, match="transaction changed"):
        service.consume_runtime(
            runtime.confirmation_id,
            plan,
            live.model_copy(update={"transaction_id": uuid4()}),
        )
    with pytest.raises(TrashConfirmationError, match="Preview changed"):
        service.consume_runtime(
            runtime.confirmation_id,
            plan,
            live.model_copy(update={"preview_id": uuid4()}),
        )
    with pytest.raises(TrashConfirmationError, match="stale or mismatched"):
        service.consume_runtime(
            runtime.confirmation_id,
            plan,
            live.model_copy(update={"object_set_digest": "f" * 64}),
        )
    clock[0] += timedelta(seconds=11)
    with pytest.raises(TrashConfirmationError, match="expired"):
        service.consume_runtime(runtime.confirmation_id, plan, live)


def test_runtime_consume_fails_if_parent_approval_was_revoked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    service = TrashConfirmationService()
    plan, engine, preview = _plan_preview(monkeypatch, tmp_path)
    first = service.request_plan(plan, preview)
    service.resolve_plan(first.confirmation_id, True, plan, preview)
    live = engine.require_unchanged(plan, preview)
    runtime = service.request_runtime(first.confirmation_id, plan, live)
    service.resolve_runtime(runtime.confirmation_id, True, plan, live)
    service._requests[first.confirmation_id] = first.model_copy(
        update={"state": TrashConfirmationState.REJECTED}
    )
    with pytest.raises(TrashConfirmationError, match="no longer approved"):
        service.consume_runtime(runtime.confirmation_id, plan, live)
