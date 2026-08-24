"""Bounded selected-only Fresh Revalidation benchmark for Stage 4D4."""

from __future__ import annotations

import time
import tracemalloc
from pathlib import Path

import pytest
from tests.fixtures.residual_cleanup import (
    build_residual_cleanup_environment,
    create_residual_report,
)
from tests.fixtures.software_residuals import residual_context

from pc_manager_agent.domain.residual_cleanup import ResidualCleanupRequest


@pytest.mark.performance
def test_five_thousand_object_fresh_snapshot_is_bounded_and_read_only(
    tmp_path: Path,
) -> None:
    root = tmp_path / "SyntheticProduct"
    root.mkdir()
    file_count = 5_000
    for index in range(file_count):
        (root / f"item-{index:05d}.bin").touch()
    environment = build_residual_cleanup_environment(
        tmp_path / "state.db",
        max_objects=file_count + 1,
    )
    context = residual_context(root)
    try:
        report = create_residual_report(environment, context)
        candidate = next(item for item in report.candidates if item.path == root)
        tracemalloc.start()
        started = time.perf_counter()
        assessment = environment.service.assess(
            ResidualCleanupRequest(
                source_report_id=report.report_id,
                selected_residual_ids=(candidate.candidate_id,),
            )
        )
        elapsed = time.perf_counter() - started
        _current, peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert assessment.all_eligible
        assert assessment.items[0].material is not None
        assert assessment.items[0].material.tree.object_count == file_count + 1
        assert environment.recycle.calls == []
        assert sum(1 for _item in root.iterdir()) == file_count
        print(
            f"stage4d4-fresh-scan objects={file_count + 1} seconds={elapsed:.3f} "
            f"peak_mib={peak_bytes / 1_048_576:.2f}"
        )
        assert elapsed < 120
        assert peak_bytes < 256 * 1_048_576
    finally:
        environment.close()
