"""Privacy-minimized audit trail for controlled startup actions."""

from __future__ import annotations

from uuid import UUID

from pc_manager_agent.audit.models import AuditEvent
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.confirmation.startup_actions import StartupActionConfirmation
from pc_manager_agent.domain.startup_actions import (
    StartupActionPlan,
    StartupActionPreview,
    StartupMutationResult,
)


class StartupActionAuditLogger:
    """Record identity digests and status evidence without exact commands or backup bytes."""

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

    def previewed(self, plan: StartupActionPlan, preview: StartupActionPreview) -> None:
        """Record read-only assessment and verified-backup reference before approval."""
        observation = preview.observation
        self._repository.record(
            AuditEvent(
                event_type="startup.previewed",
                original_request=plan.user_goal,
                plan={
                    "plan_id": str(plan.plan_id),
                    "transaction_id": str(plan.transaction_id),
                    "action": plan.action.value,
                    "risk_level": plan.risk_level.value,
                    "identity_digest": plan.target_identity.canonical_digest(),
                    "backup_id": str(plan.backup_id),
                    "backup_digest": plan.backup_digest,
                },
                plan_id=str(plan.plan_id),
                plan_version=plan.plan_version,
                agent_decision=(
                    "Allowed to request confirmation"
                    if preview.executable
                    else "Blocked by deterministic StartupSafetyPolicy"
                ),
                parameters={
                    "transaction_id": str(plan.transaction_id),
                    "preview_id": str(preview.preview_id),
                    "action": plan.action.value,
                    "display_name": observation.display_name,
                    "source": observation.identity.source.value,
                    "scope": observation.scope,
                    "status": observation.status.value,
                    "publisher": observation.publisher,
                    "executable_path": (
                        str(observation.executable_path) if observation.executable_path else None
                    ),
                    "identity_digest": observation.identity.canonical_digest(),
                    "current_state_digest": preview.current_state_digest,
                    "safety_class": preview.assessment.safety_class.value,
                    "reason_codes": [reason.value for reason in preview.assessment.reason_codes],
                    "backup_verified": preview.backup_verified,
                },
                risk_level=plan.risk_level,
                confirmation_required=True,
                result={"executable": preview.executable, "mutation_executed": False},
                rollback={"level": "FULL", "backup_digest": plan.backup_digest},
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )

    def confirmation_resolved(
        self,
        plan: StartupActionPlan,
        confirmation: StartupActionConfirmation,
    ) -> None:
        """Record tier, action, binding digests, and user decision."""
        self._repository.record(
            AuditEvent(
                event_type=f"startup.{confirmation.tier.value.lower()}_confirmation_resolved",
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
                    "preview_id": str(confirmation.preview_id),
                    "identity_digest": confirmation.identity_digest,
                    "current_state_digest": confirmation.current_state_digest,
                    "backup_id": str(confirmation.backup_id),
                    "backup_digest": confirmation.backup_digest,
                },
                risk_level=plan.risk_level,
                confirmation_required=True,
                confirmation_result=confirmation.state.value,
                rollback={"level": "FULL", "backup_digest": confirmation.backup_digest},
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )

    def started(
        self,
        plan: StartupActionPlan,
        preview: StartupActionPreview,
        runtime_confirmation_id: UUID,
    ) -> None:
        """Append mandatory pre-execution evidence before the write tool can run."""
        self._repository.record(
            AuditEvent(
                event_type="startup.started",
                original_request=plan.user_goal,
                plan_id=str(plan.plan_id),
                plan_version=plan.plan_version,
                step_id=str(plan.operation_id),
                tool_name=_tool_name(plan),
                parameters={
                    "transaction_id": str(plan.transaction_id),
                    "runtime_confirmation_id": str(runtime_confirmation_id),
                    "action": plan.action.value,
                    "identity_digest": preview.observation.identity.canonical_digest(),
                    "current_state_digest": preview.current_state_digest,
                    "backup_digest": preview.backup_digest,
                },
                risk_level=plan.risk_level,
                confirmation_required=True,
                confirmation_result="CONSUMED",
                before_state={"configuration_status": preview.observation.status.value},
                rollback={"level": "FULL", "backup_digest": preview.backup_digest},
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )

    def completed(self, plan: StartupActionPlan, result: StartupMutationResult) -> None:
        """Record verified configuration status without claiming future launch behavior."""
        self._repository.record(
            AuditEvent(
                event_type=("startup.completed" if result.verified else "startup.incomplete"),
                original_request=plan.user_goal,
                plan_id=str(plan.plan_id),
                plan_version=plan.plan_version,
                step_id=str(plan.operation_id),
                tool_name=_tool_name(plan),
                parameters={
                    "transaction_id": str(plan.transaction_id),
                    "action": plan.action.value,
                    "identity_digest": result.identity_digest,
                    "backup_digest": plan.backup_digest,
                },
                risk_level=plan.risk_level,
                confirmation_required=True,
                confirmation_result="CONSUMED",
                result={
                    "configuration_status": result.after_status.value,
                    "verified": result.verified,
                    "future_launch_claimed": False,
                    "rollback_performed": result.rollback_performed,
                    "rollback_verified": result.rollback_verified,
                },
                verification={
                    "configuration_state_verified": result.verified,
                    "launch_behavior_not_verified": True,
                },
                rollback={"level": "FULL", "backup_digest": plan.backup_digest},
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
        plan: StartupActionPlan,
        *,
        phase: str,
        error_code: str,
        message: str,
        mutation_may_have_started: bool,
    ) -> None:
        """Record blocked or failed state without leaking exact restore material."""
        self._repository.record(
            AuditEvent(
                event_type="startup.execution_failed",
                original_request=plan.user_goal,
                plan_id=str(plan.plan_id),
                plan_version=plan.plan_version,
                step_id=str(plan.operation_id),
                tool_name=_tool_name(plan),
                parameters={
                    "transaction_id": str(plan.transaction_id),
                    "action": plan.action.value,
                    "phase": phase,
                    "identity_digest": plan.target_identity.canonical_digest(),
                    "backup_digest": plan.backup_digest,
                },
                risk_level=plan.risk_level,
                confirmation_required=True,
                error={
                    "code": error_code,
                    "message": message,
                    "mutation_may_have_started": mutation_may_have_started,
                },
                rollback={"level": "FULL", "backup_digest": plan.backup_digest},
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )


def _tool_name(plan: StartupActionPlan) -> str:
    return "startup.disable" if plan.action.value == "DISABLE" else "startup.restore"
