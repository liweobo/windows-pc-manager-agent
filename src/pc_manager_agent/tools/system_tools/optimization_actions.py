"""Four isolated Stage 4E3 review tools; no business writer is registered here."""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel

from pc_manager_agent.domain.optimization_actions import (
    OptimizationActionPreparationResult,
    OptimizationActionRoute,
    OptimizationRecommendationReference,
    OptimizationSession,
    OptimizationSessionCreateRequest,
    OptimizationSessionReference,
)
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.orchestration.optimization_action_router import OptimizationActionRouter
from pc_manager_agent.orchestration.optimization_session import OptimizationSessionService
from pc_manager_agent.tools.manifest import CancellationToken, ToolManifest
from pc_manager_agent.tools.registry import ToolRegistry


class OptimizationReviewTool:
    """Validate one reference-only request and perform a local review operation."""

    def __init__(
        self,
        name: str,
        request_type: type[BaseModel],
        result_type: type[BaseModel],
        handler: Callable[[BaseModel, CancellationToken], BaseModel],
        *,
        max_batch_size: int = 1,
    ) -> None:
        self._manifest = ToolManifest(
            name=name,
            description="Prepare independent domain review; system changes: zero",
            input_model=request_type,
            output_model=result_type,
            risk_level=RiskLevel.R0,
            required_permissions=("current_user",),
            read_only=True,
            idempotent=False,
            supports_cancellation=True,
            rollback_level=RollbackLevel.NONE,
            preconditions=("local confirmed analysis report", "explicit review selection"),
            postconditions=(
                "no system or user-file changes",
                "domain confirmations remain independent",
            ),
            timeout_seconds=30,
            max_batch_size=max_batch_size,
            audit_fields=("source_report_id", "recommendation_id", "session_id", "route_decision"),
            supported_platforms=("windows",),
        )
        self._handler = handler

    @property
    def manifest(self) -> ToolManifest:
        """Expose immutable R0 metadata, not a dynamic executor name."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Run a fixed preparation handler only after strict schema and cancellation checks."""
        if cancellation.is_cancelled:
            raise PermissionError("REVIEW_CANCELLED")
        validated = self.manifest.input_model.model_validate_json(request.model_dump_json())
        return self._handler(validated, cancellation)


def build_optimization_action_registry(
    router: OptimizationActionRouter,
    sessions: OptimizationSessionService,
) -> ToolRegistry:
    """Construct exactly four R0 tools, separate from E1 and E2 and without a write guard."""
    registry = ToolRegistry()

    def inspect(request: BaseModel, _token: CancellationToken) -> BaseModel:
        return router.inspect(OptimizationRecommendationReference.model_validate(request))

    def prepare(request: BaseModel, token: CancellationToken) -> BaseModel:
        return router.prepare(OptimizationRecommendationReference.model_validate(request), token)

    def create(request: BaseModel, _token: CancellationToken) -> BaseModel:
        return sessions.create(OptimizationSessionCreateRequest.model_validate(request))

    def refresh(request: BaseModel, _token: CancellationToken) -> BaseModel:
        # Refresh the journal only. Live post-action reads have a separate explicit R0 plan.
        return sessions.get(OptimizationSessionReference.model_validate(request).session_id)

    for tool in (
        OptimizationReviewTool(
            "optimization.recommendation.inspect",
            OptimizationRecommendationReference,
            OptimizationActionRoute,
            inspect,
        ),
        OptimizationReviewTool(
            "optimization.recommendation.prepare_action",
            OptimizationRecommendationReference,
            OptimizationActionPreparationResult,
            prepare,
        ),
        OptimizationReviewTool(
            "optimization.session.create",
            OptimizationSessionCreateRequest,
            OptimizationSession,
            create,
            max_batch_size=50,
        ),
        OptimizationReviewTool(
            "optimization.session.refresh",
            OptimizationSessionReference,
            OptimizationSession,
            refresh,
        ),
    ):
        registry.register(tool)
    return registry
