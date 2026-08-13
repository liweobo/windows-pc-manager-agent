"""Privacy-minimized audit trail for controlled service state changes."""

from pc_manager_agent.audit.models import AuditEvent
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.confirmation.service_actions import ServiceActionConfirmation
from pc_manager_agent.domain.service_actions import (
    ServiceActionPlan,
    ServiceActionPreview,
    ServiceActionResult,
    ServiceStepResult,
)


class ServiceActionAuditLogger:
    """Record service state evidence without binary arguments or sensitive content."""

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

    def previewed(self, plan: ServiceActionPlan, preview: ServiceActionPreview) -> None:
        """Record policy, dependencies, and permission evidence before approval."""
        observation = preview.observation
        self._repository.record(
            AuditEvent(
                event_type="service.previewed",
                original_request=plan.user_goal,
                plan={
                    "plan_id": str(plan.plan_id),
                    "transaction_id": str(plan.transaction_id),
                    "action": plan.action.value,
                    "steps": [step.value for step in plan.steps],
                    "risk_level": plan.risk_level.value,
                },
                plan_id=str(plan.plan_id),
                plan_version=plan.plan_version,
                agent_decision=(
                    "Allowed to request confirmation"
                    if preview.executable
                    else "Blocked by deterministic service safety gates"
                ),
                parameters={
                    "transaction_id": str(plan.transaction_id),
                    "preview_id": str(preview.preview_id),
                    "service_name": observation.identity.service_name,
                    "display_name": observation.identity.display_name,
                    "identity_digest": observation.identity.canonical_digest(),
                    "state": observation.state.value,
                    "state_digest": preview.current_state_digest,
                    "dependency_digest": preview.dependencies.graph_digest,
                    "permission_digest": preview.permissions.canonical_digest(),
                    "safety_class": preview.safety.safety_class.value,
                    "publisher_verified": observation.publisher_verified,
                    "reason_codes": [reason.value for reason in preview.safety.reason_codes],
                    "dependency_names": [item.service_name for item in observation.dependencies],
                    "dependent_names": [item.service_name for item in observation.dependents],
                },
                risk_level=plan.risk_level,
                confirmation_required=True,
                result={"executable": preview.executable, "mutation_executed": False},
                rollback={"level": "MANUAL", "automatic_restore": False},
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )

    def confirmation_resolved(
        self,
        plan: ServiceActionPlan,
        confirmation: ServiceActionConfirmation,
    ) -> None:
        """Record exact tier, binding digests, and user decision."""
        self._repository.record(
            AuditEvent(
                event_type=f"service.{confirmation.tier.value.lower()}_confirmation_resolved",
                original_request=plan.user_goal,
                plan_id=str(plan.plan_id),
                plan_version=plan.plan_version,
                parameters={
                    "transaction_id": str(plan.transaction_id),
                    "confirmation_id": str(confirmation.confirmation_id),
                    "parent_confirmation_id": (
                        str(confirmation.parent_confirmation_id)
                        if confirmation.parent_confirmation_id
                        else None
                    ),
                    "action": confirmation.action.value,
                    "identity_digest": confirmation.identity_digest,
                    "state_digest": confirmation.state_digest,
                    "dependency_digest": confirmation.dependency_digest,
                    "permission_digest": confirmation.permission_digest,
                },
                risk_level=plan.risk_level,
                confirmation_required=True,
                confirmation_result=confirmation.state.value,
                rollback={"level": "MANUAL", "automatic_restore": False},
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )

    def started(self, plan: ServiceActionPlan, preview: ServiceActionPreview) -> None:
        """Append mandatory pre-execution evidence before any SCM control."""
        self._repository.record(
            AuditEvent(
                event_type="service.started",
                original_request=plan.user_goal,
                plan_id=str(plan.plan_id),
                plan_version=plan.plan_version,
                step_id=str(plan.operation_id),
                parameters={
                    "transaction_id": str(plan.transaction_id),
                    "action": plan.action.value,
                    "service_name": plan.target_identity.service_name,
                    "identity_digest": plan.target_identity.canonical_digest(),
                    "preview_id": str(preview.preview_id),
                },
                risk_level=plan.risk_level,
                confirmation_required=True,
                confirmation_result="CONSUMED",
                before_state={"state": preview.observation.state.value},
                rollback={"level": "MANUAL", "automatic_restore": False},
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )

    def step_completed(
        self,
        plan: ServiceActionPlan,
        index: int,
        result: ServiceStepResult,
    ) -> None:
        """Record one explicit restart/start/stop substep and verification result."""
        self._repository.record(
            AuditEvent(
                event_type="service.step_completed",
                original_request=plan.user_goal,
                plan_id=str(plan.plan_id),
                plan_version=plan.plan_version,
                step_id=f"{plan.operation_id}:{index}",
                tool_name=(
                    "system.service.start"
                    if result.step.value == "START"
                    else "system.service.stop"
                ),
                parameters={
                    "transaction_id": str(plan.transaction_id),
                    "step_index": index,
                    "step": result.step.value,
                    "identity_digest": result.identity_digest,
                },
                risk_level=plan.risk_level,
                confirmation_required=True,
                confirmation_result="CONSUMED",
                before_state={"state": result.before_state.value},
                result={
                    "after_state": result.after_state.value,
                    "control_dispatched": result.control_dispatched,
                    "verified": result.verified,
                    "cancelled_after_dispatch": result.cancellation_requested_after_dispatch,
                },
                verification={"expected_stable_state_reached": result.verified},
                rollback={"level": "MANUAL", "automatic_restore": False},
                app_version=self._app_version,
                git_commit=self._git_commit,
                duration_ms=max(
                    0,
                    int((result.completed_at - result.started_at).total_seconds() * 1_000),
                ),
            )
        )

    def completed(self, plan: ServiceActionPlan, result: ServiceActionResult) -> None:
        """Record final/partial state without calling a reverse action an undo."""
        self._repository.record(
            AuditEvent(
                event_type=(
                    "service.completed" if result.completed else "service.partially_completed"
                ),
                original_request=plan.user_goal,
                plan_id=str(plan.plan_id),
                plan_version=plan.plan_version,
                step_id=str(plan.operation_id),
                parameters={
                    "transaction_id": str(plan.transaction_id),
                    "action": plan.action.value,
                    "service_name": plan.target_identity.service_name,
                },
                risk_level=plan.risk_level,
                confirmation_required=True,
                confirmation_result="CONSUMED",
                result={
                    "final_state": result.final_state.value,
                    "completed": result.completed,
                    "partially_completed": result.partially_completed,
                    "no_op": result.no_op,
                },
                verification={"final_state_read_from_scm": True},
                rollback={
                    "level": "MANUAL",
                    "automatic_restore": False,
                    "new_reverse_plan_required": True,
                },
                app_version=self._app_version,
                git_commit=self._git_commit,
                duration_ms=max(
                    0,
                    int((result.completed_at - result.started_at).total_seconds() * 1_000),
                ),
            )
        )

    def failed(
        self,
        plan: ServiceActionPlan,
        *,
        phase: str,
        error_code: str,
        message: str,
        mutation_may_have_started: bool,
    ) -> None:
        """Record blocked or failed control without claiming an unknown outcome."""
        self._repository.record(
            AuditEvent(
                event_type="service.execution_failed",
                original_request=plan.user_goal,
                plan_id=str(plan.plan_id),
                plan_version=plan.plan_version,
                step_id=str(plan.operation_id),
                parameters={
                    "transaction_id": str(plan.transaction_id),
                    "action": plan.action.value,
                    "phase": phase,
                    "identity_digest": plan.target_identity.canonical_digest(),
                },
                risk_level=plan.risk_level,
                confirmation_required=True,
                error={
                    "code": error_code,
                    "message": message,
                    "mutation_may_have_started": mutation_may_have_started,
                },
                verification={"outcome_known": not mutation_may_have_started},
                rollback={"level": "MANUAL", "automatic_restore": False},
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )
