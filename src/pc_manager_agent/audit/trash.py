"""Structured R2 audit events for Recycle Bin planning and execution."""

from __future__ import annotations

from pc_manager_agent.audit.models import AuditEvent
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.confirmation.trash import TrashConfirmation
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.trash import RecycleBinResult, TrashPlan, TrashPlanItem, TrashPreview
from pc_manager_agent.recovery.models import TrashRecoveryRecord


class TrashAuditLogger:
    """Record double confirmation and truthful MANUAL recovery without file contents."""

    def __init__(
        self,
        repository: AuditRepository,
        *,
        app_version: str,
        git_commit: str | None,
    ) -> None:
        self._repository = repository
        self._app_version = app_version
        self._git_commit = git_commit

    def previewed(self, plan: TrashPlan, preview: TrashPreview) -> None:
        """Record an R2 Preview that has not yet authorized any mutation."""
        self._repository.record(
            AuditEvent(
                event_type="trash.previewed",
                original_request=plan.user_goal,
                plan=plan.model_dump(mode="json"),
                plan_id=str(plan.plan_id),
                plan_version=plan.plan_version,
                agent_decision="R2 Preview generated from explicit selection; no write executed",
                parameters={
                    "transaction_id": str(preview.transaction_id),
                    "preview_id": str(preview.preview_id),
                    "object_set_digest": preview.object_set_digest,
                    "selected_count": preview.selected_count,
                    "ready_count": preview.ready_count,
                    "blocked_count": preview.blocked_count,
                },
                risk_level=RiskLevel.R2,
                confirmation_required=True,
                result={
                    "total_size_bytes": preview.total_size_bytes,
                    "contained_object_count": preview.contained_object_count,
                    "impact_level": preview.impact_level.value,
                    "mutation_executed": False,
                },
                rollback={"level": "MANUAL", "automatic_restore": False},
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )

    def confirmation_resolved(
        self,
        plan: TrashPlan,
        confirmation: TrashConfirmation,
    ) -> None:
        """Record either confirmation tier and all non-secret binding digests."""
        self._repository.record(
            AuditEvent(
                event_type=f"trash.{confirmation.tier.value.lower()}_confirmation_resolved",
                original_request=plan.user_goal,
                plan_id=str(plan.plan_id),
                plan_version=plan.plan_version,
                parameters={
                    "confirmation_id": str(confirmation.confirmation_id),
                    "parent_confirmation_id": (
                        str(confirmation.parent_confirmation_id)
                        if confirmation.parent_confirmation_id
                        else None
                    ),
                    "transaction_id": str(confirmation.transaction_id),
                    "preview_id": str(confirmation.preview_id),
                    "plan_digest": confirmation.plan_digest,
                    "preview_digest": confirmation.preview_digest,
                    "object_set_digest": confirmation.object_set_digest,
                    "selected_count": confirmation.selected_count,
                    "total_size_bytes": confirmation.total_size_bytes,
                },
                risk_level=RiskLevel.R2,
                confirmation_required=True,
                confirmation_result=confirmation.state.value,
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )

    def item_result(
        self,
        plan: TrashPlan,
        item: TrashPlanItem,
        result: RecycleBinResult | None,
        recovery: TrashRecoveryRecord,
        error: Exception | None = None,
    ) -> None:
        """Record Shell evidence, verification status, and recovery truth for one item."""
        self._repository.record(
            AuditEvent(
                event_type="trash.completed" if result and result.recycled else "trash.failed",
                original_request=plan.user_goal,
                plan_id=str(plan.plan_id),
                plan_version=plan.plan_version,
                step_id=str(item.operation_id),
                tool_name="file.trash",
                parameters={
                    "transaction_id": str(recovery.transaction_id),
                    "source": str(item.source),
                    "operation_type": item.operation_type.value,
                },
                risk_level=RiskLevel.R2,
                confirmation_required=True,
                confirmation_result="CONSUMED",
                before_state=recovery.before_state.model_dump(mode="json"),
                result=(result.model_dump(mode="json") if result else None),
                error=({"type": type(error).__name__, "message": str(error)} if error else None),
                rollback={
                    "level": recovery.recovery_level.value,
                    "recovery_id": str(recovery.recovery_id),
                    "status": recovery.status.value,
                    "automatic_restore": False,
                },
                verification={
                    "status": (
                        recovery.verification_status.value
                        if recovery.verification_status is not None
                        else "UNKNOWN"
                    )
                },
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )

    def item_started(
        self,
        plan: TrashPlan,
        item: TrashPlanItem,
        recovery: TrashRecoveryRecord,
        runtime_confirmation_id: str,
    ) -> None:
        """Fail closed before Shell invocation if mandatory audit storage is unavailable."""
        self._repository.record(
            AuditEvent(
                event_type="trash.started",
                original_request=plan.user_goal,
                plan_id=str(plan.plan_id),
                plan_version=plan.plan_version,
                step_id=str(item.operation_id),
                tool_name="file.trash",
                parameters={
                    "transaction_id": str(recovery.transaction_id),
                    "source": str(item.source),
                    "operation_type": item.operation_type.value,
                    "runtime_confirmation_id": runtime_confirmation_id,
                },
                risk_level=RiskLevel.R2,
                confirmation_required=True,
                confirmation_result="CONSUMED",
                before_state=recovery.before_state.model_dump(mode="json"),
                rollback={
                    "level": "MANUAL",
                    "recovery_id": str(recovery.recovery_id),
                    "status": recovery.status.value,
                    "automatic_restore": False,
                },
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )
