"""Confirmed execution and partial-result assembly for Stage 3 diagnostics."""

from __future__ import annotations

import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import cast
from uuid import UUID

from pydantic import BaseModel

from pc_manager_agent.audit.system_diagnostics import DiagnosticAuditLogger
from pc_manager_agent.confirmation.models import ConfirmationRequest
from pc_manager_agent.confirmation.system_diagnostics import DiagnosticConfirmationService
from pc_manager_agent.domain.system_diagnostics import (
    CollectorError,
    CollectorOutcome,
    CollectorState,
    CpuResult,
    DiagnosticPlan,
    DiagnosticReport,
    DiskResult,
    MemoryResult,
    ProcessResult,
    ServiceResult,
    SoftwareResult,
    StartupResult,
    SystemCollector,
    SystemInfoResult,
    SystemSnapshot,
)
from pc_manager_agent.orchestration.diagnostic_engine import DiagnosticEngine
from pc_manager_agent.orchestration.system_diagnostic_planner import DiagnosticPlanCompiler
from pc_manager_agent.safety.system_diagnostics import (
    DiagnosticSafetyReview,
    DiagnosticSafetyValidator,
)
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class _Collected:
    """Internal pairing of a validated tool output and its audit outcome."""

    result: BaseModel | None
    outcome: CollectorOutcome


class SystemSnapshotService:
    """Execute registered collectors and preserve useful partial results on failures."""

    def __init__(self, registry: ToolRegistry, audit: DiagnosticAuditLogger) -> None:
        self._registry = registry
        self._audit = audit

    def collect(self, plan: DiagnosticPlan, cancellation: CancellationToken) -> SystemSnapshot:
        """Run independent R0 collectors concurrently and preserve deterministic ordering."""
        collected: dict[SystemCollector, _Collected] = {}
        futures: dict[SystemCollector, Future[_Collected]] = {}
        if not cancellation.cancellation_requested():
            with ThreadPoolExecutor(
                max_workers=min(4, len(plan.collectors)),
                thread_name_prefix="diagnostic-r0",
            ) as executor:
                for collector in plan.collectors:
                    self._audit.collector_started(plan, collector.value)
                    futures[collector] = executor.submit(
                        self._collect_one, plan, collector, cancellation
                    )
                for collector in plan.collectors:
                    item = futures[collector].result()
                    self._audit.collector_completed(plan, item.outcome)
                    collected[collector] = item
        outcomes = [collected[item].outcome for item in plan.collectors if item in collected]
        for collector in plan.collectors[len(outcomes) :]:
            outcomes.append(
                CollectorOutcome(
                    collector=collector,
                    state=CollectorState.CANCELLED,
                    error=CollectorError(
                        code="cancelled",
                        message="Collection was cancelled before this collector started",
                    ),
                )
            )
        return self._assemble(collected, tuple(outcomes))

    def _collect_one(
        self,
        plan: DiagnosticPlan,
        collector: SystemCollector,
        cancellation: CancellationToken,
    ) -> _Collected:
        started = time.monotonic()
        if cancellation.cancellation_requested():
            return _Collected(
                result=None,
                outcome=CollectorOutcome(
                    collector=collector,
                    state=CollectorState.CANCELLED,
                    error=CollectorError(
                        code="cancelled",
                        message="Collection was cancelled before this collector executed",
                    ),
                ),
            )
        try:
            result = self._registry.execute(
                collector.value,
                DiagnosticSafetyValidator.arguments_for(plan, collector),
                cancellation,
            )
        except Exception as exc:
            duration = max(0, round((time.monotonic() - started) * 1_000))
            state = (
                CollectorState.CANCELLED
                if cancellation.cancellation_requested()
                else CollectorState.FAILED
            )
            outcome = CollectorOutcome(
                collector=collector,
                state=state,
                duration_ms=duration,
                error=CollectorError(
                    code="collector-failed",
                    message=f"{collector.value} failed: {type(exc).__name__}",
                ),
            )
            return _Collected(result=None, outcome=outcome)
        duration = max(0, round((time.monotonic() - started) * 1_000))
        warnings = tuple(getattr(result, "warnings", ()))
        outcome = CollectorOutcome(
            collector=collector,
            state=CollectorState.PARTIAL if warnings else CollectorState.SUCCEEDED,
            item_count=self._item_count(result),
            duration_ms=duration,
            warnings=warnings,
        )
        return _Collected(result=result, outcome=outcome)

    @staticmethod
    def _item_count(result: BaseModel) -> int:
        if isinstance(result, (SystemInfoResult, CpuResult, MemoryResult)):
            return 1
        if isinstance(result, DiskResult):
            return len(result.snapshots)
        if isinstance(result, ProcessResult):
            return len(result.collection.processes)
        if isinstance(result, StartupResult):
            return len(result.entries)
        if isinstance(result, ServiceResult):
            return len(result.services)
        if isinstance(result, SoftwareResult):
            return len(result.software)
        return 0

    @staticmethod
    def _assemble(
        collected: dict[SystemCollector, _Collected], outcomes: tuple[CollectorOutcome, ...]
    ) -> SystemSnapshot:
        def result(collector: SystemCollector, expected: type[BaseModel]) -> BaseModel | None:
            item = collected.get(collector)
            return item.result if item is not None and isinstance(item.result, expected) else None

        info = cast(SystemInfoResult | None, result(SystemCollector.SYSTEM_INFO, SystemInfoResult))
        cpu = cast(CpuResult | None, result(SystemCollector.CPU, CpuResult))
        memory = cast(MemoryResult | None, result(SystemCollector.MEMORY, MemoryResult))
        disks = cast(DiskResult | None, result(SystemCollector.DISKS, DiskResult))
        processes = cast(ProcessResult | None, result(SystemCollector.PROCESSES, ProcessResult))
        startup = cast(StartupResult | None, result(SystemCollector.STARTUP, StartupResult))
        services = cast(ServiceResult | None, result(SystemCollector.SERVICES, ServiceResult))
        software = cast(SoftwareResult | None, result(SystemCollector.SOFTWARE, SoftwareResult))
        return SystemSnapshot(
            system_info=info.snapshot if info else None,
            cpu=cpu.snapshot if cpu else None,
            memory=memory.snapshot if memory else None,
            disks=disks.snapshots if disks else (),
            processes=processes.collection if processes else None,
            startup_entries=startup.entries if startup else (),
            services=services.services if services else (),
            software=software.software if software else (),
            outcomes=outcomes,
        )


