"""Legacy user state survives the mandatory migration before runtime composition."""

from __future__ import annotations

from pathlib import Path

from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.authorization.models import AuthorizedPath, AuthorizedPathKind
from pc_manager_agent.config.settings import AppSettings
from pc_manager_agent.persistence.authorized_paths import AuthorizedPathRepository


def test_legacy_authorized_path_survives_runtime_migration(tmp_path: Path) -> None:
    data_directory = tmp_path / "app-data"
    database = data_directory / "state.db"
    selected_root = tmp_path / "selected"
    selected_root.mkdir()
    expected = AuthorizedPath(
        path=selected_root,
        label="Selected",
        kind=AuthorizedPathKind.AUTHORIZED,
    )
    legacy = AuthorizedPathRepository(database)
    legacy.initialize()
    legacy.add(expected)
    legacy.close()

    settings = AppSettings(data_directory=data_directory)
    runtime = ApplicationRuntime(settings)
    try:
        assert runtime.migration_report.from_version == 0
        assert runtime.migration_report.backup is not None
        assert runtime.authorized_path_repository.get(expected.path_id) == expected
    finally:
        runtime.close()

    reopened = ApplicationRuntime(settings)
    try:
        assert not reopened.migration_report.migrated
        assert reopened.authorized_path_repository.get(expected.path_id) == expected
    finally:
        reopened.close()
