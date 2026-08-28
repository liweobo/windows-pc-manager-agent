from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pc_manager_agent.domain.file_analysis import StoredFileRecord
from pc_manager_agent.domain.reports import FileMetadata
from pc_manager_agent.domain.software_residuals import (
    OwnershipConfidence as ResidualOwnershipConfidence,
)
from pc_manager_agent.domain.software_residuals import (
    ResidualCandidate,
    ResidualClassification,
    ResidualIdentity,
    ResidualObjectType,
    ResidualRecommendation,
    ResidualSource,
    UserDataProtectionLevel,
)
from pc_manager_agent.domain.system_optimization import (
    CleanupCategory,
    CleanupSafetyClassification,
    ProtectionLevel,
)
from pc_manager_agent.orchestration.optimization_evidence import (
    RepositoryOptimizationEvidenceSource,
)
from pc_manager_agent.persistence.analysis_results import AnalysisResultStoreError
from pc_manager_agent.persistence.software_residuals import SoftwareResidualStoreError
from pc_manager_agent.tools.manifest import CancellationToken


def test_current_stage1_duplicate_evidence_is_reused_without_execution(tmp_path: Path) -> None:
    path = tmp_path / "copy.bin"
    path.write_bytes(b"same")
    metadata = path.lstat()
    captured = datetime.fromtimestamp(metadata.st_mtime, tz=UTC)
    record = StoredFileRecord(
        record_id=1,
        metadata=FileMetadata(
            path=path,
            name=path.name,
            extension=path.suffix,
            media_type=None,
            size_bytes=metadata.st_size,
            created_at=captured,
            modified_at=captured,
            accessed_at=captured,
            scan_root=tmp_path,
            file_id=metadata.st_ino,
            device_id=metadata.st_dev,
        ),
        duplicate_group_id="verified-group",
        matches_plan=True,
    )
    observation = RepositoryOptimizationEvidenceSource._stage1_observation(record, (tmp_path,))
    assert observation is not None
    assert observation.category is CleanupCategory.DUPLICATE_FILE
    assert observation.source_safety_classification is CleanupSafetyClassification.CAUTION


def test_stage4d3_protection_is_preserved_in_stage4e1(tmp_path: Path) -> None:
    path = tmp_path / "settings.db"
    path.write_bytes(b"user-data")
    metadata = path.lstat()
    candidate = ResidualCandidate(
        report_id=uuid4(),
        identity=ResidualIdentity(
            normalized_path=path,
            device_id=metadata.st_dev,
            file_id=metadata.st_ino,
            object_type=ResidualObjectType.FILE,
            size_bytes=metadata.st_size,
            modified_time_ns=metadata.st_mtime_ns,
        ),
        path=path,
        scan_root=tmp_path,
        source=ResidualSource.INSTALL_LOCATION,
        object_type=ResidualObjectType.FILE,
        size_bytes=metadata.st_size,
        classification=ResidualClassification.DATABASE,
        ownership_confidence=ResidualOwnershipConfidence.HIGH,
        protection_level=UserDataProtectionLevel.STRONGLY_PROTECTED,
        readable=True,
        reparse_or_symlink=False,
        recommendation=ResidualRecommendation.PROTECT,
    )
    observation = RepositoryOptimizationEvidenceSource._residual_observation(candidate)
    assert observation is not None
    assert observation.category is CleanupCategory.UNKNOWN
    assert observation.source_safety_classification is CleanupSafetyClassification.PROTECTED
    assert observation.source_protection_level is ProtectionLevel.STRONGLY_PROTECTED


def test_repository_failures_are_truthful_partial_sources() -> None:
    class BrokenStage1:
        def recent_matching_candidates(self, *_args: object, **_kwargs: object) -> object:
            raise AnalysisResultStoreError("unavailable")

    class BrokenResiduals:
        def list_eligible_contexts(self, *_args: object, **_kwargs: object) -> object:
            raise SoftwareResidualStoreError("unavailable")

    source = RepositoryOptimizationEvidenceSource(  # type: ignore[arg-type]
        BrokenStage1(), BrokenResiduals()
    )
    result = source.observations((), max_items=10, cancellation=CancellationToken())
    assert result.observations == ()
    assert result.partial_sources == (
        "stage1-analysis-history",
        "stage4d3-residual-history",
    )