class DiagnosticOrchestrator:
    """Coordinate planning, safety, confirmation, tools, analysis, and audit."""

    def __init__(
        self,
        compiler: DiagnosticPlanCompiler,
        safety: DiagnosticSafetyValidator,
        confirmation: DiagnosticConfirmationService,
        snapshot_service: SystemSnapshotService,
        engine: DiagnosticEngine,
        audit: DiagnosticAuditLogger,
    ) -> None:
        self._compiler = compiler
        self._safety = safety
        self._confirmation = confirmation
        self._snapshot_service = snapshot_service
        self._engine = engine
        self._audit = audit

    def prepare(self, user_goal: str) -> tuple[DiagnosticPlan, DiagnosticSafetyReview]:
        """Compile a local plan, independently review it, and record the decision."""
        plan = self._compiler.compile(user_goal, self._compiler.local_draft(user_goal))
        review = self._safety.review(plan)
        self._audit.plan_reviewed(
            plan, review.approved, tuple(issue.message for issue in review.issues)
        )
        return plan, review

    def request_confirmation(self, plan: DiagnosticPlan) -> ConfirmationRequest:
        """Request an exact, expiring plan confirmation after a fresh safety review."""
        review = self._safety.review(plan)
        if not review.approved:
            raise ValueError("Diagnostic plan did not pass safety review")
        return self._confirmation.request(plan)

    def resolve_confirmation(
        self, confirmation_id: UUID, approved: bool, plan: DiagnosticPlan
    ) -> ConfirmationRequest:
        """Resolve the exact plan confirmation and audit the user's decision."""
        resolved = self._confirmation.resolve(confirmation_id, approved, plan)
        self._audit.confirmation_resolved(plan, approved)
        return resolved

    def execute(
        self, plan: DiagnosticPlan, cancellation: CancellationToken | None = None
    ) -> DiagnosticReport:
        """Re-review and execute only after exact approval; partial failures remain visible."""
        review = self._safety.review(plan)
        if not review.approved:
            raise ValueError("Diagnostic plan changed or failed the execution-time safety review")
        self._confirmation.require_approved(plan)
        token = cancellation or CancellationToken()
        snapshot = self._snapshot_service.collect(plan, token)
        report = self._engine.analyze(plan, snapshot)
        self._audit.report_completed(plan, report)
        return report
