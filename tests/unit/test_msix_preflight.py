"""Unit coverage for read-only MSIX process/service preflight."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from pc_manager_agent.domain.msix_uninstall import MsixPreflightState
from pc_manager_agent.orchestration.msix_execution_preflight import (
    MsixExecutionPreflightService,
    _inside,
)
from tests.unit.test_msix_uninstall import package


class FakeDiagnostics:
    """Return bounded synthetic process and service path evidence."""

    def __init__(
        self,
        process_path: Path | None,
        service_path: Path | None,
        *,
        running_service: bool = False,
        warnings: tuple[str, ...] = (),
    ) -> None:
        self.process_path = process_path
        self.service_path = service_path
        self.running_service = running_service
        self.warnings = warnings

    def collect_processes(self, interval: float, limit: int, cancellation: object):
        """Return one optional process with the shape consumed by preflight."""
        rows = (
            (SimpleNamespace(executable_path=self.process_path),)
            if self.process_path is not None
            else ()
        )
        return SimpleNamespace(processes=rows, truncated=False), self.warnings

    def collect_services(self, limit: int):
        """Return one optional Windows service path and state."""
        rows = (
            (
                SimpleNamespace(
                    executable_path=self.service_path,
                    state="running" if self.running_service else "stopped",
                ),
            )
            if self.service_path is not None
            else ()
        )
        return rows, self.warnings, False


def test_preflight_warns_for_process_but_does_not_control_it(tmp_path: Path) -> None:
    """A related app process is visible but does not grant termination authority."""
    root = tmp_path / "package"
    root.mkdir()
    executable = root / "app.exe"
    executable.touch()
    target = package().model_copy(update={"installed_path": str(root)})
    platform = FakeDiagnostics(executable, None)
    result = MsixExecutionPreflightService(platform).inspect(target, False)  # type: ignore[arg-type]
    assert result.state is MsixPreflightState.READY
    assert result.related_process_count == 1
    assert "none will be closed" in result.warnings[0]


def test_preflight_blocks_running_service_incomplete_probe_and_concurrency(
    tmp_path: Path,
) -> None:
    """Running service, warning, and another uninstall independently fail closed."""
    root = tmp_path / "package"
    root.mkdir()
    service_exe = root / "service.exe"
    service_exe.touch()
    target = package().model_copy(update={"installed_path": str(root)})
    platform = FakeDiagnostics(
        None,
        service_exe,
        running_service=True,
        warnings=("partial",),
    )
    result = MsixExecutionPreflightService(platform).inspect(target, True)  # type: ignore[arg-type]
    assert result.state is MsixPreflightState.BLOCKED
    assert len(result.blockers) == 3
    assert result.related_service_count == 1


def test_preflight_blocks_missing_install_path_and_path_escape(tmp_path: Path) -> None:
    """Unknown root blocks and resolved sibling paths do not correlate."""
    target = package().model_copy(update={"installed_path": None})
    result = MsixExecutionPreflightService(FakeDiagnostics(None, None)).inspect(  # type: ignore[arg-type]
        target, False
    )
    assert result.state is MsixPreflightState.UNKNOWN
    root = tmp_path / "root"
    sibling = tmp_path / "sibling"
    root.mkdir()
    sibling.mkdir()
    assert _inside(sibling, root) is False
