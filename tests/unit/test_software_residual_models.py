"""Domain invariants for Stage 4D3 report-only residual analysis."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from pc_manager_agent.domain.software_residuals import (
    ContextPathEvidence,
    OwnershipConfidence,
    ResidualAnalysisStatus,
    ResidualCandidate,
    ResidualClassification,
    ResidualIdentity,
    ResidualObjectType,
    ResidualRecommendation,
    ResidualReport,
    ResidualReportSummary,
    ResidualSource,
    UninstallContext,
    UninstallMechanism,
    UserDataProtectionLevel,
)
from tests.fixtures.software_residuals import residual_context


@pytest.mark.parametrize("mechanism", tuple(UninstallMechanism))
def test_all_agent_mechanisms_can_represent_verified_contexts(
    tmp_path: Path, mechanism: UninstallMechanism
) -> None:
    context = residual_context(tmp_path / mechanism.value, mechanism=mechanism)
    assert context.eligible_for_analysis is True
    assert len(context.canonical_digest()) == 64


def test_completed_unverified_context_is_eligible_but_failed_is_not(tmp_path: Path) -> None:
    uncertain = residual_context(tmp_path / "uncertain", verified_removed=False)
    failed = uncertain.model_copy(update={"verification_state": "failed"})
    assert uncertain.eligible_for_analysis is True
    assert failed.eligible_for_analysis is False


def test_context_rejects_duplicate_path_source_evidence(tmp_path: Path) -> None:
    evidence = ContextPathEvidence(
        path=tmp_path,
        source=ResidualSource.INSTALL_LOCATION,
        evidence_code="exact-path",
        expected_classification=ResidualClassification.PROGRAM_RESIDUAL,
    )
    with pytest.raises(ValidationError, match="duplicate"):
        UninstallContext(
            transaction_id=uuid4(),
            mechanism=UninstallMechanism.MSI,
            software_identity_digest="a" * 64,
            display_name="App",
            scope="current_user",
            architecture="x64",
            known_paths=(evidence, evidence),
            uninstall_started_at=datetime.now(UTC),
        )


def test_report_makes_deletion_and_candidate_mismatch_unrepresentable(tmp_path: Path) -> None:
    report_id = uuid4()
    candidate = ResidualCandidate(
        report_id=uuid4(),
        identity=ResidualIdentity(
            normalized_path=tmp_path / "data.db",
            device_id=1,
            file_id=2,
            object_type=ResidualObjectType.FILE,
            size_bytes=0,
            modified_time_ns=0,
        ),
        path=tmp_path / "data.db",
        scan_root=tmp_path,
        source=ResidualSource.INSTALL_LOCATION,
        object_type=ResidualObjectType.FILE,
        classification=ResidualClassification.DATABASE,
        ownership_confidence=OwnershipConfidence.HIGH,
        protection_level=UserDataProtectionLevel.STRONGLY_PROTECTED,
        readable=True,
        reparse_or_symlink=False,
        recommendation=ResidualRecommendation.PROTECT,
    )
    common = {
        "report_id": report_id,
        "context_id": uuid4(),
        "uninstall_transaction_id": uuid4(),
        "plan_id": uuid4(),
        "software_identity_digest": "a" * 64,
        "context_digest": "b" * 64,
        "completed_at": datetime.now(UTC),
        "status": ResidualAnalysisStatus.COMPLETED,
        "candidates": (candidate,),
        "summary": ResidualReportSummary(
            candidates=1,
            files=1,
            directories=0,
            total_size_bytes=0,
            issues=0,
            roots_requested=1,
            roots_scanned=1,
            duration_ms=1,
        ),
    }
    with pytest.raises(ValidationError, match="another report"):
        ResidualReport.model_validate(common)
    valid_candidate = candidate.model_copy(update={"report_id": report_id})
    with pytest.raises(ValidationError, match="deletion"):
        ResidualReport.model_validate(
            {**common, "candidates": (valid_candidate,), "deletion_performed": True}
        )


def test_residual_identity_and_model_payload_are_metadata_only_and_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = tmp_path / "Alice"
    path = profile / "AppData" / "Local" / "Example" / "settings.db"
    monkeypatch.setenv("USERPROFILE", str(profile))
    report_id = uuid4()
    identity = ResidualIdentity(
        normalized_path=path,
        device_id=10,
        file_id=20,
        object_type=ResidualObjectType.FILE,
        size_bytes=42,
        modified_time_ns=100,
    )
    candidate = ResidualCandidate(
        report_id=report_id,
        identity=identity,
        path=path,
        scan_root=path.parent,
        source=ResidualSource.KNOWN_APP_DATA,
        object_type=ResidualObjectType.FILE,
        size_bytes=42,
        classification=ResidualClassification.DATABASE,
        ownership_confidence=OwnershipConfidence.HIGH,
        protection_level=UserDataProtectionLevel.STRONGLY_PROTECTED,
        readable=True,
        reparse_or_symlink=False,
        recommendation=ResidualRecommendation.PROTECT,
    )
    report = ResidualReport(
        report_id=report_id,
        context_id=uuid4(),
        uninstall_transaction_id=uuid4(),
        plan_id=uuid4(),
        software_identity_digest="a" * 64,
        context_digest="b" * 64,
        completed_at=datetime.now(UTC),
        status=ResidualAnalysisStatus.COMPLETED,
        candidates=(candidate,),
        summary=ResidualReportSummary(
            candidates=1,
            files=1,
            directories=0,
            total_size_bytes=42,
            protected_size_bytes=42,
            issues=0,
            roots_requested=1,
            roots_scanned=1,
            duration_ms=1,
        ),
    )
    payload = report.to_model_payload()
    serialized = payload.model_dump_json()
    assert len(identity.canonical_digest()) == 64
    assert "Alice" not in serialized
    assert "%USERPROFILE%" in serialized
    assert "contents" not in serialized
    assert payload.deletion_performed is False
