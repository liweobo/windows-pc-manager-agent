"""Plan confirmation and non-authorizing target acknowledgement for Stage 4D1."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pc_manager_agent.confirmation.models import (
    ConfirmationKind,
    ConfirmationRequest,
    ConfirmationState,
)
from pc_manager_agent.domain.software_errors import (
    SoftwareAnalysisError,
    SoftwareAnalysisErrorCode,
)
from pc_manager_agent.domain.software_uninstall_analysis import (
    SoftwareTargetAcknowledgement,
    SoftwareUninstallAnalysisPlan,
    SoftwareUninstallPreview,
    TargetAcknowledgementState,
)


class SoftwareAnalysisConfirmationService:
    """Bind an R0 plan and later target understanding without execution authorization."""

    def __init__(
        self,
        plan_ttl_seconds: int = 300,
        acknowledgement_ttl_seconds: int = 300,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._plan_ttl_seconds = plan_ttl_seconds
        self._acknowledgement_ttl_seconds = acknowledgement_ttl_seconds
        self._now = now or (lambda: datetime.now(UTC))
        self._plans: dict[UUID, ConfirmationRequest] = {}
        self._approved_plans: dict[UUID, tuple[str, datetime]] = {}
        self._acknowledgements: dict[UUID, SoftwareTargetAcknowledgement] = {}

    def request_plan(self, plan: SoftwareUninstallAnalysisPlan) -> ConfirmationRequest:
        """Create an expiring confirmation for the exact read-only analysis plan."""
        request = ConfirmationRequest(
            kind=ConfirmationKind.PLAN,
            plan_id=plan.plan_id,
            plan_digest=plan.canonical_digest(),
            object_summary=(
                "Refresh installed-software metadata, resolve one target, and prepare a read-only "
                "uninstall-impact Preview. System changes: 0; uninstall execution: unavailable."
            ),
            expires_at=self._now() + timedelta(seconds=self._plan_ttl_seconds),
        )
        self._plans[request.confirmation_id] = request
        return request

    def resolve_plan(
        self,
        confirmation_id: UUID,
        approved: bool,
        plan: SoftwareUninstallAnalysisPlan,
    ) -> ConfirmationRequest:
        """Resolve one decision after checking exact plan ID, digest, state, and expiry."""
        try:
            request = self._plans[confirmation_id]
        except KeyError as exc:
            raise SoftwareAnalysisError(
                SoftwareAnalysisErrorCode.TARGET_CHANGED,
                "Unknown Stage 4D1 plan confirmation",
            ) from exc
        if request.state is not ConfirmationState.PENDING:
            raise SoftwareAnalysisError(
                SoftwareAnalysisErrorCode.TARGET_CHANGED,
                "Stage 4D1 plan confirmation was already resolved",
            )
        if self._now() >= request.expires_at:
            self._plans[confirmation_id] = request.model_copy(
                update={"state": ConfirmationState.EXPIRED}
            )
            raise SoftwareAnalysisError(
                SoftwareAnalysisErrorCode.PREVIEW_EXPIRED,
                "Stage 4D1 plan confirmation expired",
            )
        digest = plan.canonical_digest()
        if request.plan_id != plan.plan_id or request.plan_digest != digest:
            raise SoftwareAnalysisError(
                SoftwareAnalysisErrorCode.TARGET_CHANGED,
                "Stage 4D1 plan changed after confirmation was requested",
            )
        state = ConfirmationState.APPROVED if approved else ConfirmationState.REJECTED
        resolved = request.model_copy(update={"state": state})
        self._plans[confirmation_id] = resolved
        if approved:
            self._approved_plans[plan.plan_id] = (digest, request.expires_at)
        return resolved

    def require_plan_approved(self, plan: SoftwareUninstallAnalysisPlan) -> None:
        """Fail unless the current exact plan has an unexpired R0 approval."""
        binding = self._approved_plans.get(plan.plan_id)
        if binding is None or binding[0] != plan.canonical_digest() or self._now() >= binding[1]:
            raise SoftwareAnalysisError(
                SoftwareAnalysisErrorCode.TARGET_CHANGED,
                "Stage 4D1 analysis plan is not confirmed or has changed",
            )

    def request_acknowledgement(
        self,
        plan: SoftwareUninstallAnalysisPlan,
        preview: SoftwareUninstallPreview,
    ) -> SoftwareTargetAcknowledgement:
        """Request target understanding; this object has no executable capability or tool data."""
        self.require_plan_approved(plan)
        if preview.plan_id != plan.plan_id or preview.plan_digest != plan.canonical_digest():
            raise SoftwareAnalysisError(
                SoftwareAnalysisErrorCode.TARGET_CHANGED,
                "Preview no longer matches its confirmed plan",
            )
        now = self._now()
        if now >= preview.expires_at:
            raise SoftwareAnalysisError(
                SoftwareAnalysisErrorCode.PREVIEW_EXPIRED,
                "Software Preview expired; refresh before acknowledging the target",
            )
        acknowledgement = SoftwareTargetAcknowledgement(
            preview_id=preview.preview_id,
            preview_digest=preview.canonical_digest(),
            identity_digest=preview.identity_digest,
            object_summary=(
                f"I understand the selected target is {preview.target.display_name} "
                f"({preview.target.display_version or 'version unavailable'}), and that Stage 4D1 "
                "will stop without uninstalling it."
            ),
            expires_at=min(
                preview.expires_at,
                now + timedelta(seconds=self._acknowledgement_ttl_seconds),
            ),
        )
        self._acknowledgements[acknowledgement.acknowledgement_id] = acknowledgement
        return acknowledgement

    def resolve_acknowledgement(
        self,
        acknowledgement_id: UUID,
        acknowledged: bool,
        preview: SoftwareUninstallPreview,
    ) -> SoftwareTargetAcknowledgement:
        """Record understanding or rejection and stop; no approval binding is produced."""
        try:
            request = self._acknowledgements[acknowledgement_id]
        except KeyError as exc:
            raise SoftwareAnalysisError(
                SoftwareAnalysisErrorCode.TARGET_CHANGED,
                "Unknown target acknowledgement",
            ) from exc
        if request.state is not TargetAcknowledgementState.PENDING:
            raise SoftwareAnalysisError(
                SoftwareAnalysisErrorCode.TARGET_CHANGED,
                "Target acknowledgement was already resolved",
            )
        if self._now() >= request.expires_at or self._now() >= preview.expires_at:
            expired = request.model_copy(update={"state": TargetAcknowledgementState.EXPIRED})
            self._acknowledgements[acknowledgement_id] = expired
            raise SoftwareAnalysisError(
                SoftwareAnalysisErrorCode.PREVIEW_EXPIRED,
                "Target acknowledgement expired",
            )
        if (
            request.preview_id != preview.preview_id
            or request.preview_digest != preview.canonical_digest()
            or request.identity_digest != preview.identity_digest
        ):
            raise SoftwareAnalysisError(
                SoftwareAnalysisErrorCode.TARGET_CHANGED,
                "Target or Preview changed after acknowledgement was requested",
            )
        state = (
            TargetAcknowledgementState.ACKNOWLEDGED
            if acknowledged
            else TargetAcknowledgementState.REJECTED
        )
        resolved = request.model_copy(update={"state": state})
        self._acknowledgements[acknowledgement_id] = resolved
        return resolved
