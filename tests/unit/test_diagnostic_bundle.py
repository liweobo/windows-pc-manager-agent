"""Reviewed local diagnostic bundle privacy and single-use authority tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zipfile import ZipFile

import pytest
from pydantic import SecretStr

import pc_manager_agent.diagnostics.bundle as bundle_module
from pc_manager_agent.config.production import BuildMode, ReleaseFeature
from pc_manager_agent.config.settings import AppSettings
from pc_manager_agent.diagnostics.bundle import (
    DiagnosticBundleError,
    DiagnosticBundleService,
    DiagnosticRuntimeMetadata,
    build_runtime_metadata,
)
from pc_manager_agent.observability.logging import LogRedactionPolicy
from pc_manager_agent.persistence.migrations import MigrationManager


def _metadata() -> DiagnosticRuntimeMetadata:
    return DiagnosticRuntimeMetadata(
        app_version="0.1.0",
        build_mode=BuildMode.PRODUCTION,
        safe_mode=False,
        enabled_features=(ReleaseFeature.FILE_ANALYSIS,),
        provider="disabled",
        schema_version=1,
        config_version=1,
        schema_digest="a" * 64,
        os_name="Windows",
        os_release="11",
        architecture="AMD64",
        python_version="3.13.1",
        frozen_binary=True,
        signing_status="NOT_CONFIGURED",
    )


def _write_logs(data_directory: Path) -> None:
    logs = data_directory / "logs"
    logs.mkdir(parents=True)
    (logs / "application.jsonl").write_text(
        json.dumps(
            {
                "timestamp": "2026-01-01T00:00:00+00:00",
                "severity": "ERROR",
                "component": "test",
                "event_code": "SAFE_ERROR",
                "message": "document private body password=hunter2",
                "details": {"api_key": "sk-secret", "path": r"C:\Users\Alice\file.txt"},
                "exception": {"type": "ValueError", "message": "token=secret"},
            }
        )
        + "\nnot-json-secret\n",
        encoding="utf-8",
    )


def test_prepare_review_approve_and_export_are_local_redacted_and_single_use(
    tmp_path: Path,
) -> None:
    data_directory = tmp_path / "data"
    _write_logs(data_directory)
    target = tmp_path / "support.zip"
    now = datetime(2026, 1, 1, tzinfo=UTC)
    service = DiagnosticBundleService(
        data_directory,
        redaction=LogRedactionPolicy(path_salt=b"fixed"),
    )

    preview = service.prepare(target, _metadata(), ("STARTUP_FAILED",), now=now)

    assert not target.exists()
    assert preview.output_path == target
    assert preview.risk_level == "R1"
    assert preview.rollback_level == "MANUAL"
    assert {entry.name for entry in preview.entries} == {
        "manifest.json",
        "recent-logs.jsonl",
    }
    assert any("raw audio" in item for item in preview.exclusions)
    authority = service.approve(preview, now=now + timedelta(seconds=1))
    assert authority.secret.get_secret_value() not in repr(authority)

    result = service.export(authority, now=now + timedelta(seconds=2))

    assert result.output_path == target
    assert result.size_bytes == target.stat().st_size
    assert not result.automatically_uploaded
    with ZipFile(target) as archive:
        assert tuple(sorted(archive.namelist())) == result.entries
        manifest = json.loads(archive.read("manifest.json"))
        logs = archive.read("recent-logs.jsonl").decode("utf-8")
    assert manifest["automatic_upload"] is False
    assert manifest["runtime"]["signing_status"] == "NOT_CONFIGURED"
    assert "SAFE_ERROR" in logs
    assert "MALFORMED_LOG_LINE" in logs
    rendered = json.dumps(manifest) + logs
    for forbidden in (
        "hunter2",
        "sk-secret",
        "Alice",
        "private body",
        "not-json-secret",
        "state.db",
        "audit_events",
    ):
        assert forbidden not in rendered
    with pytest.raises(DiagnosticBundleError, match="DIAGNOSTIC_AUTHORITY_INVALID"):
        service.export(authority, now=now + timedelta(seconds=3))


def test_changed_or_expired_preview_and_authority_are_rejected(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    service = DiagnosticBundleService(tmp_path, ttl_seconds=30)
    preview = service.prepare(tmp_path / "one.zip", _metadata(), now=now)
    changed = preview.model_copy(update={"content_digest": "b" * 64})
    with pytest.raises(DiagnosticBundleError, match="DIAGNOSTIC_PREVIEW_CHANGED"):
        service.approve(changed, now=now)

    with pytest.raises(DiagnosticBundleError, match="DIAGNOSTIC_PREVIEW_EXPIRED"):
        service.approve(preview, now=now + timedelta(seconds=31))

    second = service.prepare(tmp_path / "two.zip", _metadata(), now=now)
    authority = service.approve(second, now=now)
    with pytest.raises(DiagnosticBundleError, match="DIAGNOSTIC_AUTHORITY_ALREADY_ISSUED"):
        service.approve(second, now=now)
    with pytest.raises(DiagnosticBundleError, match="DIAGNOSTIC_AUTHORITY_EXPIRED"):
        service.export(authority, now=now + timedelta(seconds=31))


def test_forged_authority_and_changed_volatile_content_fail_closed(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    service = DiagnosticBundleService(tmp_path)
    preview = service.prepare(tmp_path / "one.zip", _metadata(), now=now)
    authority = service.approve(preview, now=now)
    forged = authority.model_copy(update={"secret": SecretStr("0" * 64)})
    with pytest.raises(DiagnosticBundleError, match="DIAGNOSTIC_AUTHORITY_INVALID"):
        service.export(forged, now=now)

    pending = service._pending[preview.preview_id]
    pending.entries["manifest.json"] = b"changed"
    with pytest.raises(DiagnosticBundleError, match="DIAGNOSTIC_CONTENT_CHANGED"):
        service.export(authority, now=now)


@pytest.mark.parametrize(
    "target",
    [
        Path("relative.zip"),
        Path(r"C:\target.txt"),
        Path(r"C:\bad. \target.zip"),
    ],
)
def test_invalid_targets_are_rejected(tmp_path: Path, target: Path) -> None:
    service = DiagnosticBundleService(tmp_path)
    with pytest.raises(DiagnosticBundleError, match="DIAGNOSTIC_TARGET_INVALID"):
        service.prepare(target, _metadata())


def test_existing_missing_parent_network_and_reparse_targets_are_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = tmp_path / "existing.zip"
    existing.write_bytes(b"user data")
    service = DiagnosticBundleService(tmp_path)
    for target in (existing, tmp_path / "missing" / "one.zip"):
        with pytest.raises(DiagnosticBundleError, match="DIAGNOSTIC_TARGET_INVALID"):
            service.prepare(target, _metadata())

    monkeypatch.setattr(bundle_module, "is_network_path", lambda _path: True)
    with pytest.raises(DiagnosticBundleError, match="DIAGNOSTIC_TARGET_INVALID"):
        service.prepare(tmp_path / "network.zip", _metadata())

    monkeypatch.setattr(bundle_module, "is_network_path", lambda _path: False)
    monkeypatch.setattr(bundle_module, "is_reparse_point", lambda path: path == tmp_path)
    with pytest.raises(DiagnosticBundleError, match="DIAGNOSTIC_TARGET_INVALID"):
        service.prepare(tmp_path / "reparse.zip", _metadata())


def test_error_code_and_constructor_bounds(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Preview TTL"):
        DiagnosticBundleService(tmp_path, ttl_seconds=1)
    with pytest.raises(ValueError, match="log bound"):
        DiagnosticBundleService(tmp_path, max_log_bytes=1)
    service = DiagnosticBundleService(tmp_path)
    with pytest.raises(DiagnosticBundleError, match="DIAGNOSTIC_ERROR_CODE_LIMIT"):
        service.prepare(tmp_path / "limit.zip", _metadata(), ("ERROR",) * 51)
    for code in ("", "lowercase", "A" * 81, "HAS SECRET"):
        with pytest.raises(DiagnosticBundleError, match="DIAGNOSTIC_ERROR_CODE_INVALID"):
            service.prepare(tmp_path / "invalid.zip", _metadata(), (code,))


def test_log_bound_and_reparse_log_are_content_free(tmp_path: Path) -> None:
    data = tmp_path / "data"
    logs = data / "logs"
    logs.mkdir(parents=True)
    (logs / "application.jsonl").write_text(
        (json.dumps({"event_code": "SAFE", "message": "x" * 4_000}) + "\n") * 3,
        encoding="utf-8",
    )
    service = DiagnosticBundleService(data, max_log_bytes=4_096)
    preview = service.prepare(tmp_path / "bounded.zip", _metadata())
    log_summary = next(item for item in preview.entries if item.name == "recent-logs.jsonl")
    assert log_summary.size_bytes < 4_096

    empty = DiagnosticBundleService(tmp_path / "missing-logs")
    empty_preview = empty.prepare(tmp_path / "empty.zip", _metadata())
    empty_log = next(item for item in empty_preview.entries if item.name == "recent-logs.jsonl")
    assert empty_log.size_bytes == 0


def test_export_failure_consumes_authority_and_leaves_no_partial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    service = DiagnosticBundleService(tmp_path)
    preview = service.prepare(tmp_path / "failed.zip", _metadata(), now=now)
    authority = service.approve(preview, now=now)
    monkeypatch.setattr(
        bundle_module.os, "link", lambda *_args: (_ for _ in ()).throw(OSError("unsupported"))
    )

    with pytest.raises(DiagnosticBundleError, match="DIAGNOSTIC_EXPORT_FAILED"):
        service.export(authority, now=now)

    assert not (tmp_path / "failed.zip").exists()
    assert not tuple(tmp_path.glob("*.tmp"))
    with pytest.raises(DiagnosticBundleError, match="DIAGNOSTIC_AUTHORITY_INVALID"):
        service.export(authority, now=now)


def test_runtime_metadata_contains_no_local_identity_or_secret(tmp_path: Path) -> None:
    migration = MigrationManager(tmp_path / "data" / "state.db", "0.1.0").migrate()
    settings = AppSettings(
        data_directory=tmp_path / "data",
        openai_api_key="secret-value",
    )

    metadata = build_runtime_metadata(settings, migration, frozen_binary=False)

    rendered = metadata.model_dump_json()
    assert metadata.telemetry == "NOT_IMPLEMENTED"
    assert not metadata.automatic_upload
    assert "secret-value" not in rendered
    assert str(tmp_path) not in rendered


def test_confirmation_and_export_callbacks_receive_only_reviewed_models(tmp_path: Path) -> None:
    confirmations: list[tuple[str, bool]] = []
    exports: list[tuple[str, int]] = []
    service = DiagnosticBundleService(
        tmp_path,
        on_confirmation=lambda preview, approved: confirmations.append(
            (preview.content_digest, approved)
        ),
        on_exported=lambda result: exports.append((result.sha256, result.size_bytes)),
    )
    preview = service.prepare(tmp_path / "approved.zip", _metadata())

    authority = service.approve(preview)
    result = service.export(authority)

    assert confirmations == [(preview.content_digest, True)]
    assert exports == [(result.sha256, result.size_bytes)]


def test_rejection_callback_runs_before_volatile_content_is_discarded(tmp_path: Path) -> None:
    decisions: list[bool] = []
    service = DiagnosticBundleService(
        tmp_path,
        on_confirmation=lambda _preview, approved: decisions.append(approved),
    )
    preview = service.prepare(tmp_path / "rejected.zip", _metadata())

    service.reject(preview)

    assert decisions == [False]
    assert preview.preview_id not in service._pending
    assert not (tmp_path / "rejected.zip").exists()
    with pytest.raises(DiagnosticBundleError, match="DIAGNOSTIC_PREVIEW_CHANGED"):
        service.reject(preview)


def test_failed_mandatory_callback_denies_authority_or_removes_output(tmp_path: Path) -> None:
    def fail_confirmation(_preview: object, _approved: bool) -> None:
        raise RuntimeError("audit unavailable")

    blocked = DiagnosticBundleService(tmp_path, on_confirmation=fail_confirmation)
    preview = blocked.prepare(tmp_path / "blocked.zip", _metadata())
    with pytest.raises(RuntimeError, match="audit unavailable"):
        blocked.approve(preview)
    assert blocked._pending[preview.preview_id].authority_hash is None

    exported = DiagnosticBundleService(
        tmp_path,
        on_exported=lambda _result: (_ for _ in ()).throw(RuntimeError("audit unavailable")),
    )
    export_preview = exported.prepare(tmp_path / "removed.zip", _metadata())
    authority = exported.approve(export_preview)
    with pytest.raises(DiagnosticBundleError, match="DIAGNOSTIC_EXPORT_FAILED"):
        exported.export(authority)
    assert not (tmp_path / "removed.zip").exists()
