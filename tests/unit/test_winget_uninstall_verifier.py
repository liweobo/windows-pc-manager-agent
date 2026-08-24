"""Dual-inventory verification and exact residual inspection tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from pc_manager_agent.domain.software_uninstall_analysis import SoftwareTargetQuery
from pc_manager_agent.domain.winget_uninstall import (
    WingetInventoryState,
    WingetPackageQuery,
    WingetProcessExecutionResult,
    WingetProcessResultCategory,
    WingetVerificationState,
)
from pc_manager_agent.orchestration.winget_residual_analyzer import WingetResidualAnalyzer
from pc_manager_agent.orchestration.winget_uninstall_verifier import WingetUninstallVerifier
from pc_manager_agent.tools.manifest import CancellationToken
from tests.fixtures.winget_uninstall import build_winget_environment


def _process(category: WingetProcessResultCategory) -> WingetProcessExecutionResult:
    return WingetProcessExecutionResult(
        category=category,
        launched=True,
        process_id=123,
        exit_code=None if category is WingetProcessResultCategory.MONITORING_STOPPED else 0,
        monitoring_stopped_after_launch=(
            category is WingetProcessResultCategory.MONITORING_STOPPED
        ),
    )


@pytest.mark.parametrize(
    ("keep_package", "keep_software", "expected"),
    (
        (True, True, WingetVerificationState.PACKAGE_STILL_PRESENT),
        (False, True, WingetVerificationState.PACKAGE_REMOVED_SOFTWARE_PRESENT),
        (False, False, WingetVerificationState.VERIFIED_REMOVED),
    ),
)
def test_dual_inventory_verification_matrix(
    tmp_path: Path,
    keep_package: bool,
    keep_software: bool,
    expected: WingetVerificationState,
) -> None:
    environment = build_winget_environment(tmp_path / f"{expected.value}.sqlite3")
    try:
        software = environment.services.software_resolver.resolve(
            SoftwareTargetQuery(display_name="Example Clean App"),
            10,
            CancellationToken(),
        )[0].selected
        package = environment.services.package_resolver.resolve(
            WingetPackageQuery(package_id="Example.CleanApp", installed_version="1.0.0"),
            10,
        )[0].selected
        assert software is not None and package is not None
        if not keep_package:
            environment.package_inventory.packages = ()
        if not keep_software:
            environment.software_inventory.entries = ()
        result = WingetUninstallVerifier(
            environment.services.package_resolver,
            environment.services.software_resolver,
        ).verify(package, software, _process(WingetProcessResultCategory.EXITED_ZERO), 10)
        assert result.state is expected
    finally:
        environment.close()


def test_partial_package_inventory_and_monitoring_stop_are_not_success(tmp_path: Path) -> None:
    environment = build_winget_environment(tmp_path / "partial.sqlite3")
    try:
        software = environment.services.software_resolver.resolve(
            SoftwareTargetQuery(display_name="Example Clean App"),
            10,
            CancellationToken(),
        )[0].selected
        from tests.fixtures.winget_uninstall import package

        target_package = package()
        assert software is not None
        environment.package_inventory.state = WingetInventoryState.FAILED
        environment.package_inventory.packages = ()
        environment.software_inventory.entries = ()
        verifier = WingetUninstallVerifier(
            environment.services.package_resolver,
            environment.services.software_resolver,
        )
        partial = verifier.verify(
            target_package,
            software,
            _process(WingetProcessResultCategory.EXITED_ZERO),
            10,
        )
        assert partial.state is WingetVerificationState.SOFTWARE_REMOVED_PACKAGE_UNKNOWN
        assert not partial.package_inventory_refreshed

        stopped = verifier.verify(
            target_package,
            software,
            _process(WingetProcessResultCategory.MONITORING_STOPPED),
            10,
        )
        assert stopped.state is WingetVerificationState.INTERRUPTED
        assert not stopped.package_inventory_refreshed
    finally:
        environment.close()


def test_residual_analyzer_is_exact_path_only_and_never_deletes(tmp_path: Path) -> None:
    analyzer = WingetResidualAnalyzer()
    unknown = analyzer.analyze(None)
    assert not unknown.checked_location

    missing = analyzer.analyze(tmp_path / "missing")
    assert missing.checked_location and missing.install_location_present is False

    existing = tmp_path / "existing"
    existing.mkdir()
    present = analyzer.analyze(existing)
    assert present.install_location_present is True
    assert not present.reparse_or_symlink
    assert not present.deletion_performed
