"""Privacy-minimized audit trail for Stage 4D1 zero-execution analysis."""

from __future__ import annotations

from pc_manager_agent.audit.models import AuditEvent
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.confirmation.models import ConfirmationRequest
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.software_uninstall_analysis import (
    ResolvedSoftwareTarget,
    SoftwareInventory,
    SoftwareSafetyDecision,
    SoftwareTargetAcknowledgement,
    SoftwareUninstallAnalysisPlan,
    SoftwareUninstallPreview,
)


class SoftwareUninstallAnalysisAuditLogger:
    """Record identity fingerprints and decisions, never raw commands or registry paths."""

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

    def plan_reviewed(
        self,
        plan: SoftwareUninstallAnalysisPlan,
        approved: bool,
        issues: tuple[str, ...],
    ) -> None:
        """Record the exact R0 plan and independent validation result."""
        self._repository.record(
            AuditEvent(
                event_type="software.plan.reviewed",
                original_request=plan.user_goal,
                plan={
                    "plan_id": str(plan.plan_id),
                    "plan_digest": plan.canonical_digest(),
                    "tool_names": list(plan.tool_names),
                    "max_items": plan.max_items,
                    "target_query": plan.target_query.model_dump(mode="json"),
                },
                plan_id=str(plan.plan_id),
                plan_version=plan.version,
                agent_decision="approved" if approved else "rejected",
                risk_level=RiskLevel.R0,
                confirmation_required=True,
                result={"issues": list(issues), "execution_performed": False},
                verification={"read_only": True, "registered_execution_tools": 0},
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )

    def plan_confirmation(
        self,
        plan: SoftwareUninstallAnalysisPlan,
        confirmation: ConfirmationRequest,
    ) -> None:
        """Record the plan decision and immutable digest without granting execution."""
        self._repository.record(
            AuditEvent(
                event_type="software.plan_confirmation.resolved",
                plan_id=str(plan.plan_id),
                plan_version=plan.version,
                parameters={
                    "confirmation_id": str(confirmation.confirmation_id),
                    "plan_digest": confirmation.plan_digest,
                },
                risk_level=RiskLevel.R0,
                confirmation_required=True,
                confirmation_result=confirmation.state.value,
                result={"execution_performed": False},
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )

    def inventory_completed(
        self,
        plan: SoftwareUninstallAnalysisPlan,
        inventory: SoftwareInventory,
    ) -> None:
        """Record source counts and partial state, not installed-software names or commands."""
        self._repository.record(
            AuditEvent(
                event_type="software.inventory.completed",
                plan_id=str(plan.plan_id),
                plan_version=plan.version,
                tool_name="software.inventory",
                parameters={"max_items": plan.max_items},
                risk_level=RiskLevel.R0,
                confirmation_required=True,
                confirmation_result="APPROVED",
                result={
                    "item_count": len(inventory.entries),
                    "source_counts": inventory.source_counts,
                    "warning_count": len(inventory.warnings),
                    "truncated": inventory.truncated,
                    "execution_performed": False,
                },
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )

    def target_resolved(
        self,
        plan: SoftwareUninstallAnalysisPlan,
        resolution: ResolvedSoftwareTarget,
    ) -> None:
        """Record a selected digest or candidate count without source paths."""
        selected_digest = (
            resolution.selected.identity.canonical_digest() if resolution.selected else None
        )
        self._repository.record(
            AuditEvent(
                event_type=(
                    "software.target.ambiguous"
                    if resolution.ambiguous
                    else "software.target.resolved"
                ),
                plan_id=str(plan.plan_id),
                plan_version=plan.version,
                tool_name="software.resolve",
                parameters={
                    "selected_identity_digest": selected_digest,
                    "candidate_count": len(resolution.candidates),
                },
                risk_level=RiskLevel.R0,
                confirmation_required=True,
                confirmation_result="APPROVED",
                result={"reason": resolution.reason, "execution_performed": False},
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )

    def previewed(
        self,
        plan: SoftwareUninstallAnalysisPlan,
        preview: SoftwareUninstallPreview,
    ) -> None:
        """Record sanitized Preview evidence and the mandatory execution=false assertion."""
        self._repository.record(
            AuditEvent(
                event_type=(
                    "software.preview.blocked" if preview.blocked else "software.preview.created"
                ),
                original_request=plan.user_goal,
                plan_id=str(plan.plan_id),
                plan_version=plan.version,
                tool_name="software.uninstall_preview",
                parameters={
                    "preview_id": str(preview.preview_id),
                    "identity_digest": preview.identity_digest,
                    "metadata_digest": preview.metadata_digest,
                    "capability_digest": preview.capability_digest,
                    "capability_type": preview.capability.capability_type.value,
                    "safety_class": preview.safety.safety_class.value,
                    "impact_finding_count": len(preview.impact.findings),
                },
                risk_level=RiskLevel.R0,
                confirmation_required=True,
                confirmation_result="APPROVED",
                agent_decision=(
                    "blocked"
                    if preview.safety.decision is SoftwareSafetyDecision.BLOCKED
                    else "preview-only"
                ),
                result={
                    "blocked": preview.blocked,
                    "execution_performed": False,
                    "executable_in_current_stage": False,
                    "expires_at": preview.expires_at.isoformat(),
                },
                rollback={
                    "analysis_level": "NONE",
                    "future_recovery_level": preview.future_recovery_level.value,
                    "automatic_uninstall_undo": False,
                },
                verification={"read_only": True, "system_changes": 0},
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )

    def acknowledgement_resolved(
        self,
        plan: SoftwareUninstallAnalysisPlan,
        acknowledgement: SoftwareTargetAcknowledgement,
    ) -> None:
        """Record understanding/rejection and explicitly state that no authority was created."""
        self._repository.record(
            AuditEvent(
                event_type="software.target_acknowledgement.resolved",
                plan_id=str(plan.plan_id),
                plan_version=plan.version,
                parameters={
                    "acknowledgement_id": str(acknowledgement.acknowledgement_id),
                    "preview_id": str(acknowledgement.preview_id),
                    "preview_digest": acknowledgement.preview_digest,
                    "identity_digest": acknowledgement.identity_digest,
                },
                risk_level=RiskLevel.R0,
                confirmation_required=True,
                confirmation_result=acknowledgement.state.value,
                result={
                    "execution_authorization_created": False,
                    "execution_performed": False,
                    "workflow_stopped": True,
                },
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )

    def failed(
        self,
        plan: SoftwareUninstallAnalysisPlan,
        phase: str,
        error_code: str,
    ) -> None:
        """Record a sanitized fail-closed boundary without retaining exception text."""
        self._repository.record(
            AuditEvent(
                event_type="software.analysis.failed",
                plan_id=str(plan.plan_id),
                plan_version=plan.version,
                parameters={"phase": phase},
                risk_level=RiskLevel.R0,
                confirmation_required=True,
                error={"code": error_code, "execution_performed": False},
                verification={"system_changes": 0},
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )
