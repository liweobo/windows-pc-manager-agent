from __future__ import annotations

from pc_manager_agent.domain.system_diagnostics import (
    CollectorOutcome,
    CollectorState,
    DiagnosticIntent,
    DiagnosticPlan,
    DiagnosticThresholds,
    DiskKind,
    DiskSnapshot,
    SystemCollector,
    SystemSnapshot,
)
from pc_manager_agent.orchestration.diagnostic_engine import DiagnosticEngine
from pc_manager_agent.tools.manifest import CancellationToken
from tests.fixtures.system_diagnostics import FakeSystemPlatform


def test_engine_uses_multi_sample_thresholds_and_never_proposes_execution() -> None:
    platform = FakeSystemPlatform()
    token = CancellationToken()
    snapshot = SystemSnapshot(
        system_info=platform.collect_system_info(),
        cpu=platform.collect_cpu(3, 0.1, token),
        memory=platform.collect_memory(),
        disks=platform.collect_disks()[0],
        processes=platform.collect_processes(0.1, 10, token)[0],
        startup_entries=tuple(platform.collect_startup(10)[0] * 20),
        outcomes=(
            CollectorOutcome(
                collector=SystemCollector.SYSTEM_INFO,
                state=CollectorState.SUCCEEDED,
                item_count=1,
            ),
        ),
    )
    plan = DiagnosticPlan(
        summary="overview",
        user_goal="overview",
        intent=DiagnosticIntent.OVERVIEW,
        collectors=tuple(SystemCollector),
    )
    report = DiagnosticEngine().analyze(plan, snapshot)
    codes = {finding.code for finding in report.findings}
    assert codes == {
        "cpu.sustained-utilization",
        "memory.high-utilization",
        "disk.capacity-utilization",
        "process.resource-observation",
        "startup.entry-count",
    }
    assert all(not action.executable for finding in report.findings for action in finding.actions)
    assert report.thresholds.cpu_notice_percent == 75
    assert "not a malware" in report.disclaimer


def test_engine_does_not_claim_pressure_when_data_is_below_thresholds() -> None:
    plan = DiagnosticPlan(
        summary="overview",
        user_goal="overview",
        intent=DiagnosticIntent.OVERVIEW,
        collectors=(SystemCollector.SYSTEM_INFO,),
    )
    platform = FakeSystemPlatform()
    quiet_memory = platform.collect_memory().model_copy(update={"used_percent": 20})
    quiet_cpu = platform.collect_cpu(3, 0.1, CancellationToken()).model_copy(
        update={"average_percent": 10, "peak_percent": 20}
    )
    roomy_disk = DiskSnapshot(
        device="D:",
        mountpoint="D:/",
        filesystem="NTFS",
        kind=DiskKind.FIXED,
        total_bytes=4 * 1024**4,
        used_bytes=3_700 * 1024**3,
        free_bytes=396 * 1024**3,
        used_percent=90,
    )
    snapshot = SystemSnapshot(
        cpu=quiet_cpu,
        memory=quiet_memory,
        disks=(roomy_disk,),
        processes=platform.collect_processes(0.1, 10, CancellationToken())[0].model_copy(
            update={"processes": ()}
        ),
        outcomes=(),
    )
    assert DiagnosticEngine().analyze(plan, snapshot).findings == ()


def test_engine_warning_boundaries_are_explicit() -> None:
    platform = FakeSystemPlatform()
    plan = DiagnosticPlan(
        summary="overview",
        user_goal="overview",
        intent=DiagnosticIntent.OVERVIEW,
        collectors=(SystemCollector.SYSTEM_INFO,),
    )
    cpu = platform.collect_cpu(3, 0.1, CancellationToken()).model_copy(
        update={"average_percent": 95, "peak_percent": 100}
    )
    memory = platform.collect_memory().model_copy(update={"used_percent": 96})
    critical_disk = platform.collect_disks()[0][0].model_copy(
        update={"used_percent": 97, "free_bytes": 1}
    )
    report = DiagnosticEngine(DiagnosticThresholds()).analyze(
        plan,
        SystemSnapshot(cpu=cpu, memory=memory, disks=(critical_disk,), outcomes=()),
    )
    assert {finding.severity.value for finding in report.findings} == {"warning"}
