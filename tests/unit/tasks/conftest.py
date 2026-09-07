"""Stage 5E task test composition without Windows actions or network access."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from pc_manager_agent.app.tasks import FinalTaskServices, build_final_task_services
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.config.settings import AppSettings


@pytest.fixture
def task_services(tmp_path: Path) -> Iterator[FinalTaskServices]:
    """Build isolated final-task storage and audit services."""
    settings = AppSettings(data_directory=tmp_path / "app")
    audit = AuditRepository(settings.database_path)
    audit.initialize()
    services = build_final_task_services(settings, audit)
    try:
        yield services
    finally:
        services.close()
        audit.close()
