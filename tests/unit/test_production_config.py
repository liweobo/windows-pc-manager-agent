"""Production mode and release feature allow-list tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from platformdirs import user_data_path

from pc_manager_agent.config.production import (
    BuildMode,
    FeatureDisabledError,
    FeatureFlags,
    ProductionConfigValidator,
    ProductionRuntimeContext,
    ReleaseFeature,
)
from pc_manager_agent.config.settings import AppSettings


def _context(**updates: object) -> ProductionRuntimeContext:
    values: dict[str, object] = {
        "frozen_binary": True,
        "process_elevated": False,
        "executable_path": Path(r"C:\Program Files\Windows PC Manager Agent\app.exe"),
        "active_environment_names": frozenset({"PC_MANAGER_BUILD_MODE"}),
    }
    values.update(updates)
    return ProductionRuntimeContext.model_validate(values)


def _production_settings(**updates: object) -> AppSettings:
    values: dict[str, object] = {
        "build_mode": BuildMode.PRODUCTION,
        "feature_flags": FeatureFlags.private_rc_defaults(),
        "data_directory": user_data_path("WindowsPCManagerAgent", ensure_exists=False),
    }
    values.update(updates)
    return AppSettings.model_validate(values)


def test_private_rc_allow_list_is_read_only_and_exact() -> None:
    flags = FeatureFlags.private_rc_defaults()

    assert flags.enabled == {
        ReleaseFeature.FILE_ANALYSIS,
        ReleaseFeature.SYSTEM_DIAGNOSTICS,
        ReleaseFeature.SOFTWARE_ANALYSIS,
        ReleaseFeature.OPTIMIZATION_ANALYSIS,
    }
    flags.require(ReleaseFeature.FILE_ANALYSIS)
    with pytest.raises(FeatureDisabledError, match="FEATURE_DISABLED:software_uninstall"):
        flags.require(ReleaseFeature.SOFTWARE_UNINSTALL)


def test_environment_selects_fixed_production_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PC_MANAGER_BUILD_MODE", "production")

    settings = AppSettings.from_environment()

    assert settings.build_mode is BuildMode.PRODUCTION
    assert settings.feature_flags == FeatureFlags.private_rc_defaults()


def test_valid_unsigned_private_rc_configuration_passes() -> None:
    report = ProductionConfigValidator().validate(_production_settings(), _context())

    assert report.valid
    assert report.violations == ()


def test_production_smoke_test_may_use_an_isolated_data_directory(tmp_path: Path) -> None:
    isolated = _production_settings(data_directory=tmp_path)

    assert (
        ProductionConfigValidator()
        .validate(
            isolated,
            _context(smoke_test=True),
        )
        .valid
    )
    ordinary_report = ProductionConfigValidator().validate(isolated, _context())
    assert "DATA_DIRECTORY" in {item.code for item in ordinary_report.violations}


def test_safe_mode_allows_only_empty_features_and_disabled_provider() -> None:
    safe = _production_settings(
        safe_mode=True,
        feature_flags=FeatureFlags(),
        llm_provider="disabled",
    )
    assert ProductionConfigValidator().validate(safe, _context()).valid

    unsafe = safe.model_copy(update={"llm_provider": "openai"})
    report = ProductionConfigValidator().validate(unsafe, _context())
    assert "SAFE_MODE_PROVIDER" in {item.code for item in report.violations}


def test_safe_mode_rejects_enabled_provider() -> None:
    """Verify safe mode strictly enforces disabled LLM provider."""
    safe_with_openai = _production_settings(
        safe_mode=True,
        feature_flags=FeatureFlags(),
        llm_provider="openai",
        openai_model="gpt-4",
        openai_api_key="sk-test",
    )

    report = ProductionConfigValidator().validate(safe_with_openai, _context())
    assert not report.valid
    assert "SAFE_MODE_PROVIDER" in {item.code for item in report.violations}


@pytest.mark.parametrize(
    ("settings", "context", "code"),
    [
        (_production_settings(), _context(frozen_binary=False), "NOT_FROZEN"),
        (_production_settings(), _context(process_elevated=True), "MAIN_ELEVATED"),
        (
            _production_settings(privileged_broker_mode="mock"),
            _context(),
            "BROKER_ENABLED",
        ),
        (
            _production_settings(privileged_broker_trust_mode="development"),
            _context(),
            "BROKER_TRUST",
        ),
        (
            _production_settings(feature_flags=FeatureFlags.development_defaults()),
            _context(),
            "FEATURE_SET",
        ),
        (
            _production_settings(),
            _context(active_environment_names=frozenset({"PC_MANAGER_DEBUG_MODE"})),
            "DEVELOPMENT_ENVIRONMENT",
        ),
    ],
)
def test_production_validator_rejects_unsafe_configuration(
    settings: AppSettings,
    context: ProductionRuntimeContext,
    code: str,
) -> None:
    report = ProductionConfigValidator().validate(settings, context)

    assert not report.valid
    assert code in {item.code for item in report.violations}


def test_production_report_never_contains_environment_values() -> None:
    report = ProductionConfigValidator().validate(
        _production_settings(),
        _context(active_environment_names=frozenset({"PC_MANAGER_MOCK_SECRET"})),
    )

    rendered = report.model_dump_json()
    assert "MOCK_SECRET" not in rendered
    assert "DEVELOPMENT_ENVIRONMENT" in rendered
