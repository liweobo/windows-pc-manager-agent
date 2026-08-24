"""Bounded synthetic large-directory performance for Stage 4D3."""

from __future__ import annotations

import time
import tracemalloc
from pathlib import Path

import pytest
from tests.fixtures.software_residuals import (
    build_residual_environment,
    residual_context,
)

from pc_manager_agent.domain.software_residuals import ResidualAnalysisStatus


@pytest.mark.performance
def test_ten_thousand_metadata_only_residuals_remain_bounded(tmp_path: Path) -> None:
    root = tmp_path / "synthetic-install-location"
    root.mkdir()
    file_count = 10_000
    for index in range(file_count):
        (root / f"item-{index:05d}.bin").touch()
    environment = build_residual_environment(
        tmp_path / "state.db",
        max_objects=file_count + 1,
        timeout_seconds=120,
    )
    context = residual_context(root)
    environment.repository.upsert_context(context)
    try:
        plan, loaded, _review = environment.service.prepare(
            "只读取精确安装目录的元数据", context.transaction_id
        )
        confirmation = environment.service.request_plan_confirmation(plan, loaded)
        environment.service.resolve_plan_confirmation(
            confirmation.confirmation_id, True, plan, loaded
        )
        tracemalloc.start()
        started = time.perf_counter()
        report = environment.service.analyze(plan, loaded)
        elapsed = time.perf_counter() - started
        _current, peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert report.status is ResidualAnalysisStatus.COMPLETED
        assert report.summary.candidates == file_count + 1
        assert report.deletion_performed is False
        print(
            f"stage4d3-benchmark objects={file_count + 1} seconds={elapsed:.3f} "
            f"peak_mib={peak_bytes / 1_048_576:.2f}"
        )
        assert elapsed < 120
        assert peak_bytes < 256 * 1_048_576
    finally:
        environment.close()
