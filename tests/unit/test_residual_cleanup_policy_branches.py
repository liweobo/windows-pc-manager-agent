"""Decision-table branch coverage for Stage 4D4 safety policy."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from pc_manager_agent.domain.residual_cleanup import (
    CleanupEligibilityDecision,
    ResidualCleanupRequest,
)
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.software_residuals import (
    OwnershipConfidence,
    ResidualClassification,
    ResidualSource,
    UserDataProtectionLevel,
)
from pc_manager_agent.safety.path_policy import PathPolicy
from pc_manager_agent.safety.residual_cleanup_policy import (
    CleanupEligibilityPolicy,
    CleanupRiskPolicy,
    ResidualCleanupPathPolicy,
    ResidualCleanupPolicyError,
    ResidualRecentModificationPolicy,
)
from pc_manager_agent.safety.residual_scope_policy import ResidualScanScopePolicy
from tests.fixtures.residual_cleanup import (
    build_residual_cleanup_environment,
    create_residual_report,
)
from tests.fixtures.software_residuals import residual_context


def _eligible_case(tmp_path: Path):
    root = tmp_path / "SyntheticProduct"
    root.mkdir()
    (root / "app.bin").write_bytes(b"program")
    environment = build_residual_cleanup_environment(tmp_path / "state.db")
    context = residual_context(root)
    report = create_residual_report(environment, context)
    old = next(item for item in report.candidates if item.path == root)
    assessment = environment.service.assess(
        ResidualCleanupRequest(
            source_report_id=report.report_id,
            selected_residual_ids=(old.candidate_id,),
        )
    )
    fresh = assessment.items[0]
    assert fresh.material is not None
    assert fresh.path_safety is not None
    assert fresh.recent_activity is not None
    return environment, context, context.known_paths[0], old, fresh


@pytest.mark.parametrize(
    ("classification", "expected_fragment"),
    (
        (ResidualClassification.DATABASE, "always-blocked"),
        (ResidualClassification.CONFIGURATION, "always-blocked"),
        (ResidualClassification.USER_DATA, "always-blocked"),
        (ResidualClassification.PLUGIN_OR_EXTENSION, "always-blocked"),
        (ResidualClassification.PACKAGE_USER_DATA, "always-blocked"),
        (ResidualClassification.APPLICATION_STATE, "always-blocked"),
        (ResidualClassification.LICENSE_DATA, "always-blocked"),
        (ResidualClassification.UNKNOWN, "always-blocked"),
        (ResidualClassification.TEMPORARY_DATA, "deferred-v1"),
        (ResidualClassification.SERVICE_RELATED_ARTIFACT, "deferred-v1"),
        (ResidualClassification.CRASH_DUMP, "deferred-v1"),
    ),
)
def test_classification_matrix_blocks_every_non_allowlisted_class(
    tmp_path: Path,
    classification: ResidualClassification,
    expected_fragment: str,
) -> None:
    environment, context, evidence, _old, fresh = _eligible_case(tmp_path)
    try:
        material = fresh.material
        path_safety = fresh.path_safety
        recent = fresh.recent_activity
        assert material is not None and path_safety is not None and recent is not None
        decision, reasons = CleanupEligibilityPolicy().evaluate(
            context=context,
            evidence=evidence,
            classification=classification,
            ownership=OwnershipConfidence.HIGH,
            protection=UserDataProtectionLevel.CAUTION,
            material=material,
            path_safety=path_safety,
            recent_activity=recent,
            recycle_bin_available=True,
        )
        assert decision is CleanupEligibilityDecision.BLOCKED
        assert any(expected_fragment in reason for reason in reasons)
    finally:
        environment.close()


def test_independent_eligibility_gates_and_manual_review(tmp_path: Path) -> None:
    environment, context, evidence, _old, fresh = _eligible_case(tmp_path)
    try:
        material = fresh.material
        path_safety = fresh.path_safety
        recent = fresh.recent_activity
        assert material is not None and path_safety is not None and recent is not None
        policy = CleanupEligibilityPolicy()

        decision, reasons = policy.evaluate(
            context=context,
            evidence=evidence,
            classification=ResidualClassification.PROGRAM_RESIDUAL,
            ownership=OwnershipConfidence.MEDIUM,
            protection=UserDataProtectionLevel.CAUTION,
            material=material,
            path_safety=path_safety,
            recent_activity=recent,
            recycle_bin_available=True,
        )
        assert decision is CleanupEligibilityDecision.MANUAL_REVIEW
        assert reasons == ("ownership-confidence-not-high",)

        blocked_context = context.model_copy(update={"verified_removed": False})
        blocked_path = path_safety.model_copy(
            update={"safe": False, "reason_codes": ("synthetic-unsafe-path",)}
        )
        blocked_recent = recent.model_copy(
            update={"blocked": True, "reason_codes": ("modified-after-uninstall",)}
        )
        forbidden_material = material.model_copy(update={"forbidden_descendant_count": 1})
        decision, reasons = policy.evaluate(
            context=blocked_context,
            evidence=evidence,
            classification=ResidualClassification.PROGRAM_RESIDUAL,
            ownership=OwnershipConfidence.LOW,
            protection=UserDataProtectionLevel.PROTECTED,
            material=forbidden_material,
            path_safety=blocked_path,
            recent_activity=blocked_recent,
            recycle_bin_available=False,
        )
        assert decision is CleanupEligibilityDecision.BLOCKED
        for expected in (
            "uninstall-not-verified-removed",
            "ownership-confidence-not-high",
            "protection-blocked-protected",
            "synthetic-unsafe-path",
            "modified-after-uninstall",
            "recycle-bin-unavailable",
            "tree-contains-forbidden-descendant",
        ):
            assert expected in reasons
    finally:
        environment.close()


def test_shortcut_cache_and_log_require_exact_source_evidence(tmp_path: Path) -> None:
    environment, context, evidence, _old, fresh = _eligible_case(tmp_path)
    try:
        material = fresh.material
        path_safety = fresh.path_safety
        recent = fresh.recent_activity
        assert material is not None and path_safety is not None and recent is not None
        policy = CleanupEligibilityPolicy()
        for classification, expected in (
            (ResidualClassification.SHORTCUT, "shortcut-source-not-exact"),
            (ResidualClassification.CACHE, "cache-path-is-not-app-specific"),
            (ResidualClassification.LOG, "log-path-is-not-app-specific"),
        ):
            unsuitable = evidence.model_copy(update={"source": ResidualSource.CONFIGURATION})
            decision, reasons = policy.evaluate(
                context=context,
                evidence=unsuitable,
                classification=classification,
                ownership=OwnershipConfidence.HIGH,
                protection=UserDataProtectionLevel.CAUTION,
                material=material,
                path_safety=path_safety,
                recent_activity=recent,
                recycle_bin_available=True,
            )
            assert decision is CleanupEligibilityDecision.BLOCKED
            assert expected in reasons
        shortcut_evidence = evidence.model_copy(update={"source": ResidualSource.SHORTCUT})
        decision, reasons = policy.evaluate(
            context=context,
            evidence=shortcut_evidence,
            classification=ResidualClassification.SHORTCUT,
            ownership=OwnershipConfidence.HIGH,
            protection=UserDataProtectionLevel.CAUTION,
            material=material,
            path_safety=path_safety,
            recent_activity=recent,
            recycle_bin_available=True,
        )
        assert decision is CleanupEligibilityDecision.BLOCKED
        assert "shortcut-target-evidence-missing" in reasons
    finally:
        environment.close()


def test_path_policy_rejects_mismatch_escape_missing_and_access_failure(
    tmp_path: Path,
) -> None:
    environment, _context, evidence, old, _fresh = _eligible_case(tmp_path)
    try:
        scope = ResidualScanScopePolicy(max_roots=16)
        denied = ResidualCleanupPathPolicy(
            scope,
            access_checker=lambda _path, _mode: False,
            user_profile=tmp_path / "profile",
        )
        _path, decision = denied.validate_candidate(old, evidence)
        assert not decision.safe
        assert "ordinary-user-delete-access-not-proven" in decision.reason_codes

        with pytest.raises(ResidualCleanupPolicyError, match="invalid-report"):
            denied.validate_candidate(old.model_copy(update={"report_id": UUID(int=0)}), evidence)
        with pytest.raises(ResidualCleanupPolicyError, match="context-evidence"):
            denied.validate_candidate(
                old.model_copy(update={"scan_root": tmp_path / "different"}),
                evidence,
            )
        outside = tmp_path / "outside.bin"
        outside.write_bytes(b"outside")
        with pytest.raises(ResidualCleanupPolicyError, match="outside"):
            denied.validate_candidate(old.model_copy(update={"path": outside}), evidence)
        missing = old.model_copy(update={"path": old.path / "missing"})
        with pytest.raises(ResidualCleanupPolicyError):
            denied.validate_candidate(missing, evidence)
    finally:
        environment.close()


def test_path_shared_and_recent_policy_branches(tmp_path: Path) -> None:
    environment, context, evidence, old, fresh = _eligible_case(tmp_path)
    try:
        policy = ResidualCleanupPathPolicy(
            ResidualScanScopePolicy(),
            access_checker=lambda _path, _mode: True,
            user_profile=tmp_path / "profile",
        )
        assert policy.is_shared(old.path, evidence.model_copy(update={"shared_location": True}))
        shared = tmp_path / "Shared" / "Product"
        shared.mkdir(parents=True)
        assert policy.is_shared(shared, evidence)
        policy._program_data = tmp_path / "ProgramData"
        program_data_vendor = policy._program_data / "Vendor"
        program_data_vendor.mkdir(parents=True)
        assert policy.is_shared(program_data_vendor, evidence)
        product_cache = policy._program_data / "Vendor" / "Product" / "Cache"
        product_cache.mkdir(parents=True)
        assert not policy.is_shared(product_cache, evidence)
        policy._program_files = (tmp_path / "Program Files",)
        common = policy._program_files[0] / "Common Files" / "Thing"
        common.mkdir(parents=True)
        assert policy.is_shared(common, evidence)
        product = policy._program_files[0] / "Vendor" / "Product"
        product.mkdir(parents=True)
        assert not policy.is_shared(product, evidence)

        policy._windows = old.path.parent
        _path, windows_decision = policy.validate_candidate(old, evidence)
        assert "windows-system-path" in windows_decision.reason_codes

        material = fresh.material
        assert material is not None
        with pytest.raises(ResidualCleanupPolicyError, match="completion"):
            ResidualRecentModificationPolicy().evaluate(
                context.model_copy(update={"uninstall_completed_at": None}),
                material,
            )
        assert policy.entry_rejection_reason(tmp_path / "outside", old.path) == "scope-escape"
    finally:
        environment.close()


def test_path_policy_wraps_base_denial_and_checks_descendant_detectors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment, _context, evidence, old, _fresh = _eligible_case(tmp_path)

    class SyntheticBasePolicy:
        def __init__(self, *, forbidden: bool = False, deny: bool = False) -> None:
            self.forbidden = forbidden
            self.deny = deny

        def validate_operation_source(self, path: Path) -> Path:
            if self.deny:
                raise PermissionError("synthetic base denial")
            return path

        def is_forbidden(self, _path: Path) -> bool:
            return self.forbidden

    try:
        policy = ResidualCleanupPathPolicy(
            ResidualScanScopePolicy(),
            access_checker=lambda _path, _mode: True,
            user_profile=tmp_path / "profile",
        )
        monkeypatch.setattr(
            PathPolicy,
            "for_authorized_roots",
            lambda *_args, **_kwargs: SyntheticBasePolicy(deny=True),
        )
        with pytest.raises(ResidualCleanupPolicyError, match="synthetic base"):
            policy.validate_candidate(old, evidence)

        monkeypatch.setattr(
            PathPolicy,
            "for_authorized_roots",
            lambda *_args, **_kwargs: SyntheticBasePolicy(forbidden=True),
        )
        assert policy.entry_rejection_reason(old.path, old.path) == "protected-descendant"

        network_policy = ResidualCleanupPathPolicy(
            ResidualScanScopePolicy(),
            network_path_detector=lambda _path: True,
            access_checker=lambda _path, _mode: True,
            user_profile=tmp_path / "profile",
        )
        monkeypatch.setattr(
            PathPolicy,
            "for_authorized_roots",
            lambda *_args, **_kwargs: SyntheticBasePolicy(),
        )
        assert network_policy.entry_rejection_reason(old.path, old.path) == "network-descendant"
    finally:
        environment.close()


def test_invalid_risk_configuration_is_rejected() -> None:
    with pytest.raises(ValueError, match="positive"):
        CleanupRiskPolicy(
            max_normal_item_count=0,
            max_normal_object_count=1,
            max_normal_total_size=1,
            max_normal_single_item_size=1,
        )
    policy = CleanupRiskPolicy(
        max_normal_item_count=1,
        max_normal_object_count=1,
        max_normal_total_size=1,
        max_normal_single_item_size=1,
    )
    assert (
        policy.classify(item_count=1, object_count=1, total_size=1, largest_item=1) is RiskLevel.R2
    )
