from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from pc_manager_agent.domain.file_analysis import (
    AnalysisMatchMode,
    AnalysisType,
    DuplicateFileAnalysisRequest,
    InactiveConfidence,
    InactiveFileAnalysisRequest,
    LargeFileAnalysisRequest,
)
from pc_manager_agent.domain.reports import FileMetadata, ScanBatch, ScanRequest
from pc_manager_agent.persistence.analysis_results import AnalysisResultRepository
from pc_manager_agent.safety.path_policy import PathPolicy
from pc_manager_agent.tools.file_tools.duplicate_analyzer import DuplicateFileAnalyzer
from pc_manager_agent.tools.file_tools.hashing import SafeFileHasher
from pc_manager_agent.tools.file_tools.inactive_file_analyzer import InactiveFileAnalyzer
from pc_manager_agent.tools.file_tools.large_file_analyzer import LargeFileAnalyzer
from pc_manager_agent.tools.file_tools.scanner import DirectoryScannerTool
from pc_manager_agent.tools.manifest import CancellationToken


def metadata(path: Path, *, size: int, age_days: int = 120) -> FileMetadata:
    timestamp = datetime.now(UTC) - timedelta(days=age_days)
    return FileMetadata(
        path=path,
        name=path.name,
        extension=path.suffix,
        media_type=None,
        size_bytes=size,
        created_at=timestamp,
        modified_at=timestamp,
        accessed_at=timestamp,
        scan_root=path.parent,
    )


def build_results(tmp_path: Path) -> AnalysisResultRepository:
    repository = AnalysisResultRepository(tmp_path / "analysis.db")
    repository.initialize()
    return repository


def test_large_file_analyzer_uses_inclusive_configured_threshold(tmp_path: Path) -> None:
    repository = build_results(tmp_path)
    session_id = uuid4()
    repository.create_session(session_id, (tmp_path,))
    repository.store_batch(
        ScanBatch(
            session_id=session_id,
            files=(
                metadata(tmp_path / "below.bin", size=99),
                metadata(tmp_path / "equal.bin", size=100),
                metadata(tmp_path / "above.bin", size=101),
            ),
        )
    )
    result = LargeFileAnalyzer(repository).analyze(
        LargeFileAnalysisRequest(
            analysis_session_id=session_id,
            minimum_size_bytes=100,
        ),
        CancellationToken(),
    )
    repository.finalize_matches(
        session_id,
        (AnalysisType.LARGE_FILES,),
        AnalysisMatchMode.ALL,
    )

    rows = repository.page_candidates(session_id)

    assert result.files == 2
    assert result.total_bytes == 201
    assert {row.metadata.name for row in rows} == {"equal.bin", "above.bin"}
    repository.close()


def test_inactive_assessment_never_treats_atime_as_definitive(tmp_path: Path) -> None:
    old = metadata(tmp_path / "old.bin", size=10, age_days=180)
    threshold = datetime.now(UTC) - timedelta(days=90)

    reliable = InactiveFileAnalyzer.assess(
        old,
        threshold=threshold,
        inactive_days=90,
        atime_reliable=True,
    )
    unreliable = InactiveFileAnalyzer.assess(
        old,
        threshold=threshold,
        inactive_days=90,
        atime_reliable=False,
    )
    recent = InactiveFileAnalyzer.assess(
        old.model_copy(update={"accessed_at": datetime.now(UTC)}),
        threshold=threshold,
        inactive_days=90,
        atime_reliable=True,
    )

    assert reliable.possibly_inactive
    assert reliable.confidence is InactiveConfidence.HIGH
    assert unreliable.confidence is InactiveConfidence.LOW
    assert not recent.possibly_inactive
    assert recent.confidence is InactiveConfidence.UNKNOWN


