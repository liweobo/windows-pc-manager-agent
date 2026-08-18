"""Privacy-minimized audit trail for persistent service startup configuration writes."""

from uuid import UUID

from pc_manager_agent.audit.models import AuditEvent
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.confirmation.service_startup_actions import (
    ServiceStartupActionConfirmation,
)
from pc_manager_agent.domain.service_startup_actions import (
    ServiceStartupActionPlan,
    ServiceStartupActionPreview,
    ServiceStartupMutationResult,
)


class ServiceStartupActionAuditLogger:
    """Record digests and state evidence without service command lines or backup bytes."""

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

    def previewed(
        self,
        plan: ServiceStartupActionPlan,
        preview: ServiceStartupActionPreview,
    ) -> None:
        """Record policy, impact, permission, and verified-backup evidence."""
        self._repository.record(
            AuditEvent(
                event_type="service_startup.previewed",
                original_request=plan.user_goal,
                plan={
                    "plan_id": str(plan.plan_id),
                    "transaction_id": str(plan.transaction_id),
                    "action": plan.action.value,
                    "risk_level": plan.risk_level.value,
                    "identity_digest": plan.target_identity.canonical_digest(),
                    "source_configuration_digest": (plan.source_configuration.canonical_digest()),
                    "target_configuration_digest": (plan.target_configuration.canonical_digest()),
                    "backup_id": str(plan.backup_id),
                    "backup_digest": plan.backup_digest,
                },
                plan_id=str(plan.plan_id),
                plan_version=plan.plan_version,
                agent_decision=(
                    "Allowed to request confirmation"
                    if preview.executable
                    else "Blocked by deterministic service startup safety gates"
                ),
                parameters={
                    "transaction_id": str(plan.transaction_id),
                    "preview_id": str(preview.preview_id),
                    "service_name": plan.target_identity.service_name,
                    "display_name": plan.display_name,
                    "source_type": plan.source_configuration.startup_type.value,
                    "target_type": plan.target_configuration.startup_type.value,
                    "runtime_state": preview.observation.state.value,
                    "runtime_change_expected": preview.impact.runtime_change_expected,
                    "impact_digest": preview.impact.canonical_digest(),
                    "permission_digest": preview.permissions.canonical_digest(),
                    "safety_class": preview.safety.safety_class.value,
                    "reason_codes": [item.value for item in preview.safety.reason_codes],
                    "backup_verified": preview.backup_verified,
                },
                risk_level=plan.risk_level,
                confirmation_required=True,
                result={"executable": preview.executable, "mutation_executed": False},
                rollback={
                    "level": "FULL",
                    "conditional_on_exact_agent_written_state": True,
                    "backup_digest": plan.backup_digest,
                },
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )

    def confirmation_resolved(
        self,
        plan: ServiceStartupActionPlan,
        confirmation: ServiceStartupActionConfirmation,
    ) -> None:
        """Record tier, immutable binding digests, expiry, and user decision."""
        self._repository.record(
            AuditEvent(
                event_type=(
                    f"service_startup.{confirmation.tier.value.lower()}_confirmation_resolved"
                ),
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
                    "source_configuration_digest": (confirmation.source_configuration_digest),
                    "target_configuration_digest": (confirmation.target_configuration_digest),
                    "impact_digest": confirmation.impact_digest,
                    "permission_digest": confirmation.permission_digest,
                    "backup_id": str(confirmation.backup_id),
                    "backup_digest": confirmation.backup_digest,
                    "expires_at": confirmation.expires_at.isoformat(),
                },
                risk_level=plan.risk_level,
                confirmation_required=True,
                confirmation_result=confirmation.state.value,
                rollback={"level": "FULL", "backup_digest": plan.backup_digest},
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )

    def started(
        self,
        plan: ServiceStartupActionPlan,
        preview: ServiceStartupActionPreview,
        runtime_confirmation_id: UUID,
    ) -> None:
        """Append mandatory pre-write evidence before tool registry dispatch."""
        self._repository.record(
            AuditEvent(
                event_type="service_startup.started",
                original_request=plan.user_goal,
                plan_id=str(plan.plan_id),
                plan_version=plan.plan_version,
                step_id=str(plan.operation_id),
                tool_name=_tool_name(plan),
                parameters={
                    "transaction_id": str(plan.transaction_id),
                    "runtime_confirmation_id": str(runtime_confirmation_id),
                    "action": plan.action.value,
                    "identity_digest": plan.target_identity.canonical_digest(),
                    "source_configuration_digest": (plan.source_configuration.canonical_digest()),
                    "target_configuration_digest": (plan.target_configuration.canonical_digest()),
                    "backup_digest": plan.backup_digest,
                },
                risk_level=plan.risk_level,
                confirmation_required=True,
                confirmation_result="CONSUMED",
                before_state={
                    "startup_type": plan.source_configuration.startup_type.value,
                    "runtime_state": preview.observation.state.value,
                },
                rollback={"level": "FULL", "backup_digest": plan.backup_digest},
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )

    def completed(
        self,
        plan: ServiceStartupActionPlan,
        result: ServiceStartupMutationResult,
    ) -> None:
        """Record read-back configuration and proof that runtime state was not changed."""
        self._repository.record(
            AuditEvent(
                event_type=(
                    "service_startup.completed" if result.verified else "service_startup.incomplete"
                ),
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
                    "before_startup_type": result.before_configuration.startup_type.value,
                    "after_startup_type": result.after_configuration.startup_type.value,
                    "before_runtime_state": result.before_runtime_state.value,
                    "after_runtime_state": result.after_runtime_state.value,
                    "change_dispatched": result.change_dispatched,
                    "verified": result.verified,
                    "runtime_unchanged": result.runtime_unchanged,
                },
                verification={
                    "configuration_read_back": result.verified,
                    "runtime_state_unchanged": result.runtime_unchanged,
                },
                rollback={
                    "level": "FULL",
                    "conditional_on_exact_agent_written_state": True,
                    "backup_digest": plan.backup_digest,
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
        plan: ServiceStartupActionPlan,
        *,
        phase: str,
        error_code: str,
        message: str,
        mutation_may_have_started: bool,
    ) -> None:
        """Record failure without claiming the configuration outcome is known."""
        self._repository.record(
            AuditEvent(
                event_type="service_startup.execution_failed",
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
                verification={"outcome_known": not mutation_may_have_started},
                rollback={"level": "FULL", "backup_digest": plan.backup_digest},
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )


def _tool_name(plan: ServiceStartupActionPlan) -> str:
    return {
        "SET_AUTOMATIC": "system.service.startup.set_automatic",
        "SET_MANUAL": "system.service.startup.set_manual",
        "RESTORE": "system.service.startup.restore",
    }[plan.action.value]
