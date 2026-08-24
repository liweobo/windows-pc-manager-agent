"""Branch coverage for Stage 4D2C1 preflight, Preview, and safety validation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pc_manager_agent.domain.software_uninstall_analysis import SoftwareTargetQuery
from pc_manager_agent.domain.system_diagnostics import (
    ProcessCollection,
    ProcessSnapshot,
    ServiceSnapshot,
)
from pc_manager_agent.domain.winget_uninstall import WingetPreflightState
from pc_manager_agent.orchestration.winget_execution_preflight import (
    WingetExecutionPreflightService,
)
from pc_manager_agent.safety.winget_uninstall_preview import WingetUninstallPreviewEngine
from pc_manager_agent.safety.winget_uninstall_validator import (
    WingetUninstallSafetyError,
    WingetUninstallSafetyValidator,
)
from pc_manager_agent.tools.manifest import CancellationToken
from tests.fixtures.winget_uninstall import build_winget_environment


class _ObservedSystem:
    def __init__(
        self,
        processes: tuple[ProcessSnapshot, ...],
        services: tuple[ServiceSnapshot, ...],
        *,
        process_warnings: tuple[str, ...] = (),
        service_warnings: tuple[str, ...] = (),
        truncated: bool = False,
    ) -> None:
        self.processes = processes
        self.services = services
        self.process_warnings = process_warnings
        self.service_warnings = service_warnings
        self.truncated = truncated

    def collect_processes(
        self,
        interval_seconds: float,
        max_processes: int,
        cancellation: CancellationToken,
    ) -> tuple[ProcessCollection, tuple[str, ...]]:
        del interval_seconds, max_processes, cancellation
        return (
            ProcessCollection(
                processes=self.processes,
                groups=(),
                complete_count=len(self.processes),
                partial_count=0,
                skipped_count=0,
                truncated=self.truncated,
            ),
            self.process_warnings,
        )

    def collect_services(
        self,
        max_items: int,
    ) -> tuple[tuple[ServiceSnapshot, ...], tuple[str, ...], bool]:
        del max_items
        return self.services, self.service_warnings, self.truncated


def _process(pid: int, name: str, path: Path | None) -> ProcessSnapshot:
    return ProcessSnapshot(
        pid=pid,
        name=name,
        executable_path=path,
        cpu_percent=0,
        memory_rss_bytes=0,
        memory_percent=0,
    )


def test_preflight_warns_for_related_process_and_blocks_busy_service_and_concurrency(
    tmp_path: Path,
) -> None:
    install_root = tmp_path / "app"
    software_environment = build_winget_environment(tmp_path / "state.sqlite3")
    try:
        software = software_environment.services.software_resolver.resolve(
            SoftwareTargetQuery(display_name="Example Clean App"),
            10,
            CancellationToken(),
        )[0].selected
        assert software is not None
        software = software.model_copy(update={"install_location": install_root})
        observed = _ObservedSystem(
            (
                _process(1, "app.exe", install_root / "app.exe"),
                _process(2, "winget.exe", tmp_path / "WindowsApps" / "winget.exe"),
                _process(3, "unknown.exe", None),
            ),
            (
                ServiceSnapshot(
                    name="ExampleService",
                    display_name="Example Service",
                    state="Running",
                    executable_path=install_root / "service.exe",
                ),
            ),
        )
        result = WingetExecutionPreflightService(observed).inspect(
            software,
            CancellationToken(),
            another_uninstall_active=True,
        )
        assert result.state is WingetPreflightState.BLOCKED
        assert result.winget_busy
        assert len(result.related_processes) == 1
        assert len(result.related_services) == 1
        assert any("will not close" in warning for warning in result.warnings)
        assert any("related service" in blocker for blocker in result.blockers)
        assert any("transaction is active" in blocker for blocker in result.blockers)
    finally:
        software_environment.close()


def test_preflight_blocks_missing_location_incomplete_evidence_and_cancellation(
    tmp_path: Path,
) -> None:
    environment = build_winget_environment(tmp_path / "state.sqlite3")
    try:
        software = environment.services.software_resolver.resolve(
            SoftwareTargetQuery(display_name="Example Clean App"),
            10,
            CancellationToken(),
        )[0].selected
        assert software is not None
        no_location = WingetExecutionPreflightService(_ObservedSystem((), ())).inspect(
            software.model_copy(update={"install_location": None}),
            CancellationToken(),
        )
        assert no_location.state is WingetPreflightState.UNKNOWN

        cancellation = CancellationToken()
        cancellation.cancel()
        incomplete = WingetExecutionPreflightService(
            _ObservedSystem(
                (),
                (),
                process_warnings=("partial",),
                service_warnings=("partial",),
                truncated=True,
            )
        ).inspect(software, cancellation)
        assert incomplete.state is WingetPreflightState.BLOCKED
        assert any("cancelled" in blocker for blocker in incomplete.blockers)
        assert any("incomplete" in blocker for blocker in incomplete.blockers)
    finally:
        environment.close()


def test_preview_ttl_and_validator_reject_blocked_expired_plan_and_invariant_changes(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="positive"):
        WingetUninstallPreviewEngine(0)
    environment = build_winget_environment(tmp_path / "validation.sqlite3")
    try:
        prepared = environment.services.service.prepare(
            "卸载 Example Clean App",
            SoftwareTargetQuery(display_name="Example Clean App"),
        )
        assert prepared.plan and prepared.preview
        with pytest.raises(ValueError, match="digests"):
            WingetUninstallPreviewEngine().build(
                prepared.plan.model_copy(update={"mapping_digest": "0" * 64}),
                prepared.preview.package,
                prepared.preview.software,
                prepared.preview.mapping,
                prepared.preview.availability,
                prepared.preview.capability,
                prepared.preview.execution_assessment,
                prepared.preview.preflight,
            )
        validator = WingetUninstallSafetyValidator()
        validator.validate(prepared.plan, prepared.preview, prepared.preview)
        validator.validate_invariant(
            prepared.plan,
            prepared.preview.invariant_digest(),
            prepared.preview,
        )

        blocked = prepared.preview.model_copy(update={"executable": False})
        with pytest.raises(WingetUninstallSafetyError, match="blocked"):
            validator.validate(prepared.plan, blocked, prepared.preview)

        expired = prepared.preview.model_copy(
            update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
        )
        with pytest.raises(WingetUninstallSafetyError, match="expired"):
            validator.validate(prepared.plan, expired, prepared.preview)
        with pytest.raises(WingetUninstallSafetyError, match="blocked or expired"):
            validator.validate_invariant(
                prepared.plan,
                prepared.preview.invariant_digest(),
                expired,
            )

        changed_plan = prepared.plan.model_copy(update={"user_goal": "changed"})
        with pytest.raises(WingetUninstallSafetyError, match="plan changed"):
            validator.validate(changed_plan, prepared.preview, prepared.preview)
        with pytest.raises(WingetUninstallSafetyError, match="plan changed"):
            validator.validate_invariant(
                changed_plan,
                prepared.preview.invariant_digest(),
                prepared.preview,
            )

        changed = prepared.preview.model_copy(
            update={
                "preflight": prepared.preview.preflight.model_copy(
                    update={"warnings": ("changed",)}
                )
            }
        )
        with pytest.raises(WingetUninstallSafetyError, match="evidence changed"):
            validator.validate(prepared.plan, prepared.preview, changed)
        with pytest.raises(WingetUninstallSafetyError, match="invariant changed"):
            validator.validate_invariant(prepared.plan, "0" * 64, prepared.preview)
    finally:
        environment.close()
