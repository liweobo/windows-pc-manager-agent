"""Branch coverage for bounded Stage 4D3 metadata collectors."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from pc_manager_agent.domain.software_residuals import (
    ContextPathEvidence,
    OwnershipConfidence,
    ResidualAnalysisStatus,
    ResidualClassification,
    ResidualSource,
)
from pc_manager_agent.orchestration.residual_collectors import (
    InstallLocationResidualCollector,
    ResidualCollectionBudget,
)
from pc_manager_agent.safety.residual_classification import ResidualClassifier
from pc_manager_agent.safety.residual_ownership import ResidualOwnershipEvaluator
from pc_manager_agent.safety.residual_scope_policy import ResidualScanScopePolicy
from pc_manager_agent.safety.user_data_protection import UserDataProtectionPolicy
from pc_manager_agent.tools.manifest import CancellationToken


def _collector(tmp_path: Path) -> InstallLocationResidualCollector:
    return InstallLocationResidualCollector(
        ResidualScanScopePolicy(),
        ResidualClassifier(),
        ResidualOwnershipEvaluator(),
        UserDataProtectionPolicy(tmp_path / "profile"),
    )


def _evidence(path: Path, *, max_depth: int = 6, shared: bool = False) -> ContextPathEvidence:
    return ContextPathEvidence(
        path=path,
        source=ResidualSource.INSTALL_LOCATION,
        evidence_code="install-location-exact",
        expected_classification=ResidualClassification.PROGRAM_RESIDUAL,
        max_depth=max_depth,
        shared_location=shared,
    )


def test_collection_budget_validates_timeout_limit_and_cancellation() -> None:
    with pytest.raises(ValueError, match="object budget"):
        ResidualCollectionBudget(0, 1, CancellationToken())
    with pytest.raises(ValueError, match="timeout"):
        ResidualCollectionBudget(1, 0, CancellationToken())
    now = [0.0]
    timed = ResidualCollectionBudget(1, 1, CancellationToken(), clock=lambda: now[0])
    assert timed.consume()
    assert not timed.consume()
    assert timed.stop_status is ResidualAnalysisStatus.TRUNCATED
    timed_out = ResidualCollectionBudget(2, 1, CancellationToken(), clock=lambda: now[0])
    now[0] = 2.0
    assert not timed_out.can_continue()
    assert timed_out.stop_status is ResidualAnalysisStatus.TIMED_OUT
    token = CancellationToken()
    cancelled = ResidualCollectionBudget(2, 10, token)
    token.cancel()
    assert not cancelled.can_continue()
    assert cancelled.stop_status is ResidualAnalysisStatus.CANCELLED


def test_exact_file_root_builds_stable_metadata_identity(tmp_path: Path) -> None:
    root = tmp_path / "application.log"
    root.write_bytes(b"content-is-not-read")
    result = _collector(tmp_path).collect(
        _evidence(root),
        uuid4(),
        ResidualCollectionBudget(10, 10, CancellationToken()),
        uninstall_verified=True,
    )
    assert result.root_scanned is True
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.classification is ResidualClassification.LOG
    assert candidate.identity.size_bytes == len(b"content-is-not-read")
    assert candidate.identity.file_id >= 0


def test_depth_shared_and_unverified_flags_are_conservative(tmp_path: Path) -> None:
    root = tmp_path / "Publisher"
    nested = root / "child"
    nested.mkdir(parents=True)
    (nested / "hidden.bin").write_bytes(b"not traversed")
    result = _collector(tmp_path).collect(
        _evidence(root, max_depth=0, shared=True),
        uuid4(),
        ResidualCollectionBudget(10, 10, CancellationToken()),
        uninstall_verified=False,
    )
    assert any(issue.code == "depth-limit" for issue in result.issues)
    assert not any(candidate.path.name == "hidden.bin" for candidate in result.candidates)
    root_candidate = next(candidate for candidate in result.candidates if candidate.path == root)
    assert root_candidate.ownership_confidence is OwnershipConfidence.MEDIUM
    assert "possibly-shared-location" in root_candidate.risk_flags
    assert "uninstall-not-fully-verified" in root_candidate.risk_flags
    assert "recently-modified" in root_candidate.risk_flags


@pytest.mark.parametrize(
    ("exception", "code"),
    ((PermissionError(), "access-denied"), (OSError(), "filesystem-error")),
)
def test_root_metadata_errors_fail_soft(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exception: OSError,
    code: str,
) -> None:
    import pc_manager_agent.orchestration.residual_collectors.base as collector_module

    monkeypatch.setattr(collector_module.os, "lstat", MagicMock(side_effect=exception))
    result = _collector(tmp_path).collect(
        _evidence(tmp_path / "root"),
        uuid4(),
        ResidualCollectionBudget(10, 10, CancellationToken()),
        uninstall_verified=True,
    )
    assert result.issues[0].code == code


def test_changed_directory_identity_is_skipped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    original = os.lstat(root)
    scope = MagicMock()
    scope.validate_existing_root.return_value = root
    collector = InstallLocationResidualCollector(
        scope,
        ResidualClassifier(),
        ResidualOwnershipEvaluator(),
        UserDataProtectionPolicy(tmp_path / "profile"),
    )
    changed = os.stat_result(
        (
            original.st_mode,
            original.st_ino + 1,
            original.st_dev,
            original.st_nlink,
            original.st_uid,
            original.st_gid,
            original.st_size,
            original.st_atime,
            original.st_mtime,
            original.st_ctime,
        )
    )
    import pc_manager_agent.orchestration.residual_collectors.base as collector_module

    monkeypatch.setattr(collector_module.os, "stat", lambda *_args, **_kwargs: changed)
    result = collector.collect(
        _evidence(root),
        uuid4(),
        ResidualCollectionBudget(10, 10, CancellationToken()),
        uninstall_verified=True,
    )
    assert result.candidates == ()
    assert result.issues[0].code == "path-identity-changed"
