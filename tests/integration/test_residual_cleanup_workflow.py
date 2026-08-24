"""Confirmed Stage 4D4 workflow over a recoverable synthetic Recycle Bin."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.fixtures.residual_cleanup import (
    build_residual_cleanup_environment,
    create_residual_report,
)
from tests.fixtures.software_residuals import residual_context

from pc_manager_agent.confirmation.residual_cleanup import (
    ResidualCleanupConfirmationError,
    ResidualCleanupConfirmationState,
)
from pc_manager_agent.domain.residual_cleanup import (
    CleanupEligibilityDecision,
    ResidualCleanupRequest,
    ResidualCleanupTransactionState,
    ResidualVerificationStatus,
)
from pc_manager_agent.domain.risk import RollbackLevel
from pc_manager_agent.tools.manifest import CancellationToken


def test_fresh_double_confirmed_cleanup_moves_to_synthetic_recycle_bin(
    tmp_path: Path,
) -> None:
    root = tmp_path / "SyntheticProduct"
    root.mkdir()
    payload = root / "payload.bin"
    payload.write_bytes(b"application-binary")
    environment = build_residual_cleanup_environment(tmp_path / "state.db")
    context = residual_context(root)
    try:
        report = create_residual_report(environment, context)
        selected = next(candidate for candidate in report.candidates if candidate.path == root)
        request = ResidualCleanupRequest(
            source_report_id=report.report_id,
            selected_residual_ids=(selected.candidate_id,),
        )
        assessment = environment.service.assess(request)
        assert assessment.all_eligible, assessment.items[0].eligibility_reason_codes
        assert assessment.items[0].eligibility is CleanupEligibilityDecision.ELIGIBLE
        assert root.exists()

        prepared = environment.service.prepare(assessment)
        assert prepared.plan.rollback_level is RollbackLevel.MANUAL
        plan_confirmation = environment.service.resolve_plan_confirmation(prepared, True)
        assert plan_confirmation.state is ResidualCleanupConfirmationState.APPROVED
        assert environment.recycle.calls == []

        runtime = environment.service.request_runtime_confirmation(prepared)
        immediate = environment.service.resolve_runtime_confirmation(runtime, True)
        assert immediate.state is ResidualCleanupConfirmationState.APPROVED
        assert environment.recycle.calls == []

        result = environment.service.execute(runtime)
        assert result.final_state is ResidualCleanupTransactionState.COMPLETED
        assert result.completed_count == 1
        assert result.failed_count == 0
        assert result.skipped_count == 0
        assert result.results[0].verification_status in {
            ResidualVerificationStatus.ORIGINAL_IDENTITY_REMOVED,
            ResidualVerificationStatus.ORIGINAL_REMOVED_NEW_OBJECT_PRESENT,
        }
        assert not root.exists()
        assert len(environment.recycle.calls) == 1
        recovery = environment.service.recovery_records(result.transaction_id)
        assert len(recovery) == 1
        assert recovery[0].recovery_level is RollbackLevel.MANUAL
        assert recovery[0].recycle_item_identifier is not None
    finally:
        environment.close()


def test_cleanup_requires_both_confirmations_and_runtime_confirmation_is_single_use(
    tmp_path: Path,
) -> None:
    root = tmp_path / "SyntheticProduct"
    root.mkdir()
    (root / "payload.bin").write_bytes(b"x")
    environment = build_residual_cleanup_environment(tmp_path / "state.db")
    context = residual_context(root)
    try:
        report = create_residual_report(environment, context)
        selected = next(candidate for candidate in report.candidates if candidate.path == root)
        assessment = environment.service.assess(
            ResidualCleanupRequest(
                source_report_id=report.report_id,
                selected_residual_ids=(selected.candidate_id,),
            )
        )
        prepared = environment.service.prepare(assessment)
        environment.service.resolve_plan_confirmation(prepared, True)
        runtime = environment.service.request_runtime_confirmation(prepared)
        assert environment.recycle.calls == []

        environment.service.resolve_runtime_confirmation(runtime, True)
        environment.service.execute(runtime)
        assert len(environment.recycle.calls) == 1
        try:
            environment.service.execute(runtime)
        except ResidualCleanupConfirmationError:
            pass
        else:
            raise AssertionError("Consumed runtime confirmation was replayed")
        assert len(environment.recycle.calls) == 1
    finally:
        environment.close()


def test_cancel_before_dispatch_invalidates_transaction_without_recycling(
    tmp_path: Path,
) -> None:
    root = tmp_path / "SyntheticProduct"
    root.mkdir()
    (root / "payload.bin").write_bytes(b"x")
    environment = build_residual_cleanup_environment(tmp_path / "state.db")
    context = residual_context(root)
    try:
        report = create_residual_report(environment, context)
        selected = next(candidate for candidate in report.candidates if candidate.path == root)
        assessment = environment.service.assess(
            ResidualCleanupRequest(
                source_report_id=report.report_id,
                selected_residual_ids=(selected.candidate_id,),
            )
        )
        prepared = environment.service.prepare(assessment)
        environment.service.resolve_plan_confirmation(prepared, True)
        runtime = environment.service.request_runtime_confirmation(prepared)
        environment.service.resolve_runtime_confirmation(runtime, True)
        cancellation = CancellationToken()
        cancellation.cancel()
        with pytest.raises(ResidualCleanupConfirmationError, match="cancelled"):
            environment.service.execute(runtime, cancellation)
        assert environment.cleanup_repository.state(prepared.plan.transaction_id) is (
            ResidualCleanupTransactionState.CANCELLED
        )
        assert environment.recycle.calls == []
        assert root.exists()
        with pytest.raises(ResidualCleanupConfirmationError, match="already used"):
            environment.service.execute(runtime)
    finally:
        environment.close()


def test_cancel_after_first_item_reports_partial_and_skips_future_items(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "SyntheticProduct"
    root.mkdir()
    first = root / "first.bin"
    second = root / "second.bin"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    environment = build_residual_cleanup_environment(tmp_path / "state.db")
    context = residual_context(root)
    cancellation = CancellationToken()
    try:
        report = create_residual_report(environment, context)
        by_path = {candidate.path: candidate for candidate in report.candidates}
        assessment = environment.service.assess(
            ResidualCleanupRequest(
                source_report_id=report.report_id,
                selected_residual_ids=(
                    by_path[first].candidate_id,
                    by_path[second].candidate_id,
                ),
            )
        )
        prepared = environment.service.prepare(assessment)
        environment.service.resolve_plan_confirmation(prepared, True)
        runtime = environment.service.request_runtime_confirmation(prepared)
        environment.service.resolve_runtime_confirmation(runtime, True)
        original_recycle = environment.recycle.recycle

        def recycle_then_cancel(path: Path):
            result = original_recycle(path)
            cancellation.cancel()
            return result

        monkeypatch.setattr(environment.recycle, "recycle", recycle_then_cancel)
        result = environment.service.execute(runtime, cancellation)
        assert result.final_state is ResidualCleanupTransactionState.PARTIALLY_COMPLETED
        assert result.completed_count == 1
        assert result.failed_count == 0
        assert result.skipped_count == 1
        assert len(environment.recycle.calls) == 1
        assert sum(path.exists() for path in (first, second)) == 1
        assert len(environment.service.recovery_records(result.transaction_id)) == 1
    finally:
        environment.close()
