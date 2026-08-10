from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from pc_manager_agent.domain.reports import ScanBatch, ScanRequest, ScanStatus
from pc_manager_agent.safety.path_policy import PathPolicy
from pc_manager_agent.tools.file_tools.scanner import DirectoryScannerTool
from pc_manager_agent.tools.manifest import CancellationToken


def test_streaming_scanner_flushes_bounded_batches_without_retaining_files(
    tmp_path: Path,
) -> None:
    for index in range(7):
        (tmp_path / f"file-{index}.txt").write_text(str(index), encoding="utf-8")
    batches: list[ScanBatch] = []
    session_id = uuid4()
    scanner = DirectoryScannerTool(
        PathPolicy.for_scan_root(tmp_path),
        batch_consumer=batches.append,
    )

    report = scanner.execute(
        ScanRequest(
            root=tmp_path,
            session_id=session_id,
            batch_size=3,
            retain_files=False,
        ),
        CancellationToken(),
    )

    assert report.files == ()
    assert report.summary.files_seen == 7
    assert report.summary.status is ScanStatus.COMPLETED
    assert [len(batch.files) for batch in batches] == [3, 3, 1]
    assert {batch.session_id for batch in batches} == {session_id}


def test_streaming_scanner_cancellation_keeps_first_safe_batch(tmp_path: Path) -> None:
    for index in range(10):
        (tmp_path / f"file-{index}.txt").write_text("x", encoding="utf-8")
    token = CancellationToken()
    batches: list[ScanBatch] = []

    def consume(batch: ScanBatch) -> None:
        batches.append(batch)
        token.cancel()

    scanner = DirectoryScannerTool(
        PathPolicy.for_scan_root(tmp_path),
        batch_consumer=consume,
    )
    report = scanner.execute(
        ScanRequest(
            root=tmp_path,
            session_id=uuid4(),
            batch_size=2,
            retain_files=False,
        ),
        token,
    )

    assert report.summary.status is ScanStatus.CANCELLED
    assert report.summary.files_seen == 2
    assert len(batches) == 1
    assert len(batches[0].files) == 2
