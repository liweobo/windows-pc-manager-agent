"""Digest-bound plan confirmation for read-only Stage 4E1 analysis."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pc_manager_agent.confirmation.models import (
    ConfirmationKind,
    ConfirmationRequest,
    ConfirmationState,
)
from pc_manager_agent.domain.system_optimization import OptimizationPlan


class OptimizationConfirmationError(RuntimeError):
    """Raised for missing, stale, expired, or reused optimization approval."""


class OptimizationConfirmationService:
    """Bind approval to the exact Stage 4E1 plan digest and expiry."""

    def __init__(self, ttl_seconds: int = 300, now: Callable[[], datetime] | None = None) -> None:
        self._ttl_seconds = ttl_seconds
        self._now = now or (lambda: datetime.now(UTC))
        self._requests: dict[UUID, ConfirmationRequest] = {}
        self._approved: dict[UUID, tuple[str, datetime]] = {}

    def request(self, plan: OptimizationPlan) -> ConfirmationRequest:
        """Create one confirmation that explicitly states the zero-change impact."""
        request = ConfirmationRequest(
            kind=ConfirmationKind.PLAN,
            plan_id=plan.plan_id,
            plan_digest=plan.canonical_digest(),
            object_summary=(
                f"运行 {len(plan.tools)} 个 R0 只读分析工具；授权个人目录 "
                f"{len(plan.authorized_roots)} 个；系统修改 0；不请求管理员权限。"
            ),
            expires_at=self._now() + timedelta(seconds=self._ttl_seconds),
        )
        self._requests[request.confirmation_id] = request
        return request

    def resolve(
        self, confirmation_id: UUID, approved: bool, plan: OptimizationPlan
    ) -> ConfirmationRequest:
        """Resolve once after verifying ID, digest, and expiry."""
        request = self._requests.get(confirmation_id)
        if request is None:
            raise OptimizationConfirmationError("Unknown optimization confirmation")
        if request.state is not ConfirmationState.PENDING:
            raise OptimizationConfirmationError("Optimization confirmation was already resolved")
        if self._now() >= request.expires_at:
            self._requests[confirmation_id] = request.model_copy(
                update={"state": ConfirmationState.EXPIRED}
            )
            raise OptimizationConfirmationError("Optimization confirmation expired")
        digest = plan.canonical_digest()
        if request.plan_id != plan.plan_id or request.plan_digest != digest:
            raise OptimizationConfirmationError("Optimization plan changed")
        state = ConfirmationState.APPROVED if approved else ConfirmationState.REJECTED
        resolved = request.model_copy(update={"state": state})
        self._requests[confirmation_id] = resolved
        if approved:
            self._approved[plan.plan_id] = (digest, request.expires_at)
        return resolved

    def require_approved(self, plan: OptimizationPlan) -> None:
        """Require an unexpired binding to the current canonical digest."""
        binding = self._approved.get(plan.plan_id)
        if binding is None or binding[0] != plan.canonical_digest() or self._now() >= binding[1]:
            raise OptimizationConfirmationError("Optimization plan is not confirmed or changed")
