"""Deterministic Stage 4A fixtures that never touch a real operating-system process."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pc_manager_agent.domain.process_actions import (
    ProcessIdentity,
    ProcessMemberResult,
    ProcessMemberResultState,
    ProcessObservation,
)
from pc_manager_agent.platform_support.base import CancellationSignal


def process_observation(
    *,
    pid: int = 4_001,
    name: str = "demo.exe",
    path: Path = Path("C:/Users/test/App/demo.exe"),
    owner_sid: str = "S-1-5-21-1000",
    session_id: int = 1,
    create_second: int = 1,
    window_count: int = 1,
    service_names: tuple[str, ...] = (),
    critical: bool = False,
    protection_level: int = 0xFFFFFFFE,
) -> ProcessObservation:
    """Build one fully identified fake process observation."""
    return ProcessObservation(
        identity=ProcessIdentity(
            pid=pid,
            process_name=name,
            create_time=datetime(2026, 1, 1, 0, 0, create_second, tzinfo=UTC),
            executable_path=path,
            owner_sid=owner_sid,
            username="DESKTOP\\test",
            session_id=session_id,
            parent_pid=100,
        ),
        cpu_percent=2.5,
        memory_rss_bytes=128 * 1024 * 1024,
        window_count=window_count,
        visible_window_count=window_count,
        service_names=service_names,
        is_critical=critical,
        protection_level=protection_level,
    )


class FakeProcessPlatform:
    """In-memory process adapter with explicit outcome and identity controls."""

    def __init__(self, observations: tuple[ProcessObservation, ...]) -> None:
        self.observations = observations
        self.graceful_state = ProcessMemberResultState.EXITED
        self.force_state = ProcessMemberResultState.EXITED
        self.graceful_calls: list[int] = []
        self.force_calls: list[int] = []

    def list_processes(self, max_processes: int) -> tuple[ProcessObservation, ...]:
        return self.observations[:max_processes]

    def inspect_process(self, pid: int) -> ProcessObservation | None:
        return next((item for item in self.observations if item.identity.pid == pid), None)

    def request_graceful_exit(
        self,
        identity: ProcessIdentity,
        timeout_seconds: float,
        cancellation: CancellationSignal,
    ) -> ProcessMemberResult:
        self.graceful_calls.append(identity.pid)
        return ProcessMemberResult(
            identity_digest=identity.canonical_digest(),
            pid=identity.pid,
            state=(
                ProcessMemberResultState.CANCELLED_WAITING
                if cancellation.cancellation_requested()
                else self.graceful_state
            ),
            windows_notified=1,
            message=f"fake graceful result after at most {timeout_seconds} seconds",
        )

    def force_terminate(
        self,
        identity: ProcessIdentity,
        timeout_seconds: float,
    ) -> ProcessMemberResult:
        self.force_calls.append(identity.pid)
        return ProcessMemberResult(
            identity_digest=identity.canonical_digest(),
            pid=identity.pid,
            state=self.force_state,
            message=f"fake force result after at most {timeout_seconds} seconds",
        )
