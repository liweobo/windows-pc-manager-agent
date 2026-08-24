"""Durability and restart behavior for Stage 4D4 authorization state."""

from __future__ import annotations

from pathlib import Path

from pc_manager_agent.confirmation.residual_cleanup import (
    ResidualCleanupConfirmationState,
)
from pc_manager_agent.domain.residual_cleanup import (
    ResidualCleanupRequest,
    ResidualCleanupTransactionState,
)
from pc_manager_agent.persistence.residual_cleanup import ResidualCleanupRepository
from tests.fixtures.residual_cleanup import (
    build_residual_cleanup_environment,
    create_residual_report,
)
from tests.fixtures.software_residuals import residual_context


def test_restart_marks_nonterminal_cleanup_interrupted_and_expires_approval(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.db"
    root = tmp_path / "SyntheticProduct"
    root.mkdir()
    (root / "payload.bin").write_bytes(b"program")
    environment = build_residual_cleanup_environment(database)
    context = residual_context(root)
    try:
        report = create_residual_report(environment, context)
        candidate = next(item for item in report.candidates if item.path == root)
        assessment = environment.service.assess(
            ResidualCleanupRequest(
                source_report_id=report.report_id,
                selected_residual_ids=(candidate.candidate_id,),
            )
        )
        prepared = environment.service.prepare(assessment)
        approved = environment.service.resolve_plan_confirmation(prepared, True)
        transaction_id = prepared.plan.transaction_id
        confirmation_id = approved.confirmation_id
        environment.cleanup_repository.close()

        restarted = ResidualCleanupRepository(database)
        interrupted = restarted.initialize()
        try:
            assert interrupted == (transaction_id,)
            assert restarted.state(transaction_id) is ResidualCleanupTransactionState.INTERRUPTED
            assert restarted.get_confirmation(confirmation_id).state is (
                ResidualCleanupConfirmationState.EXPIRED
            )
        finally:
            restarted.close()
    finally:
        environment.residuals.close()
