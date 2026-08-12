from __future__ import annotations

from pathlib import Path

import pytest
from tests.stage2b_support import FakeRecyclePlatform, FakeTrashIdentityPlatform

from pc_manager_agent import __version__
from pc_manager_agent.audit.trash import TrashAuditLogger
from pc_manager_agent.confirmation.trash import TrashConfirmationService
from pc_manager_agent.domain.transactions import OperationItemState, TransactionState
from pc_manager_agent.domain.trash import RecycleBinResult, RecycleVerificationStatus
from pc_manager_agent.orchestration.trash_planner import TrashPlanCompiler
from pc_manager_agent.orchestration.trash_service import TrashService
from pc_manager_agent.persistence.file_operations import TransactionExecutionGuard
from pc_manager_agent.safety.path_policy import PathPolicy
from pc_manager_agent.safety.trash_policy import TrashPathPolicy
from pc_manager_agent.safety.trash_preview import TrashPreviewEngine
from pc_manager_agent.safety.trash_validator import TrashSafetyValidator
from pc_manager_agent.tools.file_tools.trash import TrashTool
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.tools.registry import ToolRegistry


def _allow_test_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    protected = tmp_path / "protected-not-selected"
    monkeypatch.setenv("LOCALAPPDATA", str(protected / "local"))
    monkeypatch.setenv("APPDATA", str(protected / "roaming"))
    monkeypatch.setenv("SYSTEMROOT", str(protected / "windows"))
    monkeypatch.setenv("PROGRAMDATA", str(protected / "program-data"))
    monkeypatch.setenv("PROGRAMFILES", str(protected / "program-files"))
    monkeypatch.setenv("PROGRAMFILES(X86)", str(protected / "program-files-x86"))


def _services(runtime, root: Path):
    identity = FakeTrashIdentityPlatform()
    recycle = FakeRecyclePlatform()
    base_policy = PathPolicy.for_scan_root(root)
    policy = TrashPathPolicy(base_policy)
    preview = TrashPreviewEngine(policy, identity, recycle)
    registry = ToolRegistry(write_guard=TransactionExecutionGuard(runtime.operation_repository))
    registry.register(TrashTool(policy, identity, recycle, preview.snapshot))
    validator = TrashSafetyValidator(registry, policy)
    confirmations = TrashConfirmationService()
    audit = TrashAuditLogger(runtime.audit, app_version=__version__, git_commit=None)
    service = TrashService(
        validator, preview, confirmations, runtime.operation_repository, registry, audit
    )
    return identity, recycle, policy, service


@pytest.mark.windows
def test_trash_flow_requires_both_confirmations_and_persists_manual_recovery(
    runtime,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _allow_test_root(monkeypatch, tmp_path)
    root = tmp_path / "authorized"
    root.mkdir()
    source = root / "selected.txt"
    source.write_text("keep fixture", encoding="utf-8")
    record = runtime.authorized_paths.add_authorized(root)
    identity, recycle, policy, service = _services(runtime, root)
    compiler = TrashPlanCompiler(runtime.authorized_paths, policy, identity)
    plan = compiler.compile("move selected to recycle bin", (source,), (record.path_id,))
    prepared = service.prepare(plan)
    with pytest.raises(RuntimeError, match="not awaiting"):
        service.request_runtime_confirmation(prepared)
    service.resolve_plan_confirmation(prepared, True)
    runtime_prepared = service.request_runtime_confirmation(prepared)
    service.resolve_runtime_confirmation(runtime_prepared, True)
    report = service.execute(runtime_prepared)

    assert report.completed_count == 1
    assert recycle.calls == [identity.inspect(source).path]
    transaction = runtime.operation_repository.get_transaction(report.transaction_id)
    assert transaction.state is TransactionState.COMPLETED
    assert (
        runtime.operation_repository.list_items(report.transaction_id)[0].state
        is OperationItemState.COMPLETED
    )
    recovery = service.recovery_records(report.transaction_id)[0]
    assert recovery.recovery_level.value == "MANUAL"
    assert recovery.status.value == "AVAILABLE"
    assert "Automatic restoration is not available" in recovery.instructions
    event_types = {event.event_type for event in runtime.audit.list_recent(20)}
    assert {"trash.previewed", "trash.plan_confirmation_resolved", "trash.completed"} <= event_types


@pytest.mark.windows
def test_cancel_after_runtime_confirmation_stops_before_platform_call(
    runtime,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _allow_test_root(monkeypatch, tmp_path)
    root = tmp_path / "authorized"
    root.mkdir()
    source = root / "selected.txt"
    source.write_text("fixture", encoding="utf-8")
    record = runtime.authorized_paths.add_authorized(root)
    identity, recycle, policy, service = _services(runtime, root)
    compiler = TrashPlanCompiler(runtime.authorized_paths, policy, identity)
    prepared = service.prepare(compiler.compile("trash selected", (source,), (record.path_id,)))
    service.resolve_plan_confirmation(prepared, True)
    immediate = service.request_runtime_confirmation(prepared)
    service.resolve_runtime_confirmation(immediate, True)
    token = CancellationToken()
    token.cancel()
    report = service.execute(immediate, token)
    assert report.completed_count == 0
    assert not recycle.calls
    assert source.exists()


@pytest.mark.windows
def test_ambiguous_shell_result_marks_item_recovery_and_transaction_unknown(
    runtime,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _allow_test_root(monkeypatch, tmp_path)
    root = tmp_path / "authorized"
    root.mkdir()
    source = root / "uncertain.txt"
    source.write_text("fixture", encoding="utf-8")
    record = runtime.authorized_paths.add_authorized(root)
    identity, recycle, policy, service = _services(runtime, root)
    recycle.result_override = RecycleBinResult(
        source=source,
        hresult=0,
        aborted=False,
        recycled=False,
        verification_status=RecycleVerificationStatus.UNKNOWN,
        message="Shell result is ambiguous",
    )
    compiler = TrashPlanCompiler(runtime.authorized_paths, policy, identity)
    prepared = service.prepare(compiler.compile("trash selected", (source,), (record.path_id,)))
    service.resolve_plan_confirmation(prepared, True)
    immediate = service.request_runtime_confirmation(prepared)
    service.resolve_runtime_confirmation(immediate, True)

    report = service.execute(immediate)

    assert (
        runtime.operation_repository.get_transaction(report.transaction_id).state
        is TransactionState.UNKNOWN
    )
    assert (
        runtime.operation_repository.list_items(report.transaction_id)[0].state
        is OperationItemState.UNKNOWN
    )
    assert service.recovery_records(report.transaction_id)[0].status.value == "UNKNOWN"
