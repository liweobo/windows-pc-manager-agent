"""Finite CLI and reduction-only safe-mode configuration tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from pc_manager_agent.bootstrap import main as bootstrap_main
from pc_manager_agent.config.production import (
    FeatureFlags,
    ProductionConfigReport,
    ProductionConfigurationError,
    ProductionConfigViolation,
)
from pc_manager_agent.config.settings import AppSettings
from pc_manager_agent.main import (
    _smoke_failure_exit_code,
    build_parser,
    main,
    reduce_to_safe_mode,
)


def test_cli_contains_only_fixed_non_bypass_switches() -> None:
    parsed = build_parser().parse_args(["--smoke-test", "--safe-mode"])
    assert parsed.smoke_test
    assert parsed.safe_mode

    for forbidden in ("--force", "--admin", "--disable-safety", "--run-shell"):
        with pytest.raises(SystemExit):
            build_parser().parse_args([forbidden])


def test_version_exits_before_runtime_and_tolerates_windowed_stdout(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert main(["--version"]) == 0
    assert capsys.readouterr().out == "1.0.0-rc.1\n"
    monkeypatch.setattr("pc_manager_agent.main.sys.stdout", None)
    assert main(["--version"]) == 0


def test_lightweight_bootstrap_handles_exact_version_without_gui_import(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert bootstrap_main(["--version"]) == 0
    assert capsys.readouterr().out == "1.0.0-rc.1\n"
    monkeypatch.setattr("pc_manager_agent.bootstrap.sys.stdout", None)
    assert bootstrap_main(["--version"]) == 0


def test_frozen_smoke_test_uses_production_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[AppSettings, bool]] = []

    def fake_run(settings: AppSettings, *, smoke_test: bool = False) -> int:
        observed.append((settings, smoke_test))
        return 0

    monkeypatch.setattr("pc_manager_agent.main.run_application", fake_run)
    monkeypatch.setattr("pc_manager_agent.main.sys.frozen", True, raising=False)
    monkeypatch.setenv("PC_MANAGER_BUILD_MODE", "production")

    assert main(["--smoke-test"]) == 0
    assert observed[0][0].build_mode.value == "production"
    assert observed[0][0].data_directory != AppSettings().data_directory
    assert observed[0][1]


def test_unattended_smoke_distinguishes_exact_elevated_main_denial() -> None:
    elevated_only = ProductionConfigurationError(
        ProductionConfigReport(
            valid=False,
            violations=(
                ProductionConfigViolation(code="MAIN_ELEVATED", message="standard-user required"),
            ),
        )
    )
    multiple_failures = ProductionConfigurationError(
        ProductionConfigReport(
            valid=False,
            violations=(
                ProductionConfigViolation(code="MAIN_ELEVATED", message="standard-user required"),
                ProductionConfigViolation(code="FEATURE_SET", message="feature drift"),
            ),
        )
    )

    assert _smoke_failure_exit_code(elevated_only) == 23
    assert _smoke_failure_exit_code(multiple_failures) == 24
    assert _smoke_failure_exit_code(RuntimeError("startup failed")) == 24


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
