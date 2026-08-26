"""Privacy-minimized audit events for the Stage 4X1 privileged protocol."""

from __future__ import annotations

from uuid import UUID

from pydantic import JsonValue

from pc_manager_agent.audit.models import AuditEvent
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.domain.privileged_actions import (
    BrokerDecision,
    PrivilegedActionEnvelope,
    PrivilegedActionResult,
)
from pc_manager_agent.persistence.privileged_actions import nonce_fingerprint


class PrivilegedActionAuditLogger:
    """Record Main/Broker events without payloads, nonces, keys, or caller secrets."""

    def __init__(
        self,
        repository: AuditRepository,
        *,
        app_version: str,
        git_commit: str | None = None,
    ) -> None:
        self._repository = repository
        self._app_version = app_version
        self._git_commit = git_commit

    def authorized(self, envelope: PrivilegedActionEnvelope) -> UUID:
        """Record signed request creation after both confirmations are approved."""
        request = envelope.request
        event = AuditEvent(
            event_type="MAIN_AUTHORIZATION_EVENT",
            plan_id=str(request.plan_id),
            agent_decision="SIGNED_MOCK_ONLY_PRIVILEGED_REQUEST",
            risk_level=request.risk_level,
            confirmation_required=True,
            confirmation_result="APPROVED_NOT_CONSUMED",
            parameters=self._safe_parameters(envelope),
            app_version=self._app_version,
            git_commit=self._git_commit,
        )
        self._repository.record(event)
        return event.event_id

    def malformed(self, decision: BrokerDecision) -> UUID:
        """Record a request rejected before a trusted request identity was available."""
        event = AuditEvent(
            event_type="BROKER_VALIDATION_EVENT",
            agent_decision=decision.value,
            confirmation_required=True,
            confirmation_result="REJECTED_BEFORE_PARSE",
            parameters={"request_identity_available": False},
            app_version=self._app_version,
            git_commit=self._git_commit,
        )
        self._repository.record(event)
        return event.event_id

    def validation(
        self,
        envelope: PrivilegedActionEnvelope,
        decision: BrokerDecision,
    ) -> UUID:
        """Record Broker validation before any atomic consumption or Mock execution."""
        request = envelope.request
        event = AuditEvent(
            event_type="BROKER_VALIDATION_EVENT",
            plan_id=str(request.plan_id),
            agent_decision=decision.value,
            risk_level=request.risk_level,
            confirmation_required=True,
            confirmation_result=(
                "VALID_NOT_CONSUMED"
                if decision is BrokerDecision.APPROVED_FOR_MOCK_EXECUTION
                else "REJECTED"
            ),
            parameters=self._safe_parameters(envelope),
            app_version=self._app_version,
            git_commit=self._git_commit,
        )
        self._repository.record(event)
        return event.event_id

    def execution(self, envelope: PrivilegedActionEnvelope, *, started: bool) -> UUID:
        """Record whether the fake executor was reached after durable consumption."""
        request = envelope.request
        event = AuditEvent(
            event_type="BROKER_EXECUTION_EVENT",
            plan_id=str(request.plan_id),
            agent_decision="MOCK_EXECUTION_STARTED" if started else "MOCK_EXECUTION_BLOCKED",
            risk_level=request.risk_level,
            confirmation_required=True,
            confirmation_result="CONSUMED",
            parameters=self._safe_parameters(envelope),
            result={"mock_only": True, "execution_started": started},
            app_version=self._app_version,
            git_commit=self._git_commit,
        )
        self._repository.record(event)
        return event.event_id

    def verification(
        self,
        envelope: PrivilegedActionEnvelope,
        result: PrivilegedActionResult,
    ) -> UUID:
        """Record the final Mock result and verification state."""
        request = envelope.request
        event = AuditEvent(
            event_type="BROKER_VERIFICATION_EVENT",
            plan_id=str(request.plan_id),
            agent_decision=result.broker_decision.value,
            risk_level=request.risk_level,
            confirmation_required=True,
            confirmation_result="CONSUMED",
            parameters=self._safe_parameters(envelope),
            result={
                "mock_only": True,
                "execution_status": result.execution_status.value,
                "result_code": result.result_code,
            },
            verification={"status": result.verification_status.value},
            app_version=self._app_version,
            git_commit=self._git_commit,
        )
        self._repository.record(event)
        return event.event_id

    @staticmethod
    def _safe_parameters(envelope: PrivilegedActionEnvelope) -> dict[str, JsonValue]:
        request = envelope.request
        return {
            "request_id": str(request.request_id),
            "protocol_version": request.protocol_version,
            "agent_instance_id": str(request.agent_instance_id),
            "action_type": request.action_type.value,
            "target_identity_hash": request.target_identity_hash,
            "plan_hash": request.plan_hash,
            "preview_hash": request.preview_hash,
            "plan_confirmation_id": str(request.plan_confirmation_id),
            "confirmation_id": str(request.confirmation_id),
            "risk_level": request.risk_level.value,
            "privilege_requirement": request.privilege_requirement.value,
            "request_digest": envelope.request_digest,
            "nonce_fingerprint": nonce_fingerprint(request.nonce),
            "created_at": request.created_at.isoformat(),
            "expires_at": request.expires_at.isoformat(),
        }
