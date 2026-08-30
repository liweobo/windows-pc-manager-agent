"""Bounded Stage 4E2 discovery over a synthetic known location."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.fixtures.system_cleanup import (
    build_system_cleanup_environment,
    mark_old,
    save_temp_report,
)

from pc_manager_agent.domain.system_cleanup_execution import SystemCleanupRequest
from pc_manager_agent.safety.system_cleanup_revalidation import (
    SystemCleanupRevalidationError,
)


@pytest.mark.performance
def test_fresh_discovery_stops_at_hard_item_budget(tmp_path: Path) -> None:
    """Prove the collector rejects the 101st child rather than building an unbounded list."""
    environment = build_system_cleanup_environment(tmp_path / "state.db")
    for index in range(101):
        path = environment.temp_root / f"old-{index:03d}.tmp"
        path.write_bytes(b"x")
        mark_old(path)
    report = save_temp_report(environment)
    try:
        with pytest.raises(SystemCleanupRevalidationError, match="item limit"):
            environment.service.assess(
                SystemCleanupRequest(
                    source_report_id=report.report_id,
                    selected_candidate_ids=(report.cleanup_candidates[0].candidate_id,),
                )
            )
        assert environment.recycle.calls == []
    finally:
        environment.close()
