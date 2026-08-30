"""Fail-closed Stage 4E2 scope, authority, privacy, and fallback tests."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
from tests.fixtures.system_cleanup import (
    build_system_cleanup_environment,
    mark_old,
    save_temp_report,
)

from pc_manager_agent.domain.system_cleanup_execution import (
    CleanupEligibilityDecision,
    CleanupTransactionState,
    SystemCleanupRequest,
)
from pc_manager_agent.orchestration import system_cleanup as cleanup_orchestration
from pc_manager_agent.orchestration.optimization_report_store import (
    OptimizationReportUnavailableError,
)
from pc_manager_agent.persistence.system_cleanup import SystemCleanupRepository
from pc_manager_agent.platform_support.windows import recycle_bin_empty
from pc_manager_agent.safety import system_cleanup_revalidation
from pc_manager_agent.safety.system_cleanup_preview import SystemCleanupPreviewError
from pc_manager_agent.tools import registry as tool_registry
from pc_manager_agent.tools.file_tools import recycle_executor
from pc_manager_agent.tools.registry import ToolInputError, WriteAuthorizationError
from pc_manager_agent.tools.system_tools import system_cleanup as cleanup_tools


def test_mixed_eligible_and_protected_batch_is_rejected_as_a_whole(tmp_path: Path) -> None:
    environment = build_system_cleanup_environment(tmp_path / "state.db")
    safe = environment.temp_root / "safe.tmp"
    protected = environment.temp_root / "user-data.db"
    safe.write_bytes(b"cache")
    protected.write_bytes(b"database")
    mark_old(safe)
    mark_old(protected)
    report = save_temp_report(environment)
    try:
        assessment = environment.service.assess(
            SystemCleanupRequest(
                source_report_id=report.report_id,
                selected_candidate_ids=(report.cleanup_candidates[0].candidate_id,),
            )
        )
        assert {item.eligibility for item in assessment.items} == {
            CleanupEligibilityDecision.ELIGIBLE,
            CleanupEligibilityDecision.BLOCKED,
        }
        with pytest.raises(SystemCleanupPreviewError, match="blocked"):
            environment.service.prepare(
                assessment,
                tuple(item.item_ref for item in assessment.items),
            )
        assert environment.recycle.calls == []
        assert safe.exists() and protected.exists()
    finally:
        environment.close()


def test_old_report_intent_is_invalid_after_session_store_clear(tmp_path: Path) -> None:
    environment = build_system_cleanup_environment(tmp_path / "state.db")
    target = environment.temp_root / "old.tmp"
    target.write_bytes(b"old")
    mark_old(target)
    report = save_temp_report(environment)
    environment.report_store.clear()
    try:
        with pytest.raises(OptimizationReportUnavailableError):
            environment.service.assess(
                SystemCleanupRequest(
                    source_report_id=report.report_id,
                    selected_candidate_ids=(report.cleanup_candidates[0].candidate_id,),
                )
            )
        assert target.exists()
        assert environment.recycle.calls == []
    finally:
        environment.close()


def test_write_tool_rejects_raw_paths_and_unconfirmed_local_references(
    tmp_path: Path,
) -> None:
    environment = build_system_cleanup_environment(tmp_path / "state.db")
    target = environment.temp_root / "old.tmp"
    target.write_bytes(b"old")
    mark_old(target)
    report = save_temp_report(environment)
    try:
        with pytest.raises(ToolInputError):
            environment.registry.execute(
                "optimization.cleanup.trash",
                {
                    "source_report_id": str(report.report_id),
                    "path": str(target),
                    "force": True,
                },
            )
        assessment = environment.service.assess(
            SystemCleanupRequest(
                source_report_id=report.report_id,
                selected_candidate_ids=(report.cleanup_candidates[0].candidate_id,),
            )
        )
        prepared = environment.service.prepare(assessment, (assessment.items[0].item_ref,))
        request = environment.repository.request_for_item(
            prepared.plan,
            prepared.preview.preview_id,
            prepared.plan.items[0],
        )
        with pytest.raises(WriteAuthorizationError):
            environment.registry.execute(
                "optimization.cleanup.trash",
                request.model_dump(mode="json"),
            )
        assert target.exists()
        assert environment.recycle.calls == []
    finally:
        environment.close()


def test_recycle_failure_never_falls_back_or_claims_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = build_system_cleanup_environment(tmp_path / "state.db")
    target = environment.temp_root / "old.tmp"
    target.write_bytes(b"old")
    mark_old(target)
    report = save_temp_report(environment)
    try:
        assessment = environment.service.assess(
            SystemCleanupRequest(
                source_report_id=report.report_id,
                selected_candidate_ids=(report.cleanup_candidates[0].candidate_id,),
            )
        )
        prepared = environment.service.prepare(assessment, (assessment.items[0].item_ref,))
        environment.service.resolve_plan_confirmation(prepared, True)
        runtime = environment.service.request_runtime_confirmation(prepared)
        environment.service.resolve_runtime_confirmation(runtime, True)
        monkeypatch.setattr(
            environment.recycle,
            "recycle",
            lambda _path: (_ for _ in ()).throw(RuntimeError("synthetic Shell failure")),
        )

        result = environment.service.execute(runtime)
        assert result.failed_items + result.blocked_items == 1
        assert result.verified_items == 0
        assert target.exists()
        assert environment.service.recovery_records(result.transaction_id) == ()
    finally:
        environment.close()


def test_cleanup_audit_hashes_paths_and_never_stores_file_contents(tmp_path: Path) -> None:
    environment = build_system_cleanup_environment(tmp_path / "state.db")
    target = environment.temp_root / "private-name.tmp"
    secret = "must-not-enter-audit"
    target.write_text(secret, encoding="utf-8")
    mark_old(target)
    report = save_temp_report(environment)
    try:
        assessment = environment.service.assess(
            SystemCleanupRequest(
                source_report_id=report.report_id,
                selected_candidate_ids=(report.cleanup_candidates[0].candidate_id,),
            )
        )
        prepared = environment.service.prepare(assessment, (assessment.items[0].item_ref,))
        environment.service.resolve_plan_confirmation(prepared, True)
        runtime = environment.service.request_runtime_confirmation(prepared)
        environment.service.resolve_runtime_confirmation(runtime, True)
        environment.service.execute(runtime)
        rows = tuple(
            row
            for row in environment.audit.list_recent(100)
            if row.event_type.startswith("optimization.cleanup")
        )
        serialized = json.dumps(
            [
                {
                    "parameters": row.parameters,
                    "before": row.before_state,
                    "result": row.result,
                    "rollback": row.rollback,
                }
                for row in rows
            ],
            ensure_ascii=False,
        )
        assert rows
        assert str(target) not in serialized
        assert target.name not in serialized
        assert secret not in serialized
        assert "path_digest" in serialized
    finally:
        environment.close()


def test_restart_marks_active_cleanup_interrupted_and_never_resumes(tmp_path: Path) -> None:
    database_path = tmp_path / "state.db"
    environment = build_system_cleanup_environment(database_path)
    target = environment.temp_root / "old.tmp"
    target.write_bytes(b"old")
    mark_old(target)
    report = save_temp_report(environment)
    reopened: SystemCleanupRepository | None = None
    try:
        assessment = environment.service.assess(
            SystemCleanupRequest(
                source_report_id=report.report_id,
                selected_candidate_ids=(report.cleanup_candidates[0].candidate_id,),
            )
        )
        prepared = environment.service.prepare(assessment, (assessment.items[0].item_ref,))
        environment.repository.close()
        reopened = SystemCleanupRepository(database_path)
        interrupted = reopened.initialize()
        assert prepared.plan.transaction_id in interrupted
        assert reopened.state(prepared.plan.transaction_id) is CleanupTransactionState.INTERRUPTED
        assert environment.recycle.calls == []
        assert target.exists()
    finally:
        if reopened is not None:
            reopened.close()
        environment.audit.close()


@pytest.mark.parametrize(
    "forbidden",
    (
        "os.remove(",
        "os.unlink(",
        "shutil.rmtree(",
        ".unlink(",
        ".rmdir(",
        "DeleteFileW",
        "RemoveDirectoryW",
        "shell=True",
        "powershell",
        "cmd.exe",
    ),
)
def test_production_cleanup_has_no_permanent_delete_or_shell_fallback(forbidden: str) -> None:
    source = "\n".join(
        (
            inspect.getsource(cleanup_orchestration),
            inspect.getsource(system_cleanup_revalidation),
            inspect.getsource(cleanup_tools),
            inspect.getsource(recycle_bin_empty),
            inspect.getsource(recycle_executor),
            inspect.getsource(tool_registry),
        )
    ).casefold()
    assert forbidden.casefold() not in source


def test_stage4e2_registry_contains_only_four_exact_tools(tmp_path: Path) -> None:
    environment = build_system_cleanup_environment(tmp_path / "state.db")
    try:
        assert environment.registry.names == (
            "optimization.cleanup.prepare",
            "optimization.cleanup.trash",
            "optimization.recycle_bin.empty",
            "optimization.recycle_bin.inspect",
        )
    finally:
        environment.close()
