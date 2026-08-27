from __future__ import annotations

from pathlib import Path

import pytest
from platformdirs import user_data_path
from pydantic import ValidationError

from pc_manager_agent.config.settings import AppSettings


def test_settings_default_to_disabled_and_hide_secret(tmp_path: Path) -> None:
    settings = AppSettings(data_directory=tmp_path, openai_api_key="secret-value")
    assert settings.llm_provider == "disabled"
    assert settings.database_path == tmp_path / "state.db"
    assert "secret-value" not in repr(settings)
    assert "openai_api_key" not in settings.model_dump()


def test_settings_load_and_normalize_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PC_MANAGER_LLM_PROVIDER", " OPENAI ")
    monkeypatch.setenv("OPENAI_MODEL", " model-id ")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("PC_MANAGER_SCAN_MAX_FILES", "12")
    monkeypatch.setenv("PC_MANAGER_SCAN_TIMEOUT_SECONDS", "4")
    monkeypatch.setenv("PC_MANAGER_RESIDUAL_CLEANUP_MAX_SELECTED", "7")
    monkeypatch.setenv("PC_MANAGER_RESIDUAL_CLEANUP_NORMAL_TOTAL_BYTES", "4096")
    settings = AppSettings.from_environment()
    assert settings.llm_provider == "openai"
    assert settings.openai_model == "model-id"
    assert settings.scan_max_files == 12
    assert settings.scan_timeout_seconds == 4
    assert settings.trash_runtime_confirmation_ttl_seconds == 60
    assert settings.process_runtime_confirmation_ttl_seconds == 60
    assert settings.residual_cleanup_max_selected == 7
    assert settings.residual_cleanup_normal_total_bytes == 4096
    assert settings.residual_cleanup_runtime_confirmation_ttl_seconds == 60


def test_settings_reject_unknown_provider_and_invalid_limit() -> None:
    with pytest.raises(ValidationError, match="Unsupported"):
        AppSettings(llm_provider="other")
    with pytest.raises(ValidationError):
        AppSettings(scan_max_files=0)
    with pytest.raises(ValidationError):
        AppSettings(trash_max_selected=0)
    with pytest.raises(ValidationError):
        AppSettings(process_action_max_processes=0)
    with pytest.raises(ValidationError):
        AppSettings(process_graceful_timeout_seconds=31)
    with pytest.raises(ValidationError):
        AppSettings(residual_cleanup_max_contained_objects=0)


def test_empty_model_becomes_none() -> None:
    assert AppSettings(openai_model="  ").openai_model is None


def test_windows_broker_requires_fixed_shared_database_location(tmp_path: Path) -> None:
    broker = (tmp_path / "broker.exe").resolve()
    common = {
        "privileged_broker_mode": "windows",
        "privileged_broker_path": broker,
        "privileged_broker_expected_sha256": "a" * 64,
    }
    with pytest.raises(ValidationError, match="fixed per-user data directory"):
        AppSettings(data_directory=tmp_path / "different", **common)

    settings = AppSettings(
        data_directory=user_data_path("WindowsPCManagerAgent", ensure_exists=False),
        **common,
    )
    assert settings.privileged_broker_mode == "windows"
