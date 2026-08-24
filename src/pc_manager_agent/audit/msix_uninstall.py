"""Privacy-minimized audit events for irreversible current-user MSIX removal."""

from __future__ import annotations

from pc_manager_agent.audit.models import AuditEvent
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.confirmation.msix_uninstall import MsixUninstallConfirmation
from pc_manager_agent.domain.msix_uninstall import (
    MsixUninstallPlan,
    MsixUninstallPreview,
    MsixUninstallResult,
)


class MsixUninstallAuditLogger:
    """Record safe identity digests and outcomes without manifests or user-data paths."""

    def __init__(
        self, repository: AuditRepository, *, app_version: str, git_commit: str | None
    ) -> None:
        self._repository = repository
        self._app_version = app_version
        self._git_commit = git_commit

    def previewed(self, plan: MsixUninstallPlan, preview: MsixUninstallPreview) -> None:
        """Record authorization evidence and Windows data-impact semantics before approval."""
        self._repository.record(
            AuditEvent(
                event_type="software.msix_uninstall.previewed",
                original_request=plan.user_goal,
                plan={
                    "plan_digest": plan.canonical_digest(),
                    "transaction_id": str(plan.transaction_id),
                },
                plan_id=str(plan.plan_id),
                agent_decision="executable" if preview.executable else "blocked",
                tool_name=plan.tool_name,
                parameters={
                    "package_identity_digest": plan.package_identity_digest,
                    "dependency_digest": plan.dependency_digest,
                    "assessment_digest": plan.assessment_digest,
                    "preflight_digest": plan.preflight_digest,
                    "scope": "current_user",
                    "package_type": preview.package.identity.package_type.value,
                    "removal_option": "preserve_roamable_application_data",
                    "local_state_may_be_removed": True,
                    "windows_orphan_dependency_removal_possible": True,
                    "agent_extra_data_deletion": False,
                },
                risk_level=plan.risk_level,
                confirmation_required=True,
                result={"mutation_executed": False},
                rollback={"level": "NONE", "automatic_restore": False},
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )

    def confirmation_resolved(
        self, plan: MsixUninstallPlan, confirmation: MsixUninstallConfirmation
    ) -> None:
        """Record approval tier, safe bindings, and durable user decision."""
        self._repository.record(
            AuditEvent(
                event_type=f"software.msix_uninstall.{confirmation.tier.value}_confirmation",
                plan_id=str(plan.plan_id),
                tool_name=plan.tool_name,
                parameters={
                    "transaction_id": str(plan.transaction_id),
                    "confirmation_id": str(confirmation.confirmation_id),
                    "package_identity_digest": confirmation.package_identity_digest,
                    "dependency_digest": confirmation.dependency_digest,
                    "data_impact_digest": confirmation.data_impact_digest,
                },
                risk_level=plan.risk_level,
                confirmation_required=True,
                confirmation_result=confirmation.state.value,
                rollback={"level": "NONE", "automatic_restore": False},
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )

    def completed(self, plan: MsixUninstallPlan, result: MsixUninstallResult) -> None:
        """Record deployment and fresh verification as separate facts."""
        self._repository.record(
            AuditEvent(
                event_type="software.msix_uninstall.completed",
                original_request=plan.user_goal,
                plan_id=str(plan.plan_id),
                step_id=str(plan.operation_id),
                tool_name=plan.tool_name,
                parameters={"transaction_id": str(plan.transaction_id)},
                risk_level=plan.risk_level,
                confirmation_required=True,
                confirmation_result="consumed",
                result={
                    "deployment_category": result.deployment.category.value,
                    "dispatched": result.deployment.dispatched,
                    "shell_used": False,
                    "powershell_used": False,
                    "elevation_requested": False,
                    "process_or_service_control_invoked": False,
                    "extra_data_deletion": result.residual.user_data_deleted_by_agent,
                },
                verification=result.verification.model_dump(mode="json"),
                rollback={"level": "NONE", "reinstall_is_not_undo": True},
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )

    def started(
        self,
        plan: MsixUninstallPlan,
        preview: MsixUninstallPreview,
        runtime_confirmation_id: str,
    ) -> None:
        """Write the mandatory pre-dispatch audit without package paths or manifests."""
        self._repository.record(
            AuditEvent(
                event_type="software.msix_uninstall.started",
                original_request=plan.user_goal,
                plan_id=str(plan.plan_id),
                step_id=str(plan.operation_id),
                tool_name=plan.tool_name,
                parameters={
                    "transaction_id": str(plan.transaction_id),
                    "runtime_confirmation_id": runtime_confirmation_id,
                    "package_identity_digest": plan.package_identity_digest,
                    "dependency_digest": preview.dependencies.canonical_digest(),
                    "scope": "current_user",
                    "removal_option": "preserve_roamable_application_data",
                },
                risk_level=plan.risk_level,
                confirmation_required=True,
                confirmation_result="consumed",
                rollback={"level": "NONE", "automatic_restore": False},
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )
