"""Review-confirm-execute-verify-audit lifecycle for Stage 1 analysis."""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, JsonValue

from pc_manager_agent import __version__
from pc_manager_agent.audit.models import AuditEvent
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.confirmation.models import ConfirmationRequest
from pc_manager_agent.confirmation.state_machine import ConfirmationService
from pc_manager_agent.domain.file_analysis import (
    DuplicateFileAnalysisResult,
    FileAnalysisPlan,
    FileAnalysisReport,
    FileAnalysisSummary,
    InactiveFileAnalysisResult,
    LargeFileAnalysisResult,
)
from pc_manager_agent.domain.reports import ScanReport, ScanStatus, ScanSummary
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.persistence.analysis_results import AnalysisResultRepository
from pc_manager_agent.safety.file_analysis_validator import FileAnalysisSafetyValidator
from pc_manager_agent.safety.plan_reviewer import SafetyReview
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.tools.registry import ToolRegistry


class FileAnalysisOrchestrationError(RuntimeError):
    """Raised when an analysis gate, result type, or postcondition fails."""


class FileAnalysisOrchestrator:
    """Own the deterministic execution boundary for one compiled analysis plan."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        validator: FileAnalysisSafetyValidator,
        confirmation: ConfirmationService,
        audit: AuditRepository,
        results: AnalysisResultRepository,
    ) -> None:
        self._registry = registry
        self._validator = validator
        self._confirmation = confirmation
        self._audit = audit
        self._results = results
        self._git_commit = os.getenv("PC_MANAGER_GIT_COMMIT")

    def review(
        self,
        plan: FileAnalysisPlan,
        *,
        provider: str | None = None,
        provider_request_id: str | None = None,
    ) -> SafetyReview:
        """Review and audit a compiled plan without executing a tool."""
        review = self._validator.review(plan)
        task = plan.task_plan
        self._audit.record(
            AuditEvent(
                event_type="file_analysis.plan.reviewed",
                original_request=task.user_goal,
                plan=plan.model_dump(mode="json"),
                plan_id=str(task.plan_id),
                plan_version=task.plan_version,
                agent_decision="approved" if review.approved else "denied",
                parameters={"analysis_session_id": str(plan.analysis_session_id)},
                risk_level=RiskLevel.R0,
                confirmation_required=True,
                verification=review.model_dump(mode="json"),
                model_provider=provider,
                model_request_id=provider_request_id,
                app_version=__version__,
                git_commit=self._git_commit,
            )
        )
        return review

    def request_plan_confirmation(self, plan: FileAnalysisPlan) -> ConfirmationRequest:
        """Issue a digest-bound confirmation after an independent successful review."""
        review = self._validator.review(plan)
        if not review.approved:
            raise FileAnalysisOrchestrationError("Safety review denied the analysis plan")
        roots = "、".join(str(path) for path in plan.task_plan.scope.included_paths)
        analyses = "、".join(item.value for item in plan.analyses)
        return self._confirmation.request_plan(
            plan.task_plan,
            (
                f"只读扫描：{roots}；分析：{analyses}；"
                f"大小阈值 {plan.filters.minimum_size_bytes} 字节；"
                f"闲置阈值 {plan.filters.inactive_days} 天。"
                "不会移动、重命名或删除文件。"
            ),
        )

    def resolve_plan_confirmation(
        self,
        confirmation_id: UUID,
        approved: bool,
        plan: FileAnalysisPlan,
    ) -> ConfirmationRequest:
        """Resolve and audit the plan decision against the exact current digest."""
        resolved = self._confirmation.resolve(
            confirmation_id,
            approved,
            plan.task_plan,
        )
        self._audit.record(
            AuditEvent(
                event_type="file_analysis.confirmation.resolved",
                plan_id=str(plan.task_plan.plan_id),
                plan_version=plan.task_plan.plan_version,
                parameters={"analysis_session_id": str(plan.analysis_session_id)},
                risk_level=RiskLevel.R0,
                confirmation_required=True,
                confirmation_result=resolved.state.value,
                app_version=__version__,
                git_commit=self._git_commit,
            )
        )
        return resolved

    def execute(
        self,
        plan: FileAnalysisPlan,
        cancellation: CancellationToken,
    ) -> FileAnalysisReport:
        """Execute only registered steps after re-review and exact confirmation."""
        review = self._validator.review(plan)
        if not review.approved:
            raise FileAnalysisOrchestrationError("Safety review denied execution")
        self._confirmation.require_plan_approved(plan.task_plan)
        started_at = datetime.now(UTC)
        started_clock = time.monotonic()
        roots = plan.task_plan.scope.included_paths
        self._results.create_session(plan.analysis_session_id, roots)
        scan_summaries: list[ScanSummary] = []
        duplicate_results: list[DuplicateFileAnalysisResult] = []
        analyzer_executed = False

        try:
            for step in plan.task_plan.steps:
                if cancellation.cancellation_requested():
                    break
                step_started = time.monotonic()
                self._record_tool_started(plan, step.step_id, step.tool_name, step.arguments)
                try:
                    result = self._registry.execute(
                        step.tool_name,
                        step.arguments,
                        cancellation,
                    )
                    self._validate_step_result(step.tool_name, result)
                except Exception as exc:
                    self._record_tool_failed(
                        plan,
                        step.step_id,
                        step.tool_name,
                        step.arguments,
                        exc,
                        max(0, round((time.monotonic() - step_started) * 1_000)),
                    )
                    raise
                if isinstance(result, ScanReport):
                    scan_summaries.append(result.summary)
                else:
                    analyzer_executed = True
                if isinstance(result, DuplicateFileAnalysisResult):
                    duplicate_results.append(result)
                self._record_tool_completed(
                    plan,
                    step.step_id,
                    step.tool_name,
                    step.arguments,
                    result,
                    max(0, round((time.monotonic() - step_started) * 1_000)),
                )
                if self._result_cancelled(result):
                    break
        except Exception as exc:
            failed_summary = self._aggregate_scan_summaries(
                scan_summaries,
                forced_status=ScanStatus.FAILED,
                duration_ms=max(0, round((time.monotonic() - started_clock) * 1_000)),
            )
            self._results.complete_scan(plan.analysis_session_id, failed_summary)
            self._audit.record(
                AuditEvent(
                    event_type="file_analysis.failed",
                    original_request=plan.task_plan.user_goal,
                    plan_id=str(plan.task_plan.plan_id),
                    plan_version=plan.task_plan.plan_version,
                    parameters={"analysis_session_id": str(plan.analysis_session_id)},
                    risk_level=RiskLevel.R0,
                    confirmation_required=True,
                    confirmation_result="APPROVED",
                    error={"type": type(exc).__name__, "message": str(exc)},
                    app_version=__version__,
                    git_commit=self._git_commit,
                    duration_ms=failed_summary.duration_ms,
                )
            )
            raise

        forced_status = ScanStatus.CANCELLED if cancellation.is_cancelled else None
        scan_summary = self._aggregate_scan_summaries(
            scan_summaries,
            forced_status=forced_status,
            duration_ms=max(0, round((time.monotonic() - started_clock) * 1_000)),
        )
        self._results.complete_scan(plan.analysis_session_id, scan_summary)
        if analyzer_executed:
            self._results.finalize_matches(
                plan.analysis_session_id,
                plan.analyses,
                plan.match_mode,
            )
        matching_files, matching_bytes = self._results.matching_totals(plan.analysis_session_id)
        categories = self._results.category_summaries(plan.analysis_session_id)
        issues = self._results.list_issues(plan.analysis_session_id)
        summary = FileAnalysisSummary(
            files_scanned=scan_summary.files_seen,
            directories_scanned=scan_summary.directories_seen,
            total_bytes=scan_summary.total_size_bytes,
            matching_files=matching_files,
            matching_bytes=matching_bytes,
            errors=scan_summary.issues,
            status=scan_summary.status,
            categories=categories,
        )
        report = FileAnalysisReport(
            analysis_session_id=plan.analysis_session_id,
            plan_id=plan.task_plan.plan_id,
            started_at=started_at,
            summary=summary,
            duplicate_groups=tuple(
                group for result in duplicate_results for group in result.groups
            ),
            issues=issues,
        )
        self._verify_report(plan, report)
        self._audit.record(
            AuditEvent(
                event_type="file_analysis.completed",
                original_request=plan.task_plan.user_goal,
                plan_id=str(plan.task_plan.plan_id),
                plan_version=plan.task_plan.plan_version,
                parameters={
                    "analysis_session_id": str(plan.analysis_session_id),
                    "scan_roots": [str(path) for path in roots],
                    "filters": plan.filters.model_dump(mode="json"),
                    "analysis_types": [item.value for item in plan.analyses],
                },
                risk_level=RiskLevel.R0,
                confirmation_required=True,
                confirmation_result="APPROVED",
                result={"summary": summary.model_dump(mode="json")},
                verification={"passed": True, "read_only": True},
                app_version=__version__,
                git_commit=self._git_commit,
                duration_ms=scan_summary.duration_ms,
            )
        )
        return report

    def _record_tool_started(
        self,
        plan: FileAnalysisPlan,
        step_id: str,
        tool_name: str,
        arguments: Mapping[str, JsonValue],
    ) -> None:
        self._audit.record(
            AuditEvent(
                event_type="file_analysis.tool.started",
                plan_id=str(plan.task_plan.plan_id),
                plan_version=plan.task_plan.plan_version,
                step_id=step_id,
                tool_name=tool_name,
                parameters=dict(arguments),
                risk_level=RiskLevel.R0,
                confirmation_required=True,
                confirmation_result="APPROVED",
                app_version=__version__,
                git_commit=self._git_commit,
            )
        )

    def _record_tool_completed(
        self,
        plan: FileAnalysisPlan,
        step_id: str,
        tool_name: str,
        arguments: Mapping[str, JsonValue],
        result: BaseModel,
        duration_ms: int,
    ) -> None:
        self._audit.record(
            AuditEvent(
                event_type="file_analysis.tool.completed",
                plan_id=str(plan.task_plan.plan_id),
                plan_version=plan.task_plan.plan_version,
                step_id=step_id,
                tool_name=tool_name,
                parameters=dict(arguments),
                risk_level=RiskLevel.R0,
                confirmation_required=True,
                confirmation_result="APPROVED",
                result=self._safe_tool_result(result),
                verification={"passed": True},
                app_version=__version__,
                git_commit=self._git_commit,
                duration_ms=duration_ms,
            )
        )

    def _record_tool_failed(
        self,
        plan: FileAnalysisPlan,
        step_id: str,
        tool_name: str,
        arguments: Mapping[str, JsonValue],
        error: Exception,
        duration_ms: int,
    ) -> None:
        """Audit the exact registered step that failed before aborting the task."""
        self._audit.record(
            AuditEvent(
                event_type="file_analysis.tool.failed",
                plan_id=str(plan.task_plan.plan_id),
                plan_version=plan.task_plan.plan_version,
                step_id=step_id,
                tool_name=tool_name,
                parameters=dict(arguments),
                risk_level=RiskLevel.R0,
                confirmation_required=True,
                confirmation_result="APPROVED",
                error={"type": type(error).__name__, "message": str(error)},
                verification={"passed": False},
                app_version=__version__,
                git_commit=self._git_commit,
                duration_ms=duration_ms,
            )
        )

    @staticmethod
    def _validate_step_result(tool_name: str, result: BaseModel) -> None:
        expected: dict[str, type[BaseModel]] = {
            "file.scan": ScanReport,
            "file.analyze.large": LargeFileAnalysisResult,
            "file.analyze.inactive": InactiveFileAnalysisResult,
            "file.analyze.duplicates": DuplicateFileAnalysisResult,
        }
        if not isinstance(result, expected[tool_name]):
            raise FileAnalysisOrchestrationError(
                f"Tool {tool_name} returned an unexpected result type"
            )

    @staticmethod
    def _result_cancelled(result: BaseModel) -> bool:
        if isinstance(result, ScanReport):
            return result.summary.cancelled
        return bool(getattr(result, "cancelled", False))

    @staticmethod
    def _safe_tool_result(result: BaseModel) -> dict[str, object]:
        if isinstance(result, ScanReport):
            return {"summary": result.summary.model_dump(mode="json")}
        if isinstance(result, LargeFileAnalysisResult):
            return {"files": result.files, "total_bytes": result.total_bytes}
        if isinstance(result, InactiveFileAnalysisResult):
            return {
                "files": result.files,
                "total_bytes": result.total_bytes,
                "by_confidence": result.by_confidence,
            }
        if isinstance(result, DuplicateFileAnalysisResult):
            return {
                "groups": len(result.groups),
                "files": result.files,
                "reclaimable_bytes": result.reclaimable_bytes,
                "issues": len(result.issues),
            }
        return {}

    @staticmethod
    def _aggregate_scan_summaries(
        summaries: list[ScanSummary],
        *,
        forced_status: ScanStatus | None,
        duration_ms: int,
    ) -> ScanSummary:
        status = forced_status or ScanStatus.COMPLETED
        if forced_status is None:
            statuses = {summary.status for summary in summaries}
            for candidate in (
                ScanStatus.CANCELLED,
                ScanStatus.TIMED_OUT,
                ScanStatus.TRUNCATED,
            ):
                if candidate in statuses:
                    status = candidate
                    break
        return ScanSummary(
            files_seen=sum(item.files_seen for item in summaries),
            directories_seen=sum(item.directories_seen for item in summaries),
            total_size_bytes=sum(item.total_size_bytes for item in summaries),
            issues=sum(item.issues for item in summaries),
            cancelled=status is ScanStatus.CANCELLED,
            timed_out=status is ScanStatus.TIMED_OUT,
            truncated=status is ScanStatus.TRUNCATED,
            duration_ms=duration_ms,
            status=status,
        )

    @staticmethod
    def _verify_report(plan: FileAnalysisPlan, report: FileAnalysisReport) -> None:
        if report.analysis_session_id != plan.analysis_session_id:
            raise FileAnalysisOrchestrationError("Report session does not match the plan")
        if report.plan_id != plan.task_plan.plan_id:
            raise FileAnalysisOrchestrationError("Report plan ID does not match")
        if report.summary.matching_files > report.summary.files_scanned:
            raise FileAnalysisOrchestrationError("Candidate count exceeds scanned files")
