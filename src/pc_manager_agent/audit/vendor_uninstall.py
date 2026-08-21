"""Privacy-minimized audit for irreversible Stage 4D2B Vendor uninstall."""

from __future__ import annotations

from pc_manager_agent.audit.models import AuditEvent
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.confirmation.vendor_uninstall import VendorUninstallConfirmation
from pc_manager_agent.domain.vendor_uninstall import (
    VendorUninstallExecutionReport,
    VendorUninstallPlan,
    VendorUninstallPreview,
)


class VendorUninstallAuditLogger:
    """Record digests and outcomes without raw commands, paths, arguments, or environment."""

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

    def previewed(self, plan: VendorUninstallPlan, preview: VendorUninstallPreview) -> None:
        """Record current trust, policy, and preflight digests before approval."""
        self._repository.record(
            AuditEvent(
                event_type="software.vendor_uninstall.previewed",
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
                    "vendor_identity_digest": preview.vendor_identity.invariant_digest(),
                    "argument_digest": (
                        preview.vendor_identity.argument_assessment.argument_fingerprint
                    ),
                    "executable_file_digest": (
                        preview.vendor_identity.executable.file_identity.canonical_digest()
                    ),
                    "capability_digest": preview.capability.canonical_digest(),
                    "safety_digest": preview.execution_assessment.canonical_digest(),
                    "preflight_digest": preview.preflight.canonical_digest(),
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
        plan: VendorUninstallPlan,
        confirmation: VendorUninstallConfirmation,
    ) -> None:
        """Record all safe digest bindings and one durable user decision."""
        self._repository.record(
            AuditEvent(
                event_type=(
                    f"software.vendor_uninstall.{confirmation.tier.value}_confirmation_resolved"
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
                    "identity_digest": confirmation.identity_digest,
                    "vendor_identity_digest": confirmation.vendor_identity_digest,
                    "argument_digest": confirmation.argument_digest,
                    "executable_file_digest": confirmation.executable_file_digest,
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
        plan: VendorUninstallPlan,
        preview: VendorUninstallPreview,
        runtime_confirmation_id: str,
    ) -> None:
        """Append mandatory write-ahead audit without dangerous command content."""
        self._repository.record(
            AuditEvent(
                event_type="software.vendor_uninstall.started",
                original_request=plan.user_goal,
                plan_id=str(plan.plan_id),
                plan_version=plan.version,
                step_id=str(plan.operation_id),
                tool_name=plan.tool_name,
                parameters={
                    "transaction_id": str(plan.transaction_id),
                    "runtime_confirmation_id": runtime_confirmation_id,
                    "identity_digest": preview.identity_digest,
                    "vendor_identity_digest": preview.vendor_identity.invariant_digest(),
                    "argument_digest": (
                        preview.vendor_identity.argument_assessment.argument_fingerprint
                    ),
                    "arguments_source": "validated_local_registry_metadata",
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
        plan: VendorUninstallPlan,
        report: VendorUninstallExecutionReport,
    ) -> None:
        """Record process and fresh inventory states as separate observed facts."""
        self._repository.record(
            AuditEvent(
                event_type="software.vendor_uninstall.completed",
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
                    "process_id": report.process.process_id,
                    "tracked_child_count": report.process.tracked_child_count,
                    "shell_used": False,
                    "elevation_requested_by_agent": False,
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
                duration_ms=report.process.duration_ms,
            )
        )

    def failed(
        self,
        plan: VendorUninstallPlan,
        *,
        phase: str,
        error_code: str,
        mutation_may_have_started: bool,
    ) -> None:
        """Record a sanitized failure without exception text or retry."""
        self._repository.record(
            AuditEvent(
                event_type="software.vendor_uninstall.failed",
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
