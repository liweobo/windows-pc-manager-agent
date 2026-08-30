"""Stage 4E2 end-to-end gates over a synthetic Recycle Bin."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.fixtures.system_cleanup import (
    build_system_cleanup_environment,
    mark_old,
    save_temp_report,
)

from pc_manager_agent.confirmation.system_cleanup import (
    SystemCleanupConfirmationError,
    SystemCleanupConfirmationState,
)
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.system_cleanup_execution import (
    CleanupEligibilityDecision,
    CleanupRecoveryLevel,
    CleanupTransactionState,
    CleanupVerificationStatus,
    SystemCleanupRequest,
)
from pc_manager_agent.safety.recycle_bin_empty import RecycleBinEmptyPreviewError
from pc_manager_agent.safety.system_cleanup_preview import SystemCleanupPreviewError
from pc_manager_agent.tools.manifest import CancellationToken


def test_fresh_double_confirmed_cleanup_moves_only_selected_old_item(
    tmp_path: Path,
) -> None:
    environment = build_system_cleanup_environment(tmp_path / "state.db")
    selected = environment.temp_root / "old-item.tmp"
    unselected = environment.temp_root / "leave-me.tmp"
    selected.write_bytes(b"old-data")
    unselected.write_bytes(b"also-old")
    mark_old(selected)
    mark_old(unselected)
    report = save_temp_report(environment)
    try:
        assessment = environment.service.assess(
            SystemCleanupRequest(
                source_report_id=report.report_id,
                selected_candidate_ids=(report.cleanup_candidates[0].candidate_id,),
            )
        )
        assert assessment.eligible_count == 2
        selected_item = next(item for item in assessment.items if item.path == selected)

        prepared = environment.service.prepare(assessment, (selected_item.item_ref,))
        assert prepared.plan.risk_level is RiskLevel.R2
        assert prepared.preview.permanent_delete_available is False
        assert prepared.preview.recovery_summary.level is CleanupRecoveryLevel.MANUAL
        first = environment.service.resolve_plan_confirmation(prepared, True)
        assert first.state is SystemCleanupConfirmationState.APPROVED
        assert environment.recycle.calls == []

        runtime = environment.service.request_runtime_confirmation(prepared)
        second = environment.service.resolve_runtime_confirmation(runtime, True)
        assert second.state is SystemCleanupConfirmationState.APPROVED
        assert environment.recycle.calls == []

        result = environment.service.execute(runtime)
        assert result.final_state is CleanupTransactionState.COMPLETED
        assert result.verified_items == 1
        assert result.verified_disk_space_reclaimed_bytes is None
        assert not selected.exists()
        assert unselected.exists()
        assert environment.recycle.calls == [selected]
        recovery = environment.service.recovery_records(result.transaction_id)
        assert len(recovery) == 1
        assert recovery[0].recycle_item_identifier is not None
    finally:
        environment.close()


def test_runtime_confirmation_is_single_use_and_cancel_never_dispatches(
    tmp_path: Path,
) -> None:
    environment = build_system_cleanup_environment(tmp_path / "state.db")
    target = environment.temp_root / "old-item.tmp"
    target.write_bytes(b"old-data")
    mark_old(target)
    report = save_temp_report(environment)
    try:
        assessment = environment.service.assess(
            SystemCleanupRequest(
                source_report_id=report.report_id,
                selected_candidate_ids=(report.cleanup_candidates[0].candidate_id,),
            )
        )
        prepared = environment.service.prepare(
            assessment,
            (assessment.items[0].item_ref,),
        )
        environment.service.resolve_plan_confirmation(prepared, True)
        runtime = environment.service.request_runtime_confirmation(prepared)
        environment.service.resolve_runtime_confirmation(runtime, True)
        cancellation = CancellationToken()
        cancellation.cancel()

        with pytest.raises(SystemCleanupConfirmationError, match="cancelled"):
            environment.service.execute(runtime, cancellation)
        assert target.exists()
        assert environment.recycle.calls == []
        with pytest.raises(SystemCleanupConfirmationError):
            environment.service.execute(runtime)
    finally:
        environment.close()


def test_material_change_after_runtime_confirmation_fails_closed(
    tmp_path: Path,
) -> None:
    environment = build_system_cleanup_environment(tmp_path / "state.db")
    target = environment.temp_root / "old-item.tmp"
    target.write_bytes(b"version-one")
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
        target.write_bytes(b"version-two-is-different")

        with pytest.raises(SystemCleanupPreviewError, match="changed"):
            environment.service.execute(runtime)
        assert environment.repository.state(prepared.plan.transaction_id) is (
            CleanupTransactionState.BLOCKED
        )
        assert target.exists()
        assert environment.recycle.calls == []
    finally:
        environment.close()


def test_recycle_bin_empty_is_independent_twice_confirmed_and_irreversible(
    tmp_path: Path,
) -> None:
    environment = build_system_cleanup_environment(tmp_path / "state.db")
    try:
        prepared = environment.service.prepare_recycle_bin_empty()
        assert prepared.plan.risk_level is RiskLevel.R2_HIGH_IMPACT
        assert prepared.plan.recovery_level is CleanupRecoveryLevel.NONE
        assert environment.empty_platform.empty_calls == []
        environment.service.resolve_empty_plan_confirmation(prepared, True)
        runtime = environment.service.request_empty_runtime_confirmation(prepared)
        environment.service.resolve_empty_runtime_confirmation(runtime, True)
        assert environment.empty_platform.empty_calls == []

        result = environment.service.execute_recycle_bin_empty(runtime)
        assert result.verification_status is (CleanupVerificationStatus.RECYCLE_BIN_EMPTY_VERIFIED)
        assert result.recovery_level is CleanupRecoveryLevel.NONE
        assert len(environment.empty_platform.empty_calls) == 1
        with pytest.raises(RecycleBinEmptyPreviewError):
            environment.service.execute_recycle_bin_empty(runtime)
        assert len(environment.empty_platform.empty_calls) == 1
    finally:
        environment.close()


def test_recent_item_stays_blocked_in_fresh_assessment(tmp_path: Path) -> None:
    environment = build_system_cleanup_environment(tmp_path / "state.db")
    target = environment.temp_root / "recent-item.tmp"
    target.write_bytes(b"recent")
    report = save_temp_report(environment)
    try:
        assessment = environment.service.assess(
            SystemCleanupRequest(
                source_report_id=report.report_id,
                selected_candidate_ids=(report.cleanup_candidates[0].candidate_id,),
            )
        )
        assert assessment.items[0].eligibility is CleanupEligibilityDecision.BLOCKED
        assert "recent-metadata-change" in assessment.items[0].reason_codes
        assert environment.recycle.calls == []
    finally:
        environment.close()
