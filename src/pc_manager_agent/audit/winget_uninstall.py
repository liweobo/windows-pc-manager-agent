"""Privacy-minimized audit for irreversible Stage 4D2C1 winget removal."""

from __future__ import annotations

from pc_manager_agent.audit.models import AuditEvent
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.confirmation.winget_uninstall import WingetUninstallConfirmation
from pc_manager_agent.domain.winget_uninstall import (
    WingetUninstallExecutionReport,
    WingetUninstallPlan,
    WingetUninstallPreview,
)


class WingetUninstallAuditLogger:
    """Record safe digests and outcomes without commands, paths, URLs, or environment values."""

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

    def previewed(self, plan: WingetUninstallPlan, preview: WingetUninstallPreview) -> None:
        """Record all authorization digests before the first approval."""
        executable = preview.availability.executable
        self._repository.record(
            AuditEvent(
                event_type="software.winget_uninstall.previewed",
                original_request=plan.user_goal,
                plan={
                    "plan_id": str(plan.plan_id),
                    "transaction_id": str(plan.transaction_id),
                    "plan_digest": plan.canonical_digest(),
                    "risk_level": plan.risk_level.value,
                },
                plan_id=str(plan.plan_id),
                plan_version=plan.version,
                agent_decision="executable" if preview.executable else "blocked",
                tool_name=plan.tool_name,
                parameters={
                    "preview_id": str(preview.preview_id),
                    "preview_digest": preview.canonical_digest(),
                    "package_identity_digest": plan.package_identity_digest,
                    "software_identity_digest": plan.software_identity_digest,
                    "mapping_digest": preview.mapping.canonical_digest(),
                    "executable_identity_digest": (
                        executable.invariant_digest() if executable is not None else None
                    ),
                    "capability_digest": preview.capability.canonical_digest(),
                    "safety_digest": preview.execution_assessment.canonical_digest(),
                    "preflight_digest": preview.preflight.canonical_digest(),
                    "fixed_argument_policy": "stage_4d2c1_v1",
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
        plan: WingetUninstallPlan,
        confirmation: WingetUninstallConfirmation,
    ) -> None:
        """Record the tier, safe bindings, and durable user decision."""
        self._repository.record(
            AuditEvent(
                event_type=(
                    f"software.winget_uninstall.{confirmation.tier.value}_confirmation_resolved"
                ),
                plan_id=str(plan.plan_id),
                plan_version=plan.version,
                parameters={
                    "transaction_id": str(plan.transaction_id),
                    "confirmation_id": str(confirmation.confirmation_id),
                    "preview_id": str(confirmation.preview_id),
                    "plan_digest": confirmation.plan_digest,
                    "preview_digest": confirmation.preview_digest,
                    "invariant_digest": confirmation.invariant_digest,
                    "package_identity_digest": confirmation.package_identity_digest,
                    "software_identity_digest": confirmation.software_identity_digest,
                    "mapping_digest": confirmation.mapping_digest,
                    "executable_identity_digest": confirmation.executable_identity_digest,
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
        plan: WingetUninstallPlan,
        preview: WingetUninstallPreview,
        runtime_confirmation_id: str,
    ) -> None:
        """Write the mandatory pre-launch audit without storing the command line."""
        self._repository.record(
            AuditEvent(
                event_type="software.winget_uninstall.started",
                original_request=plan.user_goal,
                plan_id=str(plan.plan_id),
                plan_version=plan.version,
                step_id=str(plan.operation_id),
                tool_name=plan.tool_name,
                parameters={
                    "transaction_id": str(plan.transaction_id),
                    "runtime_confirmation_id": runtime_confirmation_id,
                    "package_identity_digest": plan.package_identity_digest,
                    "software_identity_digest": plan.software_identity_digest,
                    "arguments_source": "fixed_stage_4d2c1_policy",
                    "source": "winget",
                    "scope": "current_user",
                    "environment_policy": "sanitized_allowlist",
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
        plan: WingetUninstallPlan,
        report: WingetUninstallExecutionReport,
    ) -> None:
        """Record process evidence and dual-inventory verification separately."""
        self._repository.record(
            AuditEvent(
                event_type="software.winget_uninstall.completed",
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
                    "process_category": report.process.category.value,
                    "process_exit_code": report.process.exit_code,
                    "launched": report.process.launched,
                    "shell_used": False,
                    "elevation_requested_by_agent": False,
                    "process_or_service_control_invoked": False,
                    "automatic_restart_requested": False,
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
                duration_ms=report.process.duration_ms,
            )
        )

    def failed(
        self,
        plan: WingetUninstallPlan,
        *,
        phase: str,
        error_code: str,
        mutation_may_have_started: bool,
    ) -> None:
        """Record a sanitized failure and explicitly prohibit automatic retry."""
        self._repository.record(
            AuditEvent(
                event_type="software.winget_uninstall.failed",
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
                    "automatic_retry": False,
                },
                rollback={"level": "NONE", "automatic_restore": False},
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )
