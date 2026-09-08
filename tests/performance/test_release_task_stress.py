"""Synthetic Stage 7A task lifecycle stress baseline with no domain execution."""

from __future__ import annotations

import time
import tracemalloc
from pathlib import Path
from uuid import uuid4

import pytest

from pc_manager_agent.app.tasks import build_final_task_services
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.config.settings import AppSettings
from pc_manager_agent.domain.computer_tasks import (
    AutonomyLevel,
    ComputerTaskKind,
    ComputerTaskState,
)
from pc_manager_agent.domain.task_workflows import DomainType


@pytest.mark.performance
def test_create_and_cancel_one_hundred_synthetic_tasks_is_bounded(tmp_path: Path) -> None:
    settings = AppSettings(data_directory=tmp_path / "task-stress")
    audit = AuditRepository(settings.database_path)
    audit.initialize()
    services = build_final_task_services(settings, audit)
    tracemalloc.start()
    started = time.perf_counter()
    try:
        for index in range(100):
            task = services.orchestrator.create_task(
                f"Synthetic read-only task {index}",
                (DomainType.SYSTEM,),
                root_request_id=uuid4(),
                kind=ComputerTaskKind.ANALYSIS_ONLY,
                autonomy=AutonomyLevel.PLAN_AND_ANALYZE,
            )
            cancelled = services.orchestrator.cancel(task.task_id)
            assert cancelled.state is ComputerTaskState.CANCELLED
        duration = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        assert len(services.orchestrator.list_recent(limit=100)) == 100
    finally:
        tracemalloc.stop()
        services.close()
        audit.close()

    print(f"task-stress count=100 duration={duration:.3f}s peak_mib={peak / 1024**2:.2f}")
    assert duration < 30
    assert peak < 128 * 1024**2
