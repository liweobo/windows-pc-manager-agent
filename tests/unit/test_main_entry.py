"""Finite CLI and reduction-only safe-mode configuration tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from pc_manager_agent.config.production import FeatureFlags
from pc_manager_agent.config.settings import AppSettings
from pc_manager_agent.main import build_parser, reduce_to_safe_mode


def test_cli_contains_only_fixed_non_bypass_switches() -> None:
    parsed = build_parser().parse_args(["--smoke-test", "--safe-mode"])
    assert parsed.smoke_test
    assert parsed.safe_mode

    for forbidden in ("--force", "--admin", "--disable-safety", "--run-shell"):
        with pytest.raises(SystemExit):
            build_parser().parse_args([forbidden])


def test_safe_mode_only_removes_authority_and_provider_configuration(tmp_path: Path) -> None:
    settings = AppSettings(
        data_directory=tmp_path,
        llm_provider="openai",
        openai_model="model-id",
        openai_api_key="secret",
        privileged_broker_mode="mock",
    )

    safe = reduce_to_safe_mode(settings)

    assert safe.safe_mode
    assert safe.feature_flags == FeatureFlags()
    assert safe.llm_provider == "disabled"
    assert safe.openai_model is None
    assert safe.openai_api_key is None
    assert safe.privileged_broker_mode == "disabled"
    assert safe.privileged_broker_path is None
    assert safe.privileged_broker_expected_sha256 is None
