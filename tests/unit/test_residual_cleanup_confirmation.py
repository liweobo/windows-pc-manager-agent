"""Expiry, binding, and replay branches for Stage 4D4 confirmations."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from pc_manager_agent.confirmation.residual_cleanup import (
    ResidualCleanupConfirmationError,
    ResidualCleanupConfirmationService,
    ResidualCleanupConfirmationState,
)
from pc_manager_agent.domain.residual_cleanup import ResidualCleanupRequest
from tests.fixtures.residual_cleanup import (
    build_residual_cleanup_environment,
    create_residual_report,
)
from tests.fixtures.software_residuals import residual_context


def _prepared_case(tmp_path: Path):
    root = tmp_path / "SyntheticProduct"
    root.mkdir()
    (root / "app.bin").write_bytes(b"program")
    environment = build_residual_cleanup_environment(tmp_path / "state.db")
    context = residual_context(root)
    report = create_residual_report(environment, context)
    candidate = next(item for item in report.candidates if item.path == root)
    assessment = environment.service.assess(
        ResidualCleanupRequest(
            source_report_id=report.report_id,
            selected_residual_ids=(candidate.candidate_id,),
        )
    )
    return environment, environment.service.prepare(assessment)


def test_confirmation_constructor_rejects_nonpositive_ttls(tmp_path: Path) -> None:
    environment, _prepared = _prepared_case(tmp_path)
    try:
        with pytest.raises(ValueError, match="positive"):
            ResidualCleanupConfirmationService(
                environment.cleanup_repository,
                environment.preview,
                plan_ttl_seconds=0,
                runtime_ttl_seconds=1,
            )
    finally:
        environment.close()


def test_confirmation_rejects_double_resolution_unapproved_parent_and_pending_consume(
    tmp_path: Path,
) -> None:
    environment, prepared = _prepared_case(tmp_path)
    try:
        environment.service.resolve_plan_confirmation(prepared, False)
        with pytest.raises(ResidualCleanupConfirmationError, match="already resolved"):
            environment.confirmations.resolve(
                prepared.plan_confirmation.confirmation_id,
                True,
                prepared.plan,
                prepared.preview,
            )
        with pytest.raises(ResidualCleanupConfirmationError, match="approved"):
            environment.confirmations.request_runtime(
                prepared.plan_confirmation.confirmation_id,
                prepared.plan,
                prepared.preview,
            )
        with pytest.raises(ResidualCleanupConfirmationError, match="absent"):
            environment.confirmations.consume_runtime(
                prepared.plan_confirmation.confirmation_id,
                prepared.plan,
                prepared.preview,
            )
    finally:
        environment.close()


def test_confirmation_expiry_is_persisted_before_rejection(tmp_path: Path) -> None:
    environment, prepared = _prepared_case(tmp_path)
    try:
        future = prepared.plan_confirmation.expires_at + timedelta(seconds=1)
        service = ResidualCleanupConfirmationService(
            environment.cleanup_repository,
            environment.preview,
            plan_ttl_seconds=1,
            runtime_ttl_seconds=1,
            now=lambda: future,
        )
        with pytest.raises(ResidualCleanupConfirmationError, match="expired"):
            service.resolve(
                prepared.plan_confirmation.confirmation_id,
                True,
                prepared.plan,
                prepared.preview,
            )
        assert (
            environment.cleanup_repository.get_confirmation(
                prepared.plan_confirmation.confirmation_id
            ).state
            is ResidualCleanupConfirmationState.EXPIRED
        )
    finally:
        environment.close()


def test_confirmation_binding_detects_transaction_preview_and_evidence_changes(
    tmp_path: Path,
) -> None:
    environment, prepared = _prepared_case(tmp_path)
    confirmation = prepared.plan_confirmation
    try:
        with pytest.raises(ResidualCleanupConfirmationError, match="transaction"):
            environment.confirmations._require_binding(
                confirmation.model_copy(update={"transaction_id": uuid4()}),
                prepared.plan,
                prepared.preview,
            )
        changed_preview = prepared.preview.model_copy(update={"preview_id": uuid4()})
        with pytest.raises(ResidualCleanupConfirmationError, match="Preview changed"):
            environment.confirmations._require_binding(
                confirmation,
                prepared.plan,
                changed_preview,
            )
        with pytest.raises(ResidualCleanupConfirmationError, match="evidence"):
            environment.confirmations._require_binding(
                confirmation.model_copy(update={"total_bytes": confirmation.total_bytes + 1}),
                prepared.plan,
                prepared.preview,
            )
    finally:
        environment.close()


def test_stale_parent_blocks_runtime_consumption(tmp_path: Path) -> None:
    environment, prepared = _prepared_case(tmp_path)
    try:
        environment.service.resolve_plan_confirmation(prepared, True)
        runtime = environment.service.request_runtime_confirmation(prepared)
        approved_runtime = environment.service.resolve_runtime_confirmation(runtime, True)
        parent = environment.cleanup_repository.get_confirmation(
            prepared.plan_confirmation.confirmation_id
        )
        environment.cleanup_repository.update_confirmation(
            parent.model_copy(update={"state": ResidualCleanupConfirmationState.REJECTED})
        )
        with pytest.raises(ResidualCleanupConfirmationError, match="stale"):
            environment.confirmations.consume_runtime(
                approved_runtime.confirmation_id,
                prepared.plan,
                runtime.preview,
            )
    finally:
        environment.close()


def test_digest_helpers_fail_closed_for_incomplete_internal_candidates(
    tmp_path: Path,
) -> None:
    environment, prepared = _prepared_case(tmp_path)
    try:
        item = prepared.plan.items[0]
        incomplete = item.candidate.model_copy(
            update={
                "fresh_identity": None,
                "material": None,
                "path_safety": None,
                "recent_activity": None,
                "recoverability": None,
            }
        )
        invalid_plan = prepared.plan.model_copy(
            update={"items": (item.model_copy(update={"candidate": incomplete}),)}
        )
        assert len(environment.confirmations._identity_digest(invalid_plan)) == 64
        assert len(environment.confirmations._material_digest(invalid_plan)) == 64
        assert len(environment.confirmations._eligibility_digest(invalid_plan)) == 64
        assert len(environment.confirmations._recovery_digest(invalid_plan)) == 64
    finally:
        environment.close()
