"""Plan/Preview/reviewer fail-closed branches for Stage 4D4."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from pc_manager_agent.domain.residual_cleanup import (
    CleanupEligibilityDecision,
    ResidualCleanupRequest,
)
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.safety.residual_cleanup_policy import CleanupRiskPolicy
from pc_manager_agent.safety.residual_cleanup_preview import (
    ResidualCleanupPreviewEngine,
    ResidualCleanupPreviewError,
)
from pc_manager_agent.safety.residual_cleanup_validator import ResidualCleanupSafetyValidator
from tests.fixtures.residual_cleanup import (
    build_residual_cleanup_environment,
    create_residual_report,
)
from tests.fixtures.software_residuals import residual_context


def _assessment_case(tmp_path: Path, *, select_root_and_child: bool = False):
    root = tmp_path / "SyntheticProduct"
    root.mkdir()
    child = root / "app.bin"
    child.write_bytes(b"program")
    environment = build_residual_cleanup_environment(tmp_path / "state.db")
    context = residual_context(root)
    report = create_residual_report(environment, context)
    by_path = {item.path: item for item in report.candidates}
    selected = (
        (by_path[root].candidate_id, by_path[child].candidate_id)
        if select_root_and_child
        else (by_path[root].candidate_id,)
    )
    assessment = environment.service.assess(
        ResidualCleanupRequest(
            source_report_id=report.report_id,
            selected_residual_ids=selected,
        )
    )
    return environment, assessment


def test_preview_constructor_rejects_invalid_ttls(tmp_path: Path) -> None:
    environment, _assessment = _assessment_case(tmp_path)
    try:
        with pytest.raises(ValueError, match="positive"):
            ResidualCleanupPreviewEngine(
                environment.revalidator,
                CleanupRiskPolicy(
                    max_normal_item_count=1,
                    max_normal_object_count=1,
                    max_normal_total_size=1,
                    max_normal_single_item_size=1,
                ),
                plan_ttl_seconds=0,
                preview_ttl_seconds=1,
            )
    finally:
        environment.close()


def test_preview_rejects_overlapping_parent_child_and_missing_material(
    tmp_path: Path,
) -> None:
    environment, assessment = _assessment_case(tmp_path, select_root_and_child=True)
    try:
        assert assessment.all_eligible
        with pytest.raises(ResidualCleanupPreviewError, match="parent"):
            environment.preview.compile(assessment)
        missing = assessment.items[0].model_copy(update={"material": None})
        invalid = assessment.model_copy(update={"items": (missing,)})
        with pytest.raises(ResidualCleanupPreviewError, match="material"):
            environment.preview.compile(invalid)
    finally:
        environment.close()


def test_preview_rejects_expired_plan_and_every_binding_dimension(tmp_path: Path) -> None:
    environment, assessment = _assessment_case(tmp_path)
    try:
        plan, preview = environment.preview.compile(assessment)
        expired_plan = plan.model_copy(
            update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
        )
        with pytest.raises(ResidualCleanupPreviewError, match="expired"):
            environment.preview.revalidate(expired_plan, assessment.request)

        changed_previews = (
            preview.model_copy(update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)}),
            preview.model_copy(update={"transaction_id": uuid4()}),
            preview.model_copy(update={"plan_id": uuid4()}),
            preview.model_copy(update={"plan_digest": "0" * 64}),
            preview.model_copy(update={"item_set_digest": "0" * 64}),
            preview.model_copy(update={"risk_level": RiskLevel.R2_HIGH_IMPACT}),
        )
        for changed in changed_previews:
            with pytest.raises(ResidualCleanupPreviewError, match="stale"):
                environment.preview.require_current(plan, changed)
    finally:
        environment.close()


def test_independent_validator_reports_each_corrupted_plan_dimension(tmp_path: Path) -> None:
    environment, assessment = _assessment_case(tmp_path)
    try:
        plan, _preview = environment.preview.compile(assessment)
        item = plan.items[0]
        invalid_candidate = item.candidate.model_copy(
            update={
                "eligibility": CleanupEligibilityDecision.BLOCKED,
                "fresh_identity": None,
                "material": None,
                "path_safety": None,
                "recent_activity": None,
                "recoverability": None,
            }
        )
        corrupted_item = item.model_copy(
            update={
                "action": SimpleNamespace(value="PERMANENT_DELETE"),
                "candidate": invalid_candidate,
                "tool_name": "software.residuals.made_up_force_delete",
            }
        )
        corrupted = plan.model_copy(
            update={
                "risk_level": RiskLevel.R0,
                "rollback_level": RollbackLevel.FULL,
                "items": (corrupted_item,),
            }
        )
        review = ResidualCleanupSafetyValidator(environment.registry).review(corrupted)
        assert not review.approved
        assert {issue.code for issue in review.issues} == {
            "invalid-risk",
            "invalid-recovery",
            "invalid-action",
            "candidate-not-eligible",
            "fresh-evidence-incomplete",
            "unknown-tool",
        }

        unsafe_manifest_item = item.model_copy(
            update={"tool_name": "software.residuals.prepare_cleanup"}
        )
        unsafe_manifest_plan = plan.model_copy(update={"items": (unsafe_manifest_item,)})
        review = ResidualCleanupSafetyValidator(environment.registry).review(unsafe_manifest_plan)
        assert {issue.code for issue in review.issues} == {"unsafe-tool-manifest"}
    finally:
        environment.close()
