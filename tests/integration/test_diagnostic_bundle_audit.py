"""Diagnostic export must retain confirmation/result audit without content or target paths."""

from __future__ import annotations

from pathlib import Path

from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.config.settings import AppSettings
from pc_manager_agent.diagnostics.bundle import build_runtime_metadata


def test_runtime_diagnostic_export_records_minimal_mandatory_audit(tmp_path: Path) -> None:
    runtime = ApplicationRuntime(AppSettings(data_directory=tmp_path / "data"))
    target = tmp_path / "support-private-name.zip"
    try:
        preview = runtime.diagnostic_bundle.prepare(
            target,
            build_runtime_metadata(runtime.settings, runtime.migration_report, frozen_binary=False),
        )
        authority = runtime.diagnostic_bundle.approve(preview)

        result = runtime.diagnostic_bundle.export(authority)

        rows = runtime.audit.list_recent(10)
        assert [row.event_type for row in rows[:2]] == [
            "diagnostic_bundle.exported",
            "diagnostic_bundle.confirmation.resolved",
        ]
        confirmation = rows[1]
        exported = rows[0]
        assert confirmation.confirmation_result == "APPROVED"
        assert confirmation.parameters["content_digest"] == preview.content_digest
        assert exported.parameters == {"sha256": result.sha256, "entry_count": 2}
        rendered = repr((confirmation.parameters, exported.parameters, exported.result))
        assert str(target) not in rendered
        assert "support-private-name" not in rendered
    finally:
        runtime.close()
