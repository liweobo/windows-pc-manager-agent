from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.config.settings import AppSettings


@pytest.fixture
def runtime(tmp_path: Path) -> ApplicationRuntime:
    application_runtime = ApplicationRuntime(AppSettings(data_directory=tmp_path / "app-data"))
    yield application_runtime
    application_runtime.close()
