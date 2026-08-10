from __future__ import annotations

import time
import tracemalloc
from pathlib import Path
from uuid import uuid4

import pytest

from pc_manager_agent.domain.reports import ScanRequest, ScanStatus
from pc_manager_agent.persistence.analysis_results import AnalysisResultRepository
from pc_manager_agent.safety.path_policy import PathPolicy
from pc_manager_agent.tools.file_tools.scanner import DirectoryScannerTool
from pc_manager_agent.tools.manifest import CancellationToken


@pytest.mark.performance
def test_streaming_scan_of_ten_thousand_files_is_bounded(tmp_path: Path) -> None:
    root = tmp_path / "large-tree"
    root.mkdir()
    file_count = 10_000
    for index in range(file_count):
        (root / f"item-{index:05d}.txt").touch()

    repository = AnalysisResultRepository(tmp_path / "results.db")
    repository.initialize()
    session_id = uuid4()
    repository.create_session(session_id, (root,))
    scanner = DirectoryScannerTool(
        PathPolicy.for_scan_root(root),
        batch_consumer=repository.store_batch,
    )
    tracemalloc.start()
    started = time.perf_counter()
    report = scanner.execute(
        ScanRequest(
            root=root,
            session_id=session_id,
            batch_size=250,
            max_files=file_count + 1,
            retain_files=False,
        ),
        CancellationToken(),
    )
    elapsed = time.perf_counter() - started
    _current, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert report.summary.status is ScanStatus.COMPLETED
    assert report.summary.files_seen == file_count
    assert report.files == ()
    assert repository.record_count(session_id) == file_count
    print(
        f"stage1-benchmark files={file_count} scanner_seconds={elapsed:.3f} "
        f"peak_mib={peak_bytes / 1_048_576:.2f}"
    )
    assert elapsed < 120
    assert peak_bytes < 256 * 1_048_576
    repository.close()
