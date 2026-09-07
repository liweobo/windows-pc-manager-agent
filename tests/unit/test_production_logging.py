"""Structured production logging, redaction, and rotation tests."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

import pc_manager_agent.observability.logging as logging_module
from pc_manager_agent.config.production import BuildMode
from pc_manager_agent.observability.logging import (
    REDACTED,
    LogConfigurationError,
    LogRedactionPolicy,
    StructuredJsonFormatter,
    configure_application_logging,
)


def test_redaction_removes_sensitive_fields_tokens_queries_and_paths() -> None:
    policy = LogRedactionPolicy(path_salt=b"fixed", max_text_chars=128)
    value = {
        "password": "correct horse battery staple",
        "nested": {"api-key": "sk-live-secret-token", "count": 3},
        "path": Path(r"C:\Users\Alice\Private\notes.txt"),
        "message": (
            "Authorization: Bearer abcdefghijklmnop "
            "token=ghp_abcdefghijklmnop "
            "https://example.test/path?code=oauth-secret"
        ),
    }

    redacted = policy.redact(value)

    assert isinstance(redacted, dict)
    assert redacted["password"] == REDACTED
    assert redacted["nested"] == {"api-key": REDACTED, "count": 3}
    assert str(redacted["path"]).startswith("[PATH:")
    rendered = json.dumps(redacted)
    for secret in ("correct horse", "sk-live", "ghp_", "oauth-secret", "Alice"):
        assert secret not in rendered
    assert "REDACTED_QUERY" in rendered


def test_redaction_blocks_content_keys_bounds_sequences_and_unknown_objects() -> None:
    policy = LogRedactionPolicy(path_salt=b"fixed", max_text_chars=64)

    redacted = policy.redact(
        {
            "document_content": "private body",
            "raw_audio": b"pcm",
            "items": list(range(150)),
            "unknown": object(),
            "long": "x" * 100,
        }
    )

    assert isinstance(redacted, dict)
    assert redacted["document_content"] == REDACTED
    assert redacted["raw_audio"] == REDACTED
    assert len(redacted["items"]) == 100  # type: ignore[arg-type]
    assert "TRUNCATED" in str(redacted["long"])
    assert "private body" not in json.dumps(redacted)
    with pytest.raises(ValueError, match="max_text_chars"):
        LogRedactionPolicy(max_text_chars=32)


def test_formatter_omits_traceback_source_and_sanitizes_identifiers() -> None:
    formatter = StructuredJsonFormatter(LogRedactionPolicy(path_salt=b"fixed"))
    try:
        raise ValueError(r"password=hunter2 at C:\Users\Alice\secret.txt")
    except ValueError:
        record = logging.LogRecord(
            name="pc_manager_agent.test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="failed token=abcdefghijk",
            args=(),
            exc_info=__import__("sys").exc_info(),
        )
    record.component = "unsafe component with spaces"
    record.event_code = "SAFE_EVENT"
    record.trace_id = "trace-1"
    record.task_id = "bad task id"
    record.details = {"cookie": "session=secret", "count": 1}

    payload = json.loads(formatter.format(record))

    assert payload["component"] == "application"
    assert payload["event_code"] == "SAFE_EVENT"
    assert payload["trace_id"] == "trace-1"
    assert payload["task_id"] == "INVALID"
    assert payload["details"]["cookie"] == REDACTED
    assert "traceback" not in payload
    rendered = json.dumps(payload)
    assert "hunter2" not in rendered
    assert "Alice" not in rendered


def test_production_logger_is_info_bounded_and_rotates(tmp_path: Path) -> None:
    runtime = configure_application_logging(
        tmp_path,
        BuildMode.PRODUCTION,
        max_bytes=64 * 1024,
        backup_count=2,
        debug_requested=True,
        redaction=LogRedactionPolicy(path_salt=b"fixed"),
    )
    try:
        assert runtime.logger.level == logging.INFO
        runtime.logger.debug("must not be emitted")
        for index in range(400):
            runtime.logger.info(
                "bounded event %s %s",
                index,
                "x" * 300,
                extra={
                    "component": "test",
                    "event_code": "ROTATION_TEST",
                    "details": {"api_key": "sk-super-secret"},
                },
            )
        runtime.handler.flush()
    finally:
        runtime.close()

    files = tuple((tmp_path / "logs").glob("application.jsonl*"))
    assert 1 < len(files) <= 3
    assert all(path.stat().st_size <= 64 * 1024 for path in files)
    combined = "".join(path.read_text(encoding="utf-8") for path in files)
    assert "must not be emitted" not in combined
    assert "sk-super-secret" not in combined
    for line in combined.splitlines():
        json.loads(line)


def test_development_debug_and_configuration_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = configure_application_logging(
        tmp_path / "development",
        BuildMode.DEVELOPMENT,
        debug_requested=True,
    )
    try:
        assert runtime.logger.level == logging.DEBUG
    finally:
        runtime.close()

    with pytest.raises(LogConfigurationError, match="LOG_SIZE_POLICY_INVALID"):
        configure_application_logging(tmp_path, BuildMode.PRODUCTION, max_bytes=1)
    with pytest.raises(LogConfigurationError, match="LOG_RETENTION_POLICY_INVALID"):
        configure_application_logging(tmp_path, BuildMode.PRODUCTION, backup_count=0)

    unsafe = tmp_path / "unsafe"
    monkeypatch.setattr(
        logging_module,
        "is_reparse_point",
        lambda path: path.name in {"unsafe", "logs", "application.jsonl"},
    )
    with pytest.raises(LogConfigurationError, match="LOG_DIRECTORY_UNSAFE"):
        configure_application_logging(unsafe, BuildMode.PRODUCTION)
