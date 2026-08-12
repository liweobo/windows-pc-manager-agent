"""Digest-bound plan confirmation for Stage 3 read-only diagnostics."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pc_manager_agent.confirmation.models import (
    ConfirmationKind,
    ConfirmationRequest,
    ConfirmationState,
)
from pc_manager_agent.domain.system_diagnostics import DiagnosticPlan


class DiagnosticConfirmationError(RuntimeError):
    """Raised when diagnostic approval is absent, expired, reused, or stale."""


class DiagnosticConfirmationService:
    """Bind one plan approval to its ID, canonical digest, summary, and expiry."""

    def __init__(
        self,
        ttl_seconds: int = 300,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._now = now or (lambda: datetime.now(UTC))
        self._requests: dict[UUID, ConfirmationRequest] = {}
        self._approved: dict[UUID, tuple[str, datetime]] = {}

    def request(self, plan: DiagnosticPlan) -> ConfirmationRequest:
        """Create an expiring confirmation for the exact collector set and limits."""
        summary = (
            f"Run {len(plan.collectors)} read-only collectors: "
            + ", ".join(item.value for item in plan.collectors)
            + ". System changes: 0; administrator permission: not requested."
        )
        request = ConfirmationRequest(
            kind=ConfirmationKind.PLAN,
            plan_id=plan.plan_id,
            plan_digest=plan.canonical_digest(),
            object_summary=summary,
            expires_at=self._now() + timedelta(seconds=self._ttl_seconds),
        )
        self._requests[request.confirmation_id] = request
        return request

    def resolve(
        self, confirmation_id: UUID, approved: bool, plan: DiagnosticPlan
    ) -> ConfirmationRequest:
        """Record one decision only after verifying plan identity, digest, and expiry."""
        try:
            request = self._requests[confirmation_id]
        except KeyError as exc:
            raise DiagnosticConfirmationError("Unknown diagnostic confirmation") from exc
        if request.state is not ConfirmationState.PENDING:
            raise DiagnosticConfirmationError("Diagnostic confirmation was already resolved")
        if self._now() >= request.expires_at:
            self._requests[confirmation_id] = request.model_copy(
                update={"state": ConfirmationState.EXPIRED}
            )
            raise DiagnosticConfirmationError("Diagnostic confirmation expired")
        digest = plan.canonical_digest()
        if request.plan_id != plan.plan_id or request.plan_digest != digest:
            raise DiagnosticConfirmationError("Diagnostic plan changed after confirmation request")
        state = ConfirmationState.APPROVED if approved else ConfirmationState.REJECTED
        resolved = request.model_copy(update={"state": state})
        self._requests[confirmation_id] = resolved
        if approved:
            self._approved[plan.plan_id] = (digest, request.expires_at)
        return resolved

    def require_approved(self, plan: DiagnosticPlan) -> None:
        """Fail closed unless the exact current plan has an active approval binding."""
        binding = self._approved.get(plan.plan_id)
        if binding is None or binding[0] != plan.canonical_digest() or self._now() >= binding[1]:
            raise DiagnosticConfirmationError("Diagnostic plan is not confirmed or has changed")