def test_inactive_analyzer_marks_only_evidence_supported_rows(tmp_path: Path) -> None:
    repository = build_results(tmp_path)
    session_id = uuid4()
    repository.create_session(session_id, (tmp_path,))
    repository.store_batch(
        ScanBatch(
            session_id=session_id,
            files=(
                metadata(tmp_path / "old.bin", size=20, age_days=180),
                metadata(tmp_path / "new.bin", size=30, age_days=2),
            ),
        )
    )
    analyzer = InactiveFileAnalyzer(
        repository,
        atime_reliability=lambda _root: None,
        now=lambda: datetime.now(UTC),
    )

    result = analyzer.analyze(
        InactiveFileAnalysisRequest(analysis_session_id=session_id, inactive_days=90),
        CancellationToken(),
    )
    repository.finalize_matches(
        session_id,
        (AnalysisType.INACTIVE_FILES,),
        AnalysisMatchMode.ALL,
    )

    rows = repository.page_candidates(session_id)
    assert result.files == 1
    assert rows[0].metadata.name == "old.bin"
    assert rows[0].inactive is not None
    repository.close()


def test_duplicate_analyzer_rejects_same_size_different_content(tmp_path: Path) -> None:
    (tmp_path / "same-a.bin").write_bytes(b"identical-content")
    (tmp_path / "same-b.bin").write_bytes(b"identical-content")
    (tmp_path / "different.bin").write_bytes(b"different-content")
    repository = build_results(tmp_path)
    session_id = uuid4()
    repository.create_session(session_id, (tmp_path,))
    policy = PathPolicy.for_scan_root(tmp_path)
    scanner = DirectoryScannerTool(policy, batch_consumer=repository.store_batch)
    scanner.execute(
        ScanRequest(
            root=tmp_path,
            session_id=session_id,
            retain_files=False,
            batch_size=2,
        ),
        CancellationToken(),
    )
    analyzer = DuplicateFileAnalyzer(repository, SafeFileHasher(policy))

    result = analyzer.analyze(
        DuplicateFileAnalysisRequest(analysis_session_id=session_id),
        CancellationToken(),
    )

    assert len(result.groups) == 1
    assert {item.name for item in result.groups[0].files} == {
        "same-a.bin",
        "same-b.bin",
    }
    assert result.groups[0].byte_verified
    assert len(result.groups[0].sha256) == 64
    assert not result.issues
    repository.close()


def test_duplicate_analyzer_ignores_empty_files_and_reports_changed_candidates(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.bin"
    second = tmp_path / "second.bin"
    first.write_bytes(b"same-content")
    second.write_bytes(b"same-content")
    (tmp_path / "empty-a.bin").touch()
    (tmp_path / "empty-b.bin").touch()
    repository = build_results(tmp_path)
    session_id = uuid4()
    repository.create_session(session_id, (tmp_path,))
    policy = PathPolicy.for_scan_root(tmp_path)
    DirectoryScannerTool(policy, batch_consumer=repository.store_batch).execute(
        ScanRequest(
            root=tmp_path,
            session_id=session_id,
            retain_files=False,
        ),
        CancellationToken(),
    )
    second.write_bytes(b"now-different")

    result = DuplicateFileAnalyzer(repository, SafeFileHasher(policy)).analyze(
        DuplicateFileAnalysisRequest(analysis_session_id=session_id),
        CancellationToken(),
    )

    assert not result.groups
    assert any(issue.path == second for issue in result.issues)
    assert not any("empty" in str(issue.path) for issue in result.issues)
    repository.close()


def test_duplicate_hashing_cancellation_is_terminal_not_an_error(tmp_path: Path) -> None:
    payload = b"x" * (3 * 1_048_576)
    (tmp_path / "large-a.bin").write_bytes(payload)
    (tmp_path / "large-b.bin").write_bytes(payload)
    repository = build_results(tmp_path)
    session_id = uuid4()
    repository.create_session(session_id, (tmp_path,))
    policy = PathPolicy.for_scan_root(tmp_path)
    DirectoryScannerTool(policy, batch_consumer=repository.store_batch).execute(
        ScanRequest(
            root=tmp_path,
            session_id=session_id,
            retain_files=False,
        ),
        CancellationToken(),
    )

    class DelayedCancellation(CancellationToken):
        def __init__(self) -> None:
            super().__init__()
            self.checks = 0

        def cancellation_requested(self) -> bool:
            self.checks += 1
            return self.checks >= 4

    result = DuplicateFileAnalyzer(repository, SafeFileHasher(policy)).analyze(
        DuplicateFileAnalysisRequest(analysis_session_id=session_id),
        DelayedCancellation(),
    )

    assert result.cancelled
    assert not result.issues
    repository.close()
