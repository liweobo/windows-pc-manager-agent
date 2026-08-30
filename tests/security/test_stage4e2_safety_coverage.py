"""Branch coverage for the Stage 4E2 deterministic safety boundaries."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import BaseModel
from tests.fixtures.system_cleanup import (
    SyntheticRecycleBinEmptyPlatform,
    SyntheticSystemCleanupEnvironment,
    build_system_cleanup_environment,
    mark_old,
    save_temp_report,
)

import pc_manager_agent.domain.system_cleanup_execution as cleanup_execution_module
import pc_manager_agent.safety.system_cleanup_preview as cleanup_preview_module
from pc_manager_agent.confirmation.system_cleanup import (
    SystemCleanupConfirmationError,
    SystemCleanupConfirmationService,
    SystemCleanupConfirmationState,
)
from pc_manager_agent.domain.file_operations import FileObjectKind
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.system_cleanup_execution import (
    CleanupAction,
    CleanupAdapterType,
    CleanupEligibilityDecision,
    SystemCleanupRequest,
)
from pc_manager_agent.domain.system_optimization import (
    CleanupCategory,
    CleanupEvidenceOrigin,
    CleanupSourceReference,
    OptimizationConfidence,
    ProtectionLevel,
)
from pc_manager_agent.safety.recycle_bin_empty import (
    RecycleBinEmptyPlanBuilder,
    RecycleBinEmptyPreviewError,
)
from pc_manager_agent.safety.system_cleanup_policy import (
    CleanupRecentActivityPolicy,
    SystemCleanupEligibilityPolicy,
    SystemCleanupPathPolicy,
    SystemCleanupPolicyError,
    SystemCleanupRiskPolicy,
)
from pc_manager_agent.safety.system_cleanup_preview import (
    CleanupExecutionPlanBuilder,
    SystemCleanupPreviewError,
)
from pc_manager_agent.safety.system_cleanup_revalidation import (
    FreshCleanupCandidateRevalidator,
    SystemCleanupRevalidationError,
)
from pc_manager_agent.safety.system_cleanup_validator import SystemCleanupSafetyValidator
from pc_manager_agent.tools.manifest import CancellationToken, ToolManifest
from pc_manager_agent.tools.registry import ToolRegistry


def _prepared_old_file(
    tmp_path: Path,
) -> tuple[SyntheticSystemCleanupEnvironment, Path, object, object]:
    environment = build_system_cleanup_environment(tmp_path / "state.db")
    target = environment.temp_root / "old-item.tmp"
    target.write_bytes(b"old-data")
    mark_old(target)
    report = save_temp_report(environment)
    assessment = environment.service.assess(
        SystemCleanupRequest(
            source_report_id=report.report_id,
            selected_candidate_ids=(report.cleanup_candidates[0].candidate_id,),
        )
    )
    selected = next(item for item in assessment.items if item.path == target)
    prepared = environment.service.prepare(assessment, (selected.item_ref,))
    return environment, target, assessment, prepared


def test_path_policy_covers_exact_root_and_denial_branches(tmp_path: Path) -> None:
    profile = tmp_path / "user"
    root = profile / "AppData" / "Local" / "Temp"
    child = root / "old.tmp"
    child.parent.mkdir(parents=True)
    child.write_text("x", encoding="utf-8")
    policy = SystemCleanupPathPolicy({"current-user-temp": root}, user_profile=profile)

    assert policy.validate_report_root("current-user-temp", root, CleanupCategory.USER_TEMP) == root
    copied = policy.known_roots
    copied.clear()
    assert policy.known_roots == {"current-user-temp": root}
    normalized, decision = policy.validate_item(child, root)
    assert normalized == child
    assert decision.safe is True
    assert policy.validate_entry(child, root) == child

    with pytest.raises(SystemCleanupPolicyError, match="exact V1 known root"):
        policy.validate_report_root("wrong-source", root, CleanupCategory.USER_TEMP)
    with pytest.raises(SystemCleanupPolicyError, match="exact V1 known root"):
        policy.validate_report_root("current-user-temp", root, CleanupCategory.APPLICATION_CACHE)

    outside = tmp_path / "outside" / "Temp"
    outside.mkdir(parents=True)
    outside_child = outside / "child.tmp"
    outside_child.write_text("x", encoding="utf-8")
    outside_policy = SystemCleanupPathPolicy({"current-user-temp": outside}, user_profile=profile)
    with pytest.raises(SystemCleanupPolicyError, match="outside the current-user profile"):
        outside_policy.validate_report_root("current-user-temp", outside, CleanupCategory.USER_TEMP)

    _, root_decision = policy.validate_item(root, root)
    assert root_decision.safe is False
    assert "known-root-itself-cannot-be-selected" in root_decision.reason_codes

    public_root = profile / "Public" / "Temp"
    public_child = public_root / "shared.tmp"
    public_child.parent.mkdir(parents=True)
    public_child.write_text("x", encoding="utf-8")
    public_policy = SystemCleanupPathPolicy(
        {"current-user-temp": public_root}, user_profile=profile
    )
    _, public_decision = public_policy.validate_item(public_child, public_root)
    assert public_decision.shared_location is True
    assert public_decision.safe is False

    _, other_user_decision = outside_policy.validate_item(outside_child, outside)
    assert other_user_decision.other_user is True
    assert other_user_decision.current_user_scope is False

    network_policy = SystemCleanupPathPolicy(
        {"current-user-temp": root},
        user_profile=profile,
        network_path_detector=lambda path: path == child,
    )
    with pytest.raises(SystemCleanupPolicyError):
        network_policy.validate_item(child, root)
    with pytest.raises(SystemCleanupPolicyError):
        network_policy.validate_entry(child, root)

    forbidden_root = profile / ".ssh"
    forbidden_root.mkdir()
    forbidden_policy = SystemCleanupPathPolicy(
        {"current-user-temp": forbidden_root}, user_profile=profile
    )
    with pytest.raises(SystemCleanupPolicyError):
        forbidden_policy.validate_report_root(
            "current-user-temp", forbidden_root, CleanupCategory.USER_TEMP
        )


def test_activity_eligibility_and_cutoff_cover_every_gate(tmp_path: Path) -> None:
    environment, _, assessment, _ = _prepared_old_file(tmp_path)
    try:
        candidate = assessment.items[0]
        assert candidate.material is not None
        assert candidate.path_safety is not None
        assert candidate.activity is not None
        material = candidate.material

        with pytest.raises(ValueError, match="minimum age"):
            CleanupRecentActivityPolicy(minimum_age_days=0)
        activity_policy = CleanupRecentActivityPolicy(minimum_age_days=7)
        now = datetime.now(UTC)
        assert activity_policy.cutoff(now) == now - timedelta(days=7)
        clear = activity_policy.evaluate(
            material,
            delete_access_available=True,
            active_installer_detected=False,
            now=now,
        )
        assert clear.blocked is False
        blocked = activity_policy.evaluate(
            material.model_copy(update={"recently_modified_count": 1}),
            delete_access_available=False,
            active_installer_detected=True,
            now=now,
        )
        assert blocked.blocked is True
        assert set(blocked.reason_codes) == {
            "recent-metadata-change",
            "locked-or-delete-access-unavailable",
            "installer-transaction-active",
        }

        eligibility = SystemCleanupEligibilityPolicy()
        deferred = eligibility.evaluate(
            category=CleanupCategory.SYSTEM_TEMP,
            source="system-temp",
            confidence=OptimizationConfidence.HIGH,
            protection=ProtectionLevel.NONE,
            material=material,
            path_safety=candidate.path_safety,
            activity=clear,
            recycle_bin_available=True,
        )
        assert deferred[:2] == (
            CleanupEligibilityDecision.DEFERRED,
            CleanupAdapterType.DEFERRED,
        )
        personal_file = eligibility.evaluate(
            category=CleanupCategory.LARGE_FILE,
            source="stage1",
            confidence=OptimizationConfidence.HIGH,
            protection=ProtectionLevel.NONE,
            material=material,
            path_safety=candidate.path_safety,
            activity=clear,
            recycle_bin_available=True,
        )
        assert personal_file[1] is CleanupAdapterType.STAGE2B_HANDOFF
        unknown = eligibility.evaluate(
            category=CleanupCategory.UNKNOWN,
            source="unknown",
            confidence=OptimizationConfidence.UNKNOWN,
            protection=ProtectionLevel.UNKNOWN,
            material=material,
            path_safety=candidate.path_safety,
            activity=blocked,
            recycle_bin_available=False,
        )
        assert unknown[1] is CleanupAdapterType.DEFERRED

        unsafe_path = candidate.path_safety.model_copy(
            update={"safe": False, "reason_codes": ("scope-denied",)}
        )
        all_blocked = eligibility.evaluate(
            category=CleanupCategory.USER_TEMP,
            source="wrong-source",
            confidence=OptimizationConfidence.MEDIUM,
            protection=ProtectionLevel.CAUTION,
            material=material.model_copy(update={"reparse_count": 1, "sensitive_signal_count": 1}),
            path_safety=unsafe_path,
            activity=blocked,
            recycle_bin_available=False,
        )
        assert all_blocked[0] is CleanupEligibilityDecision.BLOCKED
        assert {
            "source-category-not-directly-allow-listed",
            "fresh-analysis-confidence-not-high",
            "protection-blocked-CAUTION",
            "scope-denied",
            "tree-contains-reparse-point",
            "tree-contains-protected-data-signal",
            "recycle-bin-unavailable",
        }.issubset(all_blocked[2])

        eligible = eligibility.evaluate(
            category=CleanupCategory.USER_TEMP,
            source="current-user-temp",
            confidence=OptimizationConfidence.HIGH,
            protection=ProtectionLevel.NONE,
            material=material,
            path_safety=candidate.path_safety,
            activity=clear,
            recycle_bin_available=True,
        )
        assert eligible[0] is CleanupEligibilityDecision.ELIGIBLE
    finally:
        environment.close()


@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        ((1, 1, 1, 1), RiskLevel.R2),
        ((2, 1, 1, 1), RiskLevel.R2_HIGH_IMPACT),
        ((1, 2, 1, 1), RiskLevel.R2_HIGH_IMPACT),
        ((1, 1, 2, 1), RiskLevel.R2_HIGH_IMPACT),
        ((1, 1, 1, 2), RiskLevel.R2_HIGH_IMPACT),
    ],
)
def test_cleanup_risk_threshold_branches(
    counts: tuple[int, int, int, int], expected: RiskLevel
) -> None:
    policy = SystemCleanupRiskPolicy(
        max_normal_items=1,
        max_normal_objects=1,
        max_normal_total_bytes=1,
        max_normal_single_item_bytes=1,
    )
    assert (
        policy.classify(
            item_count=counts[0],
            object_count=counts[1],
            total_bytes=counts[2],
            largest_item_bytes=counts[3],
        )
        is expected
    )


def test_cleanup_risk_and_preview_constructors_reject_nonpositive_limits(
    tmp_path: Path,
) -> None:
    environment = build_system_cleanup_environment(tmp_path / "state.db")
    try:
        with pytest.raises(ValueError, match="risk thresholds"):
            SystemCleanupRiskPolicy(
                max_normal_items=0,
                max_normal_objects=1,
                max_normal_total_bytes=1,
                max_normal_single_item_bytes=1,
            )
        with pytest.raises(ValueError, match="Preview limits"):
            CleanupExecutionPlanBuilder(
                environment.revalidator,
                SystemCleanupRiskPolicy(
                    max_normal_items=1,
                    max_normal_objects=1,
                    max_normal_total_bytes=1,
                    max_normal_single_item_bytes=1,
                ),
                max_selected_items=0,
                plan_ttl_seconds=1,
                preview_ttl_seconds=1,
            )
        with pytest.raises(ValueError, match="revalidation limits"):
            FreshCleanupCandidateRevalidator(
                environment.report_store,
                SystemCleanupPathPolicy(
                    {"current-user-temp": environment.temp_root},
                    user_profile=environment.profile,
                ),
                SystemCleanupEligibilityPolicy(),
                CleanupRecentActivityPolicy(),
                environment.revalidator._identity,
                environment.recycle,
                environment.activity,
                max_selected_candidates=0,
            )
    finally:
        environment.close()


def test_preview_selection_staleness_material_and_overlap_denials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment, target, assessment, prepared = _prepared_old_file(tmp_path)
    try:
        selected = assessment.items[0]
        with pytest.raises(SystemCleanupPreviewError, match="between 1"):
            environment.plans.compile(assessment, ())
        with pytest.raises(SystemCleanupPreviewError, match="duplicates"):
            environment.plans.compile(assessment, (selected.item_ref, selected.item_ref))
        with pytest.raises(SystemCleanupPreviewError, match="stale or unknown"):
            environment.plans.compile(assessment, (uuid4(),))

        blocked = selected.model_copy(update={"eligibility": CleanupEligibilityDecision.BLOCKED})
        blocked_assessment = assessment.model_copy(
            update={
                "items": (blocked,),
                "eligible_count": 0,
                "blocked_count": 1,
            }
        )
        with pytest.raises(SystemCleanupPreviewError, match="blocked or deferred"):
            environment.plans.compile(blocked_assessment, (blocked.item_ref,))

        no_material = selected.model_copy(update={"material": None})
        incomplete = assessment.model_copy(update={"items": (no_material,)})
        monkeypatch.setattr(environment.plans, "_reject_overlapping", lambda _items: None)
        monkeypatch.setattr(
            cleanup_preview_module,
            "PlannedCleanupItem",
            lambda sequence, candidate: SimpleNamespace(sequence=sequence, candidate=candidate),
        )
        with pytest.raises(SystemCleanupPreviewError, match="material evidence"):
            environment.plans.compile(incomplete, (no_material.item_ref,))
        monkeypatch.undo()

        stale_preview = prepared.preview.model_copy(update={"plan_digest": "0" * 64})
        with pytest.raises(SystemCleanupPreviewError, match="stale or mismatched"):
            environment.plans.require_current(prepared.plan, stale_preview)
        expired_plan = prepared.plan.model_copy(
            update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
        )
        with pytest.raises(SystemCleanupPreviewError, match="plan expired"):
            environment.plans.revalidate(expired_plan)

        monkeypatch.setattr(
            environment.revalidator,
            "require_unchanged",
            lambda expected, _cancellation=None: expected.model_copy(
                update={"reason_codes": ("changed-after-revalidation",)}
            ),
        )
        with pytest.raises(SystemCleanupPreviewError, match="evidence changed"):
            environment.plans.revalidate(prepared.plan)
        monkeypatch.undo()

        directory = environment.temp_root / "old-directory"
        child = directory / "child.tmp"
        child.parent.mkdir()
        child.write_bytes(b"x")
        mark_old(child)
        mark_old(directory)
        report = save_temp_report(environment)
        directory_assessment = environment.service.assess(
            SystemCleanupRequest(
                source_report_id=report.report_id,
                selected_candidate_ids=(report.cleanup_candidates[0].candidate_id,),
            )
        )
        parent = next(item for item in directory_assessment.items if item.path == directory)
        assert parent.fresh_identity is not None
        child_state = parent.fresh_identity.state.model_copy(
            update={"path": child, "kind": FileObjectKind.FILE}
        )
        child_identity = parent.fresh_identity.model_copy(
            update={"resolved_path": child, "state": child_state}
        )
        child_candidate = parent.model_copy(
            update={"item_ref": uuid4(), "path": child, "fresh_identity": child_identity}
        )
        with pytest.raises(SystemCleanupPreviewError, match="parent and its child"):
            environment.plans._reject_overlapping((parent, child_candidate))
        with pytest.raises(SystemCleanupPreviewError, match="parent and its child"):
            environment.plans._reject_overlapping((child_candidate, parent))

        monkeypatch.setattr(
            cleanup_execution_module.CleanupExecutionCandidate,
            "model_validate",
            staticmethod(lambda item: item),
        )
        missing_first = parent.model_copy(update={"path": None, "fresh_identity": None})
        with pytest.raises(SystemCleanupPreviewError, match="lacks identity"):
            environment.plans._reject_overlapping((missing_first,))
        missing_other = child_candidate.model_copy(update={"path": None, "fresh_identity": None})
        with pytest.raises(SystemCleanupPreviewError, match="lacks identity"):
            environment.plans._reject_overlapping((parent, missing_other))
        monkeypatch.undo()

        moved = target.with_suffix(".moved")
        target.replace(moved)
        with pytest.raises(SystemCleanupPreviewError):
            environment.plans.revalidate(prepared.plan)
    finally:
        environment.close()


def test_revalidation_routes_non_direct_provenance_and_empty_known_root(
    tmp_path: Path,
) -> None:
    environment = build_system_cleanup_environment(tmp_path / "state.db")
    base_report = save_temp_report(environment)
    base = base_report.cleanup_candidates[0]
    stage1 = base.model_copy(
        update={
            "candidate_id": uuid4(),
            "category": CleanupCategory.LARGE_FILE,
            "source": "stage1-large-files",
            "source_reference": CleanupSourceReference(
                origin=CleanupEvidenceOrigin.STAGE1_REPORT,
                upstream_report_id=uuid4(),
                upstream_record_id=1,
            ),
        }
    )
    stage4d3 = base.model_copy(
        update={
            "candidate_id": uuid4(),
            "source_reference": CleanupSourceReference(
                origin=CleanupEvidenceOrigin.STAGE4D3_REPORT,
                upstream_report_id=uuid4(),
                upstream_candidate_id=uuid4(),
            ),
        }
    )
    recycle = base.model_copy(
        update={
            "candidate_id": uuid4(),
            "category": CleanupCategory.RECYCLE_BIN_CONTENT,
            "source": "recycle-bin",
            "source_reference": None,
        }
    )
    unknown = base.model_copy(
        update={
            "candidate_id": uuid4(),
            "category": CleanupCategory.UNKNOWN,
            "source": "unknown-source",
            "source_reference": None,
        }
    )
    missing_provenance = base.model_copy(update={"candidate_id": uuid4(), "source_reference": None})
    invalid_direct_root = base.model_copy(
        update={"candidate_id": uuid4(), "source": "invalid-direct-source"}
    )
    empty_known = base.model_copy(update={"candidate_id": uuid4()})
    report = base_report.model_copy(
        update={
            "report_id": uuid4(),
            "cleanup_candidates": (
                stage1,
                stage4d3,
                recycle,
                unknown,
                missing_provenance,
                invalid_direct_root,
                empty_known,
            ),
        }
    )
    environment.report_store.save(report)
    try:
        assessment = environment.revalidator.assess(
            SystemCleanupRequest(
                source_report_id=report.report_id,
                selected_candidate_ids=tuple(
                    item.candidate_id for item in report.cleanup_candidates
                ),
            )
        )
        adapters = {item.cleanup_adapter_type for item in assessment.items}
        assert CleanupAdapterType.STAGE2B_HANDOFF in adapters
        assert CleanupAdapterType.STAGE4D4_HANDOFF in adapters
        assert CleanupAdapterType.RECYCLE_BIN_EMPTY in adapters
        assert any(
            "missing-known-location-provenance" in item.reason_codes for item in assessment.items
        )
        assert any(
            "known-location-currently-empty" in item.reason_codes for item in assessment.items
        )
        assert any(
            "fresh-discovery-failed-systemcleanuppolicyerror" in item.reason_codes
            for item in assessment.items
        )
    finally:
        environment.close()


def test_revalidation_limits_cancellation_and_protected_signals(tmp_path: Path) -> None:
    environment = build_system_cleanup_environment(tmp_path / "state.db", max_selected=1)
    report = save_temp_report(environment)
    try:
        with pytest.raises(SystemCleanupRevalidationError, match="at most 1"):
            environment.revalidator.assess(
                SystemCleanupRequest(
                    source_report_id=uuid4(),
                    selected_candidate_ids=(uuid4(), uuid4()),
                )
            )
        with pytest.raises(SystemCleanupRevalidationError, match="unknown or cross-report"):
            environment.revalidator.assess(
                SystemCleanupRequest(
                    source_report_id=report.report_id,
                    selected_candidate_ids=(uuid4(),),
                )
            )
        token = CancellationToken()
        token.cancel()
        with pytest.raises(SystemCleanupRevalidationError, match="cancelled"):
            environment.revalidator.assess(
                SystemCleanupRequest(
                    source_report_id=report.report_id,
                    selected_candidate_ids=(report.cleanup_candidates[0].candidate_id,),
                ),
                token,
            )

        pathless = environment.revalidator._non_direct(
            report.report_id,
            report.cleanup_candidates[0],
            CleanupEligibilityDecision.BLOCKED,
            CleanupAdapterType.DEFERRED,
            "synthetic-pathless",
            path=None,
        ).model_copy(update={"path": None})
        with pytest.raises(SystemCleanupRevalidationError, match="path is unavailable"):
            environment.revalidator.require_unchanged(pathless)

        unknown_source = pathless.model_copy(
            update={
                "path": environment.temp_root / "missing.tmp",
                "source_candidate_id": uuid4(),
            }
        )
        with pytest.raises(SystemCleanupRevalidationError, match="source candidate"):
            environment.revalidator.require_unchanged(unknown_source)

        with pytest.raises(SystemCleanupRevalidationError, match="item limit"):
            environment.revalidator._assess_report_candidate(
                report.report_id,
                report.cleanup_candidates[0],
                CancellationToken(),
                item_budget=0,
            )
        with pytest.raises(SystemCleanupRevalidationError, match="provenance is absent"):
            environment.revalidator._handoff(
                report.report_id,
                report.cleanup_candidates[0].model_copy(update={"source_reference": None}),
                CleanupAdapterType.STAGE2B_HANDOFF,
            )

        assert (
            environment.revalidator._protected_signal(
                Path("C:/user/credentials/item"),
                CleanupCategory.USER_TEMP,
                FileObjectKind.FILE,
            )
            == "protected-component"
        )
        assert (
            environment.revalidator._protected_signal(
                Path("C:/user/cache/data.db"),
                CleanupCategory.USER_TEMP,
                FileObjectKind.FILE,
            )
            == "database-suffix"
        )
        assert (
            environment.revalidator._protected_signal(
                Path("C:/user/cache/settings.ini"),
                CleanupCategory.USER_TEMP,
                FileObjectKind.FILE,
            )
            == "configuration-suffix"
        )
        assert (
            environment.revalidator._protected_signal(
                Path("C:/user/crash/not-a-dump.txt"),
                CleanupCategory.CRASH_DUMP,
                FileObjectKind.FILE,
            )
            == "non-dump-object-in-crash-dump-root"
        )
        assert (
            environment.revalidator._protected_signal(
                Path("C:/user/cache/ordinary.tmp"),
                CleanupCategory.USER_TEMP,
                FileObjectKind.FILE,
            )
            is None
        )
    finally:
        environment.close()


def test_revalidation_blocks_sensitive_locked_and_oversized_objects(tmp_path: Path) -> None:
    environment = build_system_cleanup_environment(tmp_path / "state.db")
    sensitive = environment.temp_root / "sensitive.db"
    sensitive.write_bytes(b"database-like-name")
    mark_old(sensitive)
    report = save_temp_report(environment)
    environment.recycle.available = False
    environment.activity.available = False
    environment.revalidator._active_installer = lambda: True
    try:
        assessment = environment.revalidator.assess(
            SystemCleanupRequest(
                source_report_id=report.report_id,
                selected_candidate_ids=(report.cleanup_candidates[0].candidate_id,),
            )
        )
        item = assessment.items[0]
        assert item.eligibility is CleanupEligibilityDecision.BLOCKED
        assert item.protection_level is ProtectionLevel.STRONGLY_PROTECTED
        assert item.recoverability_evidence is None
        assert "recycle-bin-unavailable" in item.reason_codes

        environment.recycle.available = True
        environment.activity.available = True
        environment.revalidator._active_installer = lambda: False
        environment.revalidator._max_bytes = 1
        limited = environment.revalidator.assess(
            SystemCleanupRequest(
                source_report_id=report.report_id,
                selected_candidate_ids=(report.cleanup_candidates[0].candidate_id,),
            )
        )
        assert limited.items[0].eligibility is CleanupEligibilityDecision.BLOCKED
        assert (
            "fresh-object-validation-failed-systemcleanuppolicyerror"
            in limited.items[0].reason_codes
        )
    finally:
        environment.close()


def test_revalidation_discovery_object_limit_and_mid_snapshot_cancel(tmp_path: Path) -> None:
    environment = build_system_cleanup_environment(tmp_path / "state.db")
    first = environment.temp_root / "first.tmp"
    second = environment.temp_root / "second.tmp"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    mark_old(first)
    mark_old(second)
    report = save_temp_report(environment)

    class _CancelDuringSnapshot:
        calls = 0

        def cancellation_requested(self) -> bool:
            self.calls += 1
            return self.calls >= 2

    try:
        environment.revalidator._max_discovered = 1
        with pytest.raises(SystemCleanupRevalidationError, match="item limit"):
            environment.revalidator.assess(
                SystemCleanupRequest(
                    source_report_id=report.report_id,
                    selected_candidate_ids=(report.cleanup_candidates[0].candidate_id,),
                )
            )

        environment.revalidator._max_discovered = 100
        environment.revalidator._max_objects = 1
        directory = environment.temp_root / "tree"
        nested = directory / "nested.tmp"
        nested.parent.mkdir()
        nested.write_bytes(b"nested")
        mark_old(nested)
        mark_old(directory)
        with pytest.raises(SystemCleanupRevalidationError, match="hard object or byte limit"):
            environment.revalidator.assess(
                SystemCleanupRequest(
                    source_report_id=report.report_id,
                    selected_candidate_ids=(report.cleanup_candidates[0].candidate_id,),
                )
            )
        with pytest.raises(SystemCleanupPolicyError, match="object-limit"):
            environment.revalidator._snapshot(
                directory, CleanupCategory.USER_TEMP, CancellationToken()
            )

        environment.revalidator._max_objects = 1_000
        with pytest.raises(SystemCleanupRevalidationError, match="snapshot cancelled"):
            environment.revalidator._snapshot(
                directory,
                CleanupCategory.USER_TEMP,
                _CancelDuringSnapshot(),  # type: ignore[arg-type]
            )
    finally:
        environment.close()


def test_revalidation_defends_against_empty_and_overproduced_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = build_system_cleanup_environment(tmp_path / "state.db")
    target = environment.temp_root / "old.tmp"
    target.write_bytes(b"old")
    mark_old(target)
    report = save_temp_report(environment)
    request = SystemCleanupRequest(
        source_report_id=report.report_id,
        selected_candidate_ids=(report.cleanup_candidates[0].candidate_id,),
    )
    try:
        item = environment.revalidator.assess(request).items[0]
        environment.revalidator._max_discovered = 1
        monkeypatch.setattr(
            environment.revalidator,
            "_assess_report_candidate",
            lambda *_args, **_kwargs: (
                item,
                item.model_copy(update={"item_ref": uuid4()}),
            ),
        )
        with pytest.raises(SystemCleanupRevalidationError, match="item limit"):
            environment.revalidator.assess(request)

        monkeypatch.setattr(
            environment.revalidator,
            "_assess_report_candidate",
            lambda *_args, **_kwargs: (),
        )
        with pytest.raises(SystemCleanupRevalidationError, match="No fresh cleanup object"):
            environment.revalidator.assess(request)
    finally:
        environment.close()


def test_revalidation_detects_root_identity_change_during_both_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = build_system_cleanup_environment(tmp_path / "state.db")
    target = environment.temp_root / "old.tmp"
    target.write_bytes(b"old")
    mark_old(target)
    report = save_temp_report(environment)
    candidate = report.cleanup_candidates[0]
    original_inspect = environment.revalidator._identity.inspect

    def changing_after(threshold: int):
        calls = {"count": 0}

        def inspect(path: Path):
            state = original_inspect(path)
            if path == target:
                calls["count"] += 1
                if calls["count"] >= threshold:
                    return state.model_copy(update={"modified_ns": state.modified_ns + 1})
            return state

        return inspect

    try:
        monkeypatch.setattr(environment.revalidator._identity, "inspect", changing_after(3))
        with pytest.raises(SystemCleanupPolicyError, match="root-changed-during-material"):
            environment.revalidator._snapshot(
                target, CleanupCategory.USER_TEMP, CancellationToken()
            )

        monkeypatch.undo()
        monkeypatch.setattr(environment.revalidator._identity, "inspect", changing_after(5))
        assert candidate.path is not None
        root = environment.revalidator._paths.validate_report_root(
            candidate.source, candidate.path, candidate.category
        )
        changed = environment.revalidator._assess_object(
            report.report_id,
            candidate,
            root,
            target,
            CancellationToken(),
        )
        assert changed.eligibility is CleanupEligibilityDecision.BLOCKED
        assert "fresh-object-validation-failed-systemcleanuppolicyerror" in changed.reason_codes
    finally:
        environment.close()


def test_recycle_bin_empty_builder_success_and_all_denials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SYSTEMROOT", "C:/Windows")
    with pytest.raises(ValueError, match="TTLs"):
        RecycleBinEmptyPlanBuilder(
            SyntheticRecycleBinEmptyPlatform(),
            plan_ttl_seconds=0,
            preview_ttl_seconds=1,
        )

    incomplete = SyntheticRecycleBinEmptyPlatform()
    incomplete.enumeration_complete = False
    with pytest.raises(RecycleBinEmptyPreviewError, match="incomplete"):
        RecycleBinEmptyPlanBuilder(
            incomplete, plan_ttl_seconds=30, preview_ttl_seconds=10
        ).prepare()

    empty = SyntheticRecycleBinEmptyPlatform(item_count=0, observed_size_bytes=0)
    with pytest.raises(RecycleBinEmptyPreviewError, match="already empty"):
        RecycleBinEmptyPlanBuilder(empty, plan_ttl_seconds=30, preview_ttl_seconds=10).prepare()

    platform = SyntheticRecycleBinEmptyPlatform()
    builder = RecycleBinEmptyPlanBuilder(platform, plan_ttl_seconds=30, preview_ttl_seconds=10)
    plan, preview = builder.prepare()
    assert preview.plan_id == plan.plan_id
    assert builder.revalidate(plan).snapshot.canonical_digest() == plan.snapshot_digest
    expired = plan.model_copy(update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)})
    with pytest.raises(RecycleBinEmptyPreviewError, match="expired"):
        builder.revalidate(expired)
    platform.item_count += 1
    with pytest.raises(RecycleBinEmptyPreviewError, match="contents changed"):
        builder.revalidate(plan)

    monkeypatch.setenv("SYSTEMROOT", "relative")
    with pytest.raises(OSError, match="system volume"):
        RecycleBinEmptyPlanBuilder._system_volume()


def test_item_confirmation_rejection_expiry_single_use_and_kind_binding(
    tmp_path: Path,
) -> None:
    environment, _, _, prepared = _prepared_old_file(tmp_path)
    try:
        rejected = environment.confirmations.resolve(
            prepared.plan_confirmation.confirmation_id,
            False,
            prepared.plan,
            prepared.preview,
        )
        assert rejected.state is SystemCleanupConfirmationState.REJECTED
        with pytest.raises(SystemCleanupConfirmationError, match="approved cleanup plan"):
            environment.confirmations.request_runtime(
                rejected.confirmation_id, prepared.plan, prepared.preview
            )
        with pytest.raises(SystemCleanupConfirmationError, match="already resolved"):
            environment.confirmations.resolve(
                rejected.confirmation_id,
                True,
                prepared.plan,
                prepared.preview,
            )

        plan_gate = environment.confirmations.request_plan(prepared.plan, prepared.preview)
        approved = environment.confirmations.resolve(
            plan_gate.confirmation_id, True, prepared.plan, prepared.preview
        )
        runtime = environment.confirmations.request_runtime(
            approved.confirmation_id, prepared.plan, prepared.preview
        )
        with pytest.raises(SystemCleanupConfirmationError, match="absent or already used"):
            environment.confirmations.consume_runtime(
                runtime.confirmation_id, prepared.plan, prepared.preview
            )
        runtime_rejected = environment.confirmations.resolve(
            runtime.confirmation_id, False, prepared.plan, prepared.preview
        )
        assert runtime_rejected.state is SystemCleanupConfirmationState.REJECTED

        replacement_plan, replacement_preview = environment.plans.compile(
            prepared.assessment, (prepared.assessment.items[0].item_ref,)
        )
        replacement_gate = environment.confirmations.request_plan(prepared.plan, prepared.preview)
        with pytest.raises(SystemCleanupConfirmationError, match="evidence changed"):
            environment.confirmations.resolve(
                replacement_gate.confirmation_id,
                True,
                replacement_plan,
                replacement_preview,
            )

        stale_parent_gate = environment.confirmations.request_plan(prepared.plan, prepared.preview)
        stale_parent = environment.confirmations.resolve(
            stale_parent_gate.confirmation_id,
            True,
            prepared.plan,
            prepared.preview,
        )
        stale_runtime = environment.confirmations.request_runtime(
            stale_parent.confirmation_id, prepared.plan, prepared.preview
        )
        environment.confirmations.resolve(
            stale_runtime.confirmation_id,
            True,
            prepared.plan,
            prepared.preview,
        )
        environment.repository.update_confirmation(
            stale_parent.model_copy(update={"state": SystemCleanupConfirmationState.REJECTED})
        )
        with pytest.raises(SystemCleanupConfirmationError, match="plan approval is stale"):
            environment.confirmations.consume_runtime(
                stale_runtime.confirmation_id, prepared.plan, prepared.preview
            )

        runtime2 = environment.confirmations.request_runtime(
            approved.confirmation_id, prepared.plan, prepared.preview
        )
        environment.confirmations.resolve(
            runtime2.confirmation_id, True, prepared.plan, prepared.preview
        )
        consumed = environment.confirmations.consume_runtime(
            runtime2.confirmation_id, prepared.plan, prepared.preview
        )
        assert consumed.state is SystemCleanupConfirmationState.CONSUMED
        with pytest.raises(SystemCleanupConfirmationError, match="absent or already used"):
            environment.confirmations.consume_runtime(
                runtime2.confirmation_id, prepared.plan, prepared.preview
            )

        empty_plan, empty_preview = environment.empty_plans.prepare()
        with pytest.raises(SystemCleanupConfirmationError, match="kinds do not match"):
            environment.confirmations.request_plan(prepared.plan, empty_preview)
        with pytest.raises(SystemCleanupConfirmationError, match="kinds do not match"):
            environment.confirmations.request_plan(empty_plan, prepared.preview)
        with pytest.raises(SystemCleanupConfirmationError, match="kinds do not match"):
            environment.confirmations._binding(prepared.plan, empty_preview)

        with pytest.raises(ValueError, match="TTLs"):
            SystemCleanupConfirmationService(
                environment.repository,
                environment.plans,
                plan_ttl_seconds=0,
                runtime_ttl_seconds=1,
            )

        clock = [datetime.now(UTC)]
        expiring = SystemCleanupConfirmationService(
            environment.repository,
            environment.plans,
            plan_ttl_seconds=1,
            runtime_ttl_seconds=1,
            now=lambda: clock[0],
        )
        expiring_gate = expiring.request_plan(prepared.plan, prepared.preview)
        clock[0] += timedelta(seconds=2)
        with pytest.raises(SystemCleanupConfirmationError, match="expired"):
            expiring.resolve(
                expiring_gate.confirmation_id,
                True,
                prepared.plan,
                prepared.preview,
            )
    finally:
        environment.close()


def test_empty_confirmation_success_and_stale_preview_denial(tmp_path: Path) -> None:
    environment = build_system_cleanup_environment(tmp_path / "state.db")
    try:
        prepared = environment.service.prepare_recycle_bin_empty()
        approved = environment.confirmations.resolve(
            prepared.plan_confirmation.confirmation_id,
            True,
            prepared.plan,
            prepared.preview,
        )
        fresh_preview = environment.empty_plans.revalidate(prepared.plan)
        runtime = environment.confirmations.request_runtime(
            approved.confirmation_id, prepared.plan, fresh_preview
        )
        environment.confirmations.resolve(
            runtime.confirmation_id, True, prepared.plan, fresh_preview
        )
        consumed = environment.confirmations.consume_runtime(
            runtime.confirmation_id, prepared.plan, fresh_preview
        )
        assert consumed.state is SystemCleanupConfirmationState.CONSUMED

        stale = prepared.preview.model_copy(update={"plan_id": uuid4()})
        with pytest.raises(SystemCleanupConfirmationError, match="stale"):
            environment.confirmations.request_plan(prepared.plan, stale)
    finally:
        environment.close()


class _ManifestOnlyTool:
    def __init__(self, manifest: ToolManifest) -> None:
        self._manifest = manifest

    @property
    def manifest(self) -> ToolManifest:
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        _ = cancellation
        return request


def test_independent_validator_covers_every_issue_category(tmp_path: Path) -> None:
    environment, _, _, prepared = _prepared_old_file(tmp_path)
    try:
        assert environment.validator.review(prepared.plan).approved is True
        original = prepared.plan.items[0]
        incomplete = original.candidate.model_copy(
            update={
                "eligibility": CleanupEligibilityDecision.BLOCKED,
                "cleanup_adapter_type": CleanupAdapterType.DEFERRED,
                "path": None,
                "fresh_identity": None,
                "material": None,
                "path_safety": None,
                "activity": None,
                "recoverability_evidence": None,
            }
        )
        invalid_item = original.model_copy(
            update={
                "action": CleanupAction.EMPTY_RECYCLE_BIN,
                "candidate": incomplete,
                "tool_name": "unknown.cleanup.tool",
            }
        )
        invalid_plan = prepared.plan.model_copy(
            update={
                "risk_level": RiskLevel.R0,
                "rollback_level": RollbackLevel.FULL,
                "items": (invalid_item,),
            }
        )
        review = environment.validator.review(invalid_plan)
        assert review.approved is False
        assert {issue.code for issue in review.issues} == {
            "invalid-risk",
            "invalid-recovery",
            "invalid-action",
            "candidate-not-eligible",
            "fresh-evidence-incomplete",
            "unknown-tool",
        }

        unsafe_manifest = replace(
            environment.registry.manifest("optimization.cleanup.trash"), read_only=True
        )
        registry = ToolRegistry()
        registry.register(_ManifestOnlyTool(unsafe_manifest))
        unsafe_review = SystemCleanupSafetyValidator(registry).review(prepared.plan)
        assert unsafe_review.approved is False
        assert unsafe_review.issues[0].code == "unsafe-tool-manifest"
    finally:
        environment.close()
