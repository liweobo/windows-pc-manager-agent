"""Privacy-minimized Stage 5C browser audit events."""

from __future__ import annotations

import hashlib

from pc_manager_agent import __version__
from pc_manager_agent.audit.models import AuditEvent
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.domain.browser import BrowserActionResult, BrowserObservation
from pc_manager_agent.domain.browser_downloads import (
    BrowserDownloadIdentity,
    BrowserDownloadPreview,
    BrowserDownloadRecoveryRecord,
)
from pc_manager_agent.domain.browser_plans import BrowserTaskPlan
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.safety.browser.network import BrowserUrlRedactor


class BrowserAuditLogger:
    """Record origins, counts, fixed codes, and digests without page bodies or URL secrets."""

    def __init__(self, repository: AuditRepository, git_commit: str | None = None) -> None:
        self._repository = repository
        self._git_commit = git_commit
        self._redactor = BrowserUrlRedactor()

    def plan_reviewed(self, plan: BrowserTaskPlan) -> None:
        """Record the deterministic plan without raw query text or page content."""
        self._repository.record(
            AuditEvent(
                event_type="browser.plan.reviewed",
                plan_id=str(plan.plan_id),
                plan_version=plan.version,
                agent_decision=plan.policy.decision.value,
                risk_level=plan.policy.risk_level,
                confirmation_required=plan.requires_plan_confirmation,
                parameters={
                    "session_id": str(plan.action.session_id),
                    "action": plan.action.kind.value,
                    "origin": plan.allowed_origin,
                    "action_digest": plan.action.canonical_digest(),
                    "profile_persistent": False,
                    "model_authority": False,
                },
                app_version=__version__,
                git_commit=self._git_commit,
            )
        )

    def confirmation_resolved(self, plan: BrowserTaskPlan, approved: bool) -> None:
        """Record approval state without storing user-entered search or form values."""
        self._repository.record(
            AuditEvent(
                event_type="browser.confirmation.resolved",
                plan_id=str(plan.plan_id),
                plan_version=plan.version,
                risk_level=plan.policy.risk_level,
                confirmation_required=True,
                confirmation_result="APPROVED" if approved else "REJECTED",
                parameters={"action_digest": plan.action.canonical_digest()},
                app_version=__version__,
                git_commit=self._git_commit,
            )
        )

    def observation(self, observation: BrowserObservation) -> None:
        """Record only a redacted URL, structural counts, and injection-signal enums."""
        self._repository.record(
            AuditEvent(
                event_type="browser.page.observed",
                risk_level=RiskLevel.R0,
                parameters={
                    "session_id": str(observation.session_id),
                    "page_id": str(observation.page_id),
                    "navigation_id": str(observation.navigation_id),
                    "url": self._redactor.redact(observation.url),
                    "visible_character_count": len(observation.visible_text),
                    "element_count": len(observation.elements),
                    "prompt_injection_signals": [
                        signal.value for signal in observation.prompt_injection_signals
                    ],
                    "truncated": observation.truncated,
                },
                verification={"content_trust": observation.trust.value},
                app_version=__version__,
                git_commit=self._git_commit,
            )
        )

    def action_completed(self, plan: BrowserTaskPlan, result: BrowserActionResult) -> None:
        """Record verified status and reason code, never page content."""
        self._repository.record(
            AuditEvent(
                event_type="browser.action.completed",
                plan_id=str(plan.plan_id),
                plan_version=plan.version,
                risk_level=plan.policy.risk_level,
                confirmation_required=plan.requires_plan_confirmation,
                confirmation_result="APPROVED" if plan.requires_plan_confirmation else None,
                result={
                    "action_id": str(result.action_id),
                    "completed": result.completed,
                    "reason_code": result.reason_code,
                },
                verification={"fresh_observation": result.observation is not None},
                app_version=__version__,
                git_commit=self._git_commit,
            )
        )

    def download_completed(
        self,
        preview: BrowserDownloadPreview,
        identity: BrowserDownloadIdentity,
    ) -> None:
        """Record content identity and aggregate impact without a local path or URL query."""
        self._repository.record(
            AuditEvent(
                event_type="browser.download.completed",
                plan_id=None,
                risk_level=RiskLevel.R1,
                confirmation_required=True,
                confirmation_result="APPROVED",
                parameters={
                    "preview_id": str(preview.preview_id),
                    "origin": preview.source_origin,
                    "filename_digest": hashlib.sha256(
                        preview.suggested_filename.encode("utf-8")
                    ).hexdigest(),
                    "mime_type": identity.detected_mime_type,
                },
                result={"size_bytes": identity.size_bytes, "sha256": identity.sha256},
                rollback={"level": "FULL", "valid_only_if_unchanged": True},
                verification={"extension_mime_magic_size_consistent": True},
                app_version=__version__,
                git_commit=self._git_commit,
            )
        )

    def download_rolled_back(self, record: BrowserDownloadRecoveryRecord) -> None:
        """Record conditional recovery without storing either local path."""
        self._repository.record(
            AuditEvent(
                event_type="browser.download.rolled_back",
                risk_level=RiskLevel.R1,
                confirmation_required=True,
                confirmation_result="APPROVED",
                parameters={
                    "operation_id": str(record.operation_id),
                    "download_id": str(record.download.download_id),
                },
                rollback={"level": "FULL", "completed": True},
                verification={"sha256_unchanged": True, "no_overwrite": True},
                app_version=__version__,
                git_commit=self._git_commit,
            )
        )
