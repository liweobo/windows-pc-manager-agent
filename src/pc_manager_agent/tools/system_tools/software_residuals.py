"""Exact report-only tool allow-list for Stage 4D3 software residual analysis."""

from __future__ import annotations

from pydantic import BaseModel

from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.software_residuals import (
    ResidualAnalyzeRequest,
    ResidualAnalyzeResult,
    ResidualInspectRequest,
    ResidualInspectResult,
    ResidualReportRequest,
    ResidualReportResult,
)
from pc_manager_agent.orchestration.software_residual_analysis import ResidualAnalyzer
from pc_manager_agent.persistence.software_residuals import SoftwareResidualRepository
from pc_manager_agent.tools.manifest import CancellationToken, ToolManifest


def _manifest(
    name: str,
    description: str,
    input_model: type[BaseModel],
    output_model: type[BaseModel],
) -> ToolManifest:
    """Build one R0 manifest with an explicit zero-mutation postcondition."""
    return ToolManifest(
        name=name,
        description=description,
        input_model=input_model,
        output_model=output_model,
        risk_level=RiskLevel.R0,
        required_permissions=("current-user-metadata-read",),
        read_only=True,
        idempotent=False,
        supports_cancellation=True,
        rollback_level=RollbackLevel.NONE,
        preconditions=(
            "exact Stage 4D3 plan is confirmed",
            "Agent uninstall context is eligible and unchanged",
            "audit and residual stores are available",
        ),
        postconditions=(
            "deletion_performed is false",
            "no file content, registry value, link target, or reparse target is read",
            "no file, directory, registry value, process, service, or package is modified",
        ),
        timeout_seconds=600.0,
        max_batch_size=1,
        audit_fields=(
            "context digest",
            "root count",
            "candidate counts",
            "classification counts",
            "protection counts",
            "deletion_performed=false",
        ),
        supported_platforms=("windows",),
        requires_confirmation=True,
        requires_runtime_confirmation=False,
        supports_preview=False,
        irreversible=False,
    )


class SoftwareResidualAnalyzeTool:
    """Run bounded metadata collectors for one exact uninstall context."""

    def __init__(self, analyzer: ResidualAnalyzer) -> None:
        self._analyzer = analyzer
        self._manifest = _manifest(
            "software.residuals.analyze",
            "Analyze metadata under exact pre-uninstall paths without cleanup",
            ResidualAnalyzeRequest,
            ResidualAnalyzeResult,
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return the immutable R0 manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Return a persisted report with deletion_performed fixed to false."""
        if not isinstance(request, ResidualAnalyzeRequest):
            raise TypeError("SoftwareResidualAnalyzeTool received an unexpected input model")
        return ResidualAnalyzeResult(report=self._analyzer.analyze(request, cancellation))


class SoftwareResidualReportTool:
    """Read the latest local report for one exact uninstall context."""

    def __init__(self, repository: SoftwareResidualRepository) -> None:
        self._repository = repository
        self._manifest = _manifest(
            "software.residuals.report",
            "Read the latest local residual report without filesystem access",
            ResidualReportRequest,
            ResidualReportResult,
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return the immutable R0 manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Return an existing report or explicit absence; cancellation causes no mutation."""
        if not isinstance(request, ResidualReportRequest):
            raise TypeError("SoftwareResidualReportTool received an unexpected input model")
        if cancellation.cancellation_requested():
            return ResidualReportResult(report=None)
        return ResidualReportResult(report=self._repository.latest_report(request.context_id))


class SoftwareResidualInspectTool:
    """Read one exact candidate that belongs to the requested context."""

    def __init__(self, repository: SoftwareResidualRepository) -> None:
        self._repository = repository
        self._manifest = _manifest(
            "software.residuals.inspect",
            "Read one persisted metadata-only residual candidate",
            ResidualInspectRequest,
            ResidualInspectResult,
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return the immutable R0 manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Return one context-bound candidate; never accept or execute a path."""
        if not isinstance(request, ResidualInspectRequest):
            raise TypeError("SoftwareResidualInspectTool received an unexpected input model")
        if cancellation.cancellation_requested():
            return ResidualInspectResult(candidate=None)
        return ResidualInspectResult(
            candidate=self._repository.get_candidate(request.context_id, request.candidate_id)
        )
