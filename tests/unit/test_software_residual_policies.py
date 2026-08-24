"""Classification, ownership, protection, and exact-scope policy tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from pc_manager_agent.domain.software_residuals import (
    ContextPathEvidence,
    OwnershipConfidence,
    ResidualClassification,
    ResidualSource,
    UserDataProtectionLevel,
)
from pc_manager_agent.safety.residual_classification import ResidualClassifier
from pc_manager_agent.safety.residual_ownership import ResidualOwnershipEvaluator
from pc_manager_agent.safety.residual_scope_policy import (
    ResidualScanScopePolicy,
    ResidualScopeError,
)
from pc_manager_agent.safety.user_data_protection import UserDataProtectionPolicy
from tests.fixtures.software_residuals import residual_context


@pytest.mark.parametrize(
    ("name", "expected"),
    (
        ("package.bin", ResidualClassification.PACKAGE_USER_DATA),
        ("Example.lnk", ResidualClassification.SHORTCUT),
        ("settings.json", ResidualClassification.CONFIGURATION),
        ("history.sqlite", ResidualClassification.DATABASE),
        ("data", ResidualClassification.DATABASE),
        ("application.log", ResidualClassification.LOG),
        ("crash.dmp", ResidualClassification.CRASH_DUMP),
        ("entry.tmp", ResidualClassification.TEMPORARY_DATA),
        ("cache/item.bin", ResidualClassification.CACHE),
        ("plugins/item.bin", ResidualClassification.PLUGIN_OR_EXTENSION),
        ("licenses/key.bin", ResidualClassification.LICENSE_DATA),
        ("ordinary.bin", ResidualClassification.PROGRAM_RESIDUAL),
    ),
)
def test_classifier_uses_finite_metadata_rules(name: str, expected: ResidualClassification) -> None:
    source = (
        ResidualSource.MSIX_PACKAGE_DATA
        if name == "package.bin"
        else ResidualSource.INSTALL_LOCATION
    )
    classification, reasons = ResidualClassifier().classify(
        Path("C:/Exact") / name,
        source,
        ResidualClassification.PROGRAM_RESIDUAL,
    )
    assert classification is expected
    assert reasons


def test_msix_data_and_database_are_strongly_protected(tmp_path: Path) -> None:
    policy = UserDataProtectionPolicy(tmp_path / "profile")
    for classification in (
        ResidualClassification.PACKAGE_USER_DATA,
        ResidualClassification.DATABASE,
        ResidualClassification.USER_DATA,
    ):
        level, reasons = policy.protect(tmp_path / "data", classification)
        assert level is UserDataProtectionLevel.STRONGLY_PROTECTED
        assert reasons


def test_name_only_evidence_never_becomes_high_confidence() -> None:
    confidence, evidence = ResidualOwnershipEvaluator().weak_name_only()
    assert confidence is OwnershipConfidence.LOW
    assert evidence[0].code == "name-similarity-only"
    exact = ContextPathEvidence(
        path=Path("C:/Exact/App"),
        source=ResidualSource.INSTALL_LOCATION,
        evidence_code="exact-path",
        expected_classification=ResidualClassification.PROGRAM_RESIDUAL,
    )
    confidence, exact_evidence = ResidualOwnershipEvaluator().evaluate(exact)
    assert confidence is OwnershipConfidence.HIGH
    assert len(exact_evidence) == 1
    shared_confidence, _shared_evidence = ResidualOwnershipEvaluator().evaluate(
        exact, shared_location=True
    )
    assert shared_confidence is OwnershipConfidence.MEDIUM


def test_shortcut_target_is_strong_evidence_but_not_cleanup_authority(
    tmp_path: Path,
) -> None:
    root = ContextPathEvidence(
        path=tmp_path / "Example.lnk",
        source=ResidualSource.SHORTCUT,
        evidence_code="known-shortcut-path",
        expected_classification=ResidualClassification.SHORTCUT,
        related_target_path=tmp_path / "Example.exe",
    )
    confidence, evidence = ResidualOwnershipEvaluator().evaluate(root)
    assert confidence is OwnershipConfidence.HIGH
    assert {item.code for item in evidence} == {
        "known-shortcut-path",
        "shortcut-target-exact",
    }
    shared_confidence, shared_evidence = ResidualOwnershipEvaluator().evaluate(
        root, shared_location=True
    )
    assert shared_confidence is OwnershipConfidence.MEDIUM
    assert shared_evidence[-1].code == "shared-location-warning"


def test_known_app_data_without_more_specific_metadata_is_user_data() -> None:
    classification, reasons = ResidualClassifier().classify(
        Path("C:/Users/Test/AppData/Local/Example/state.bin"),
        ResidualSource.KNOWN_APP_DATA,
        ResidualClassification.UNKNOWN,
    )
    assert classification is ResidualClassification.USER_DATA
    assert reasons == ("known-app-data-source",)


@pytest.mark.parametrize(
    ("relative", "classification", "protection"),
    (
        ("Documents/Example Projects", ResidualClassification.USER_DATA, "strongly_protected"),
        ("Saved Games/Example", ResidualClassification.USER_DATA, "strongly_protected"),
        ("Projects/app/.venv", ResidualClassification.PROGRAM_RESIDUAL, "strongly_protected"),
        ("AppData/Roaming/Example", ResidualClassification.CONFIGURATION, "strongly_protected"),
        (
            "AppData/Local/Packages/Family/LocalState",
            ResidualClassification.PACKAGE_USER_DATA,
            "strongly_protected",
        ),
        ("AppData/Local/Example/unknown", ResidualClassification.UNKNOWN, "protected"),
    ),
)
def test_user_and_application_data_remain_protected(
    tmp_path: Path,
    relative: str,
    classification: ResidualClassification,
    protection: str,
) -> None:
    profile = tmp_path / "profile"
    level, reasons = UserDataProtectionPolicy(profile).protect(
        profile / Path(relative), classification
    )
    assert level.value == protection
    assert reasons


@pytest.mark.parametrize("component", ("data", "database", "pgdata"))
def test_database_like_directories_are_classified_conservatively(component: str) -> None:
    classification, _reasons = ResidualClassifier().classify(
        Path("C:/Program Files/PostgreSQL") / component,
        ResidualSource.INSTALL_LOCATION,
        ResidualClassification.PROGRAM_RESIDUAL,
    )
    assert classification is ResidualClassification.DATABASE


def test_scope_requires_eligible_exact_context_and_blocks_protected_root(tmp_path: Path) -> None:
    policy = ResidualScanScopePolicy()
    context = residual_context(tmp_path / "app")
    roots = policy.validated_roots(context)
    assert roots[0].path == tmp_path / "app"
    ineligible = context.model_copy(
        update={"verified_removed": False, "verification_state": "failed"}
    )
    with pytest.raises(ResidualScopeError, match="eligible"):
        policy.validated_roots(ineligible)


def test_scope_rejects_parent_traversal_and_credential_directory(tmp_path: Path) -> None:
    policy = ResidualScanScopePolicy(extra_forbidden=(tmp_path / "secret",))
    traversal = residual_context(Path(str(tmp_path / "app")) / ".." / "escape")
    with pytest.raises(ResidualScopeError, match="Parent traversal"):
        policy.validated_roots(traversal)
    protected = residual_context(tmp_path / "secret")
    with pytest.raises(ResidualScopeError, match="protected"):
        policy.validated_roots(protected)


def test_scope_configuration_and_context_limits_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="root limit"):
        ResidualScanScopePolicy(max_roots=0)
    empty = residual_context(tmp_path / "empty").model_copy(update={"known_paths": ()})
    with pytest.raises(ResidualScopeError, match="no exact"):
        ResidualScanScopePolicy().validated_roots(empty)
    evidence = tuple(
        ContextPathEvidence(
            path=tmp_path / f"app-{index}",
            source=ResidualSource.INSTALL_LOCATION,
            evidence_code=f"path-{index}",
            expected_classification=ResidualClassification.PROGRAM_RESIDUAL,
        )
        for index in range(2)
    )
    too_many = residual_context(tmp_path / "unused").model_copy(update={"known_paths": evidence})
    with pytest.raises(ResidualScopeError, match="root limit"):
        ResidualScanScopePolicy(max_roots=1).validated_roots(too_many)


def test_scope_rejects_drive_and_user_library_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = tmp_path / "profile"
    monkeypatch.setenv("USERPROFILE", str(profile))
    policy = ResidualScanScopePolicy()
    for broad in (Path("C:/"), profile, profile / "Documents"):
        with pytest.raises(ResidualScopeError, match="too broad"):
            policy.validated_roots(residual_context(broad))


def test_scope_deduplicates_same_path_from_distinct_sources_and_hashes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "exact"
    first = ContextPathEvidence(
        path=path,
        source=ResidualSource.INSTALL_LOCATION,
        evidence_code="install-exact",
        expected_classification=ResidualClassification.PROGRAM_RESIDUAL,
    )
    second = ContextPathEvidence(
        path=path,
        source=ResidualSource.CONFIGURATION,
        evidence_code="config-exact",
        expected_classification=ResidualClassification.CONFIGURATION,
    )
    context = residual_context(path).model_copy(update={"known_paths": (first, second)})
    policy = ResidualScanScopePolicy()
    assert len(policy.validated_roots(context)) == 1
    assert len(policy.scope_digest(context)) == 64


def test_scope_rejects_network_and_ambiguous_syntax(tmp_path: Path) -> None:
    network_policy = ResidualScanScopePolicy(network_path_detector=lambda _path: True)
    with pytest.raises(ResidualScopeError, match="Network-backed"):
        network_policy.validated_roots(residual_context(tmp_path / "network"))
    relative = residual_context(tmp_path / "unused").model_copy(
        update={
            "known_paths": (
                ContextPathEvidence(
                    path=Path("relative/app"),
                    source=ResidualSource.INSTALL_LOCATION,
                    evidence_code="relative-path",
                    expected_classification=ResidualClassification.PROGRAM_RESIDUAL,
                ),
            )
        }
    )
    with pytest.raises(ResidualScopeError, match="absolute"):
        ResidualScanScopePolicy().validated_roots(relative)
    ambiguous = residual_context(Path("C:/Example. "))
    with pytest.raises(ResidualScopeError, match="trailing"):
        ResidualScanScopePolicy().validated_roots(ambiguous)
    unc = residual_context(Path("//server/share/Example"))
    with pytest.raises(ResidualScopeError, match="UNC"):
        ResidualScanScopePolicy().validated_roots(unc)


def test_existing_root_revalidation_blocks_network_reparse_and_bad_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "exact"
    root.mkdir()
    with pytest.raises(ResidualScopeError, match="network-backed"):
        ResidualScanScopePolicy(network_path_detector=lambda _path: True).validate_existing_root(
            root
        )
    import pc_manager_agent.safety.residual_scope_policy as scope_module

    monkeypatch.setattr(scope_module, "is_reparse_point", lambda path: path == root)
    with pytest.raises(ResidualScopeError, match="reparse"):
        ResidualScanScopePolicy().validate_existing_root(root)
    monkeypatch.setattr(scope_module, "is_reparse_point", lambda _path: False)
    monkeypatch.setattr(
        scope_module.os, "lstat", lambda _path: SimpleNamespace(st_ino=-1, st_dev=1)
    )
    with pytest.raises(ResidualScopeError, match="identity"):
        ResidualScanScopePolicy().validate_existing_root(root)


def test_existing_root_revalidation_success_protected_and_unavailable(
    tmp_path: Path,
) -> None:
    root = tmp_path / "exact"
    root.mkdir()
    assert ResidualScanScopePolicy().validate_existing_root(root) == root
    with pytest.raises(ResidualScopeError, match="protected"):
        ResidualScanScopePolicy(extra_forbidden=(root,)).validate_existing_root(root)
    with pytest.raises(ResidualScopeError, match="unavailable"):
        ResidualScanScopePolicy().validate_existing_root(tmp_path / "missing" / "deep")
    with pytest.raises(ResidualScopeError, match="unavailable"):
        ResidualScanScopePolicy().validate_existing_root(Path("Z:/missing-stage4d3-root"))


def test_entry_rejection_codes_cover_every_scope_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    candidate = root / "item"
    import pc_manager_agent.safety.residual_scope_policy as scope_module

    policy = ResidualScanScopePolicy(extra_forbidden=(candidate,))
    assert policy.entry_rejection_reason(candidate, root) == "protected-path"
    network = ResidualScanScopePolicy(network_path_detector=lambda path: path == candidate)
    assert network.entry_rejection_reason(candidate, root) == "network-path"
    monkeypatch.setattr(scope_module, "is_reparse_point", lambda path: path == candidate)
    assert ResidualScanScopePolicy().entry_rejection_reason(candidate, root) == "reparse-point"
    monkeypatch.setattr(scope_module, "path_is_within", lambda _path, _root: False)
    assert ResidualScanScopePolicy().entry_rejection_reason(candidate, root) == "scope-escape"
    assert (
        ResidualScanScopePolicy().entry_rejection_reason(Path("relative/item"), root)
        == "unsafe-path-syntax"
    )
    monkeypatch.setattr(scope_module, "path_is_within", lambda _path, _root: True)
    monkeypatch.setattr(scope_module, "is_reparse_point", lambda _path: False)
    assert ResidualScanScopePolicy().entry_rejection_reason(candidate, root) is None


@pytest.mark.parametrize(
    ("path_suffix", "classification", "expected"),
    (
        ("Documents/work.bin", ResidualClassification.PROGRAM_RESIDUAL, "strongly_protected"),
        ("AppData/Roaming/Example", ResidualClassification.CONFIGURATION, "strongly_protected"),
        (
            "AppData/Local/Docker/data.bin",
            ResidualClassification.PROGRAM_RESIDUAL,
            "strongly_protected",
        ),
        ("AppData/Local/Example/config", ResidualClassification.CONFIGURATION, "protected"),
        ("AppData/Local/Example/cache", ResidualClassification.CACHE, "caution"),
        ("AppData/Local/Example/program.bin", ResidualClassification.PROGRAM_RESIDUAL, "caution"),
    ),
)
def test_protection_policy_covers_path_and_category_precedence(
    tmp_path: Path,
    path_suffix: str,
    classification: ResidualClassification,
    expected: str,
) -> None:
    profile = tmp_path / "profile"
    level, reasons = UserDataProtectionPolicy(profile).protect(
        profile / Path(path_suffix), classification
    )
    assert level.value == expected
    assert reasons
