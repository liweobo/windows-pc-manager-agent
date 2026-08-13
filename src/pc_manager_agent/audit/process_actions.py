"""Privacy-minimized audit trail for controlled process actions."""

from __future__ import annotations

from pydantic import JsonValue

from pc_manager_agent.audit.models import AuditEvent
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.confirmation.process_actions import ProcessActionConfirmation
from pc_manager_agent.domain.process_actions import (
    ProcessActionPlan,
    ProcessActionPreview,
    ProcessActionToolResult,
    ProcessIdentity,
)


class ProcessActionAuditLogger:
    """Record process identity evidence without command lines or window content."""

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

    def previewed(self, plan: ProcessActionPlan, preview: ProcessActionPreview) -> None:
        """Record the read-only impact assessment before any authorization exists."""
        self._repository.record(
            AuditEvent(
                event_type="process.previewed",
                original_request=plan.user_goal,
                plan={
                    "plan_id": str(plan.plan_id),
                    "transaction_id": str(plan.transaction_id),
                    "action": plan.action.value,
                    "risk_level": plan.risk_level.value,
                    "target_set_digest": plan.target_set_digest(),
                    "target_query": plan.target_query.model_dump(mode="json"),
                },
                plan_id=str(plan.plan_id),
                plan_version=plan.plan_version,
                agent_decision=(
                    "Allowed to request confirmation"
                    if preview.executable
                    else "Blocked by deterministic ProcessSafetyPolicy"
                ),
                parameters={
                    "transaction_id": str(plan.transaction_id),
                    "preview_id": str(preview.preview_id),
                    "action": plan.action.value,
                    "target_set_digest": preview.target_set_digest,
                    "application_count": preview.application_count,
                    "process_count": preview.process_count,
                    "safety_classes": [item.safety_class.value for item in preview.assessments],
                    "reason_codes": [
                        reason.value for item in preview.assessments for reason in item.reason_codes
                    ],
                    "resolved_targets": [
                        {
                            "display_name": target.display_name,
                            "members": [
                                _identity_evidence(member.identity) for member in target.members
                            ],
                        }
                        for target in preview.targets
                    ],
                },
                risk_level=plan.risk_level,
                confirmation_required=True,
                result={"executable": preview.executable, "mutation_executed": False},
                rollback={"level": "NONE", "automatic_restore": False},
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )

    def confirmation_resolved(
        self,
        plan: ProcessActionPlan,
        confirmation: ProcessActionConfirmation,
    ) -> None:
        """Record tier, action, binding digests, and the exact decision."""
        self._repository.record(
            AuditEvent(
                event_type=f"process.{confirmation.tier.value.lower()}_confirmation_resolved",
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
                    "plan_digest": confirmation.plan_digest,
                    "preview_digest": confirmation.preview_digest,
                    "target_set_digest": confirmation.target_set_digest,
                    "process_count": confirmation.process_count,
                },
                risk_level=plan.risk_level,
                confirmation_required=True,
                confirmation_result=confirmation.state.value,
                rollback={"level": "NONE", "automatic_restore": False},
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )

    def started(
        self,
        plan: ProcessActionPlan,
        preview: ProcessActionPreview,
        runtime_confirmation_id: str,
        tool_name: str,
    ) -> None:
        """Fail closed before platform invocation if mandatory audit cannot be appended."""
        self._repository.record(
            AuditEvent(
                event_type="process.started",
                original_request=plan.user_goal,
                plan_id=str(plan.plan_id),
                plan_version=plan.plan_version,
                step_id=str(plan.operation_id),
                tool_name=tool_name,
                parameters={
                    "transaction_id": str(plan.transaction_id),
                    "runtime_confirmation_id": runtime_confirmation_id,
                    "action": plan.action.value,
                    "target_set_digest": preview.target_set_digest,
                    "process_count": preview.process_count,
                },
                risk_level=plan.risk_level,
                confirmation_required=True,
                confirmation_result="CONSUMED",
                before_state={
                    "process_identities": [
                        _identity_evidence(member.identity)
                        for target in preview.targets
                        for member in target.members
                    ]
                },
                rollback={"level": "NONE", "automatic_restore": False},
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )

    def completed(
        self,
        plan: ProcessActionPlan,
        result: ProcessActionToolResult,
    ) -> None:
        """Record platform and verification outcomes for every original identity."""
        self._repository.record(
            AuditEvent(
                event_type="process.completed" if result.all_exited else "process.incomplete",
                original_request=plan.user_goal,
                plan_id=str(plan.plan_id),
                plan_version=plan.plan_version,
                step_id=str(plan.operation_id),
                tool_name=_tool_name(plan),
                parameters={
                    "transaction_id": str(plan.transaction_id),
                    "action": plan.action.value,
                },
                risk_level=plan.risk_level,
                confirmation_required=True,
                confirmation_result="CONSUMED",
                result={
                    "member_states": [item.state.value for item in result.members],
                    "windows_notified": sum(item.windows_notified for item in result.members),
                    "all_exited": result.all_exited,
                },
                verification={
                    "identity_digests": [item.identity_digest for item in result.members],
                    "original_identities_exited": result.all_exited,
                },
                rollback={
                    "level": "NONE",
                    "automatic_restore": False,
                    "manual_restart_is_not_undo": True,
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
        plan: ProcessActionPlan,
        *,
        phase: str,
        error_code: str,
        message: str,
        mutation_may_have_started: bool,
    ) -> None:
        """Record a blocked or failed boundary without claiming an execution outcome."""
        self._repository.record(
            AuditEvent(
                event_type="process.execution_failed",
                original_request=plan.user_goal,
                plan_id=str(plan.plan_id),
                plan_version=plan.plan_version,
                step_id=str(plan.operation_id),
                tool_name=_tool_name(plan),
                parameters={
                    "transaction_id": str(plan.transaction_id),
                    "action": plan.action.value,
                    "phase": phase,
                },
                risk_level=plan.risk_level,
                confirmation_required=True,
                error={
                    "code": error_code,
                    "message": message,
                    "mutation_may_have_started": mutation_may_have_started,
                },
                verification={"outcome_known": not mutation_may_have_started},
                rollback={"level": "NONE", "automatic_restore": False},
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )


def _tool_name(plan: ProcessActionPlan) -> str:
    return (
        "system.process.request_exit"
        if plan.action.value == "REQUEST_GRACEFUL_EXIT"
        else "system.process.force_terminate"
    )


def _identity_evidence(identity: ProcessIdentity) -> dict[str, JsonValue]:
    return {
        "pid": identity.pid,
        "process_name": identity.process_name,
        "create_time": identity.create_time.isoformat(),
        "executable_path": str(identity.executable_path),
        "owner_sid": identity.owner_sid,
        "username": identity.username,
        "session_id": identity.session_id,
        "identity_digest": identity.canonical_digest(),
    }
