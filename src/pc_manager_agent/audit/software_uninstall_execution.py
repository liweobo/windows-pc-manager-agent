"""Privacy-minimized audit for irreversible Stage 4D2A MSI uninstall."""

from __future__ import annotations

from pc_manager_agent.audit.models import AuditEvent
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.confirmation.software_uninstall_execution import MsiUninstallConfirmation
from pc_manager_agent.domain.software_uninstall_execution import (
    MsiUninstallExecutionReport,
    MsiUninstallPlan,
    MsiUninstallPreview,
)


class MsiUninstallAuditLogger:
    """Record only digests, decisions, exit category, and verification evidence."""

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

    def previewed(self, plan: MsiUninstallPlan, preview: MsiUninstallPreview) -> None:
        """Record fresh policy and preflight evidence before any approval exists."""
        self._repository.record(
            AuditEvent(
                event_type="software.msi_uninstall.previewed",
                original_request=plan.user_goal,
                plan={
                    "plan_id": str(plan.plan_id),
                    "transaction_id": str(plan.transaction_id),
                    "plan_digest": plan.canonical_digest(),
                    "identity_digest": plan.identity_digest,
                    "risk_level": plan.risk_level.value,
                },
                plan_id=str(plan.plan_id),
                plan_version=plan.version,
                agent_decision="executable" if preview.executable else "blocked",
                tool_name=plan.tool_name,
                parameters={
                    "preview_id": str(preview.preview_id),
                    "preview_digest": preview.canonical_digest(),
                    "identity_digest": preview.identity_digest,
                    "product_code_digest": preview.validated_product.product_code_digest,
                    "capability_digest": preview.capability.canonical_digest(),
                    "safety_digest": preview.execution_assessment.canonical_digest(),
                    "preflight_digest": preview.preflight.canonical_digest(),
                    "safety_class": preview.execution_assessment.safety_class.value,
                    "related_process_count": len(preview.preflight.related_processes),
                    "related_service_count": len(preview.preflight.related_services),
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
        plan: MsiUninstallPlan,
        confirmation: MsiUninstallConfirmation,
    ) -> None:
        """Record exact digest bindings and one durable decision."""
        self._repository.record(
            AuditEvent(
                event_type=(
                    f"software.msi_uninstall.{confirmation.tier.value}_confirmation_resolved"
                ),
                plan_id=str(plan.plan_id),
                plan_version=plan.version,
                parameters={
                    "transaction_id": str(plan.transaction_id),
                    "confirmation_id": str(confirmation.confirmation_id),
                    "parent_confirmation_id": (
                        str(confirmation.parent_confirmation_id)
                        if confirmation.parent_confirmation_id
                        else None
                    ),
                    "preview_id": str(confirmation.preview_id),
                    "plan_digest": confirmation.plan_digest,
                    "preview_digest": confirmation.preview_digest,
                    "invariant_digest": confirmation.invariant_digest,
                    "identity_digest": confirmation.identity_digest,
                    "product_code_digest": confirmation.product_code_digest,
                    "capability_digest": confirmation.capability_digest,
                    "safety_digest": confirmation.safety_digest,
                    "preflight_digest": confirmation.preflight_digest,
                },
                risk_level=confirmation.risk_level,
                confirmation_required=True,
                confirmation_result=confirmation.state.value,
                rollback={"level": "NONE", "automatic_restore": False},
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )

    def started(
        self,
        plan: MsiUninstallPlan,
        preview: MsiUninstallPreview,
        runtime_confirmation_id: str,
    ) -> None:
        """Append mandatory write-ahead audit before the fixed client is launched."""
        self._repository.record(
            AuditEvent(
                event_type="software.msi_uninstall.started",
                original_request=plan.user_goal,
                plan_id=str(plan.plan_id),
                plan_version=plan.version,
                step_id=str(plan.operation_id),
                tool_name=plan.tool_name,
                parameters={
                    "transaction_id": str(plan.transaction_id),
                    "runtime_confirmation_id": runtime_confirmation_id,
                    "identity_digest": preview.identity_digest,
                    "product_code_digest": preview.validated_product.product_code_digest,
                    "arguments_source": "fixed_adapter_only",
                },
                risk_level=plan.risk_level,
                confirmation_required=True,
                confirmation_result="consumed",
                rollback={"level": "NONE", "automatic_restore": False},
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )

    def completed(
        self,
        plan: MsiUninstallPlan,
        report: MsiUninstallExecutionReport,
    ) -> None:
        """Record installer and final observed states as separate facts."""
        self._repository.record(
            AuditEvent(
                event_type="software.msi_uninstall.completed",
                original_request=plan.user_goal,
                plan_id=str(plan.plan_id),
                plan_version=plan.version,
                step_id=str(plan.operation_id),
                tool_name=plan.tool_name,
                parameters={"transaction_id": str(plan.transaction_id)},
                risk_level=plan.risk_level,
                confirmation_required=True,
                confirmation_result="consumed",
                result={
                    "installer_category": report.installer.category.value,
                    "installer_exit_code": report.installer.exit_code,
                    "launched": report.installer.launched,
                    "reboot_requested_by_agent": False,
                    "process_or_service_control_invoked": False,
                    "residual_deletion_performed": report.residual.deletion_performed,
                },
                verification=report.verification.model_dump(mode="json"),
                rollback={
                    "level": "NONE",
                    "automatic_restore": False,
                    "reinstall_is_not_undo": True,
                },
                app_version=self._app_version,
                git_commit=self._git_commit,
                duration_ms=report.installer.duration_ms,
            )
        )

    def failed(
        self,
        plan: MsiUninstallPlan,
        *,
        phase: str,
        error_code: str,
        mutation_may_have_started: bool,
    ) -> None:
        """Record a failure without retaining exception text or retrying."""
        self._repository.record(
            AuditEvent(
                event_type="software.msi_uninstall.failed",
                original_request=plan.user_goal,
                plan_id=str(plan.plan_id),
                plan_version=plan.version,
                step_id=str(plan.operation_id),
                tool_name=plan.tool_name,
                parameters={"transaction_id": str(plan.transaction_id), "phase": phase},
                risk_level=plan.risk_level,
                confirmation_required=True,
                error={
                    "code": error_code,
                    "mutation_may_have_started": mutation_may_have_started,
                },
                verification={"outcome_known": not mutation_may_have_started},
                rollback={"level": "NONE", "automatic_restore": False},
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )
