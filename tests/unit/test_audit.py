from __future__ import annotations

from pathlib import Path

import pytest

from pc_manager_agent.audit.models import AuditEvent
from pc_manager_agent.audit.redaction import REDACTED, is_sensitive_key, redact_json, redact_text
from pc_manager_agent.audit.repository import AuditRepository, AuditUnavailableError
from pc_manager_agent.domain.risk import RiskLevel


def test_recursive_redaction_preserves_shape() -> None:
    value = {
        "api_key": "top-secret",
        "nested": {"sessionToken": "nested-secret", "safe": ["token=abc", 1]},
    }
    redacted = redact_json(value)
    assert redacted == {
        "api_key": REDACTED,
        "nested": {"sessionToken": REDACTED, "safe": [f"token={REDACTED}", 1]},
    }
    assert is_sensitive_key("AuthorizationHeader")
    assert not is_sensitive_key("file_count")
    assert redact_text("password=hunter2 more") == f"password={REDACTED} more"


def test_repository_requires_initialization(tmp_path: Path) -> None:
    repository = AuditRepository(tmp_path / "audit.db")
    event = AuditEvent(event_type="test", app_version="0.1.0")
    with pytest.raises(AuditUnavailableError, match="not initialized"):
        repository.record(event)
    with pytest.raises(AuditUnavailableError, match="not initialized"):
        repository.list_recent()
    repository.close()


def test_repository_records_redacted_event(tmp_path: Path) -> None:
    repository = AuditRepository(tmp_path / "audit.db")
    repository.initialize()
    repository.record(
        AuditEvent(
            event_type="tool.completed",
            original_request="api_key=secret scan",
            parameters={"OPENAI_API_KEY": "secret", "root": str(tmp_path)},
            result={"count": 1},
            risk_level=RiskLevel.R0,
            confirmation_required=True,
            confirmation_result="APPROVED",
            app_version="0.1.0",
        )
    )
    rows = repository.list_recent(10)
    assert len(rows) == 1
    assert rows[0].parameters["OPENAI_API_KEY"] == REDACTED
    assert "secret" not in (rows[0].original_request or "")
    assert rows[0].risk_level == "R0"
    assert len(repository.list_recent(0)) == 1
    repository.close()


def test_repository_rejects_corrupt_database(tmp_path: Path) -> None:
    database = tmp_path / "broken.db"
    database.write_text("not a sqlite database", encoding="utf-8")
    repository = AuditRepository(database)
    with pytest.raises(AuditUnavailableError, match="initialization"):
        repository.initialize()
    repository.close()
