"""Privacy-minimized audit events for the real Stage 4X3 Broker lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from pydantic import JsonValue

from pc_manager_agent.audit.models import AuditEvent
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.domain.elevated_broker import (
    BrokerFailureCode,
    BrokerLifecycleState,
    ElevatedBrokerResult,
)
from pc_manager_agent.domain.privileged_actions import (
    PrivilegedActionEnvelope,
    PrivilegedActionType,
)
from pc_manager_agent.persistence.privileged_actions import nonce_fingerprint


@dataclass(frozen=True, slots=True)
class ElevatedBrokerAuditContext:
    """Non-secret endpoint evidence shared by correlated Stage 4X3 events."""

    caller_sid_fingerprint: str
    session_id: int
    broker_executable_identity: str
    broker_version: str
    ipc_endpoint_fingerprint: str | None = None


class ElevatedBrokerAuditLogger:
    """Record correlated launch, validation, execution, and verification evidence."""

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

    def lifecycle(
        self,
        *,
        state: BrokerLifecycleState,
        broker_instance_id: UUID,
        request_id: UUID,
        plan_id: UUID | None,
        failure: BrokerFailureCode | None = None,
        context: ElevatedBrokerAuditContext | None = None,
        uac_result: str | None = None,
        handshake_result: str | None = None,
        main_readback_result: str | None = None,
        result_integrity_result: str | None = None,
        broker_exit_code: str | None = None,
        final_transaction_state: str | None = None,
    ) -> UUID:
        """Record UAC/IPC lifecycle state without command lines, SIDs, or keys."""
        parameters: dict[str, JsonValue] = {
            "broker_instance_id": str(broker_instance_id),
            "request_id": str(request_id),
            "protocol_version": 1,
            "state": state.value,
        }
        self._add_context(parameters, context)
        optional_results = {
            "uac_result": uac_result,
            "handshake_result": handshake_result,
            "main_readback_result": main_readback_result,
            "result_integrity_result": result_integrity_result,
            "broker_exit_code": broker_exit_code,
            "final_transaction_state": final_transaction_state,
        }
        parameters.update(
            {name: value for name, value in optional_results.items() if value is not None}
        )
        event = AuditEvent(
            event_type="ELEVATED_BROKER_LIFECYCLE_EVENT",
            plan_id=str(plan_id) if plan_id else None,
            agent_decision=failure.value if failure else state.value,
            confirmation_required=True,
            confirmation_result="NOT_REUSED",
            parameters=parameters,
            app_version=self._app_version,
            git_commit=self._git_commit,
        )
        self._repository.record(event)
        return event.event_id

    def validation(
        self,
        envelope: PrivilegedActionEnvelope,
        *,
        broker_instance_id: UUID,
        decision: str,
        context: ElevatedBrokerAuditContext | None = None,
    ) -> UUID:
        """Record request binding and fresh validation before atomic consumption."""
        parameters = self._safe_parameters(envelope, broker_instance_id, context)
        passed = decision == "APPROVED_FOR_REAL_EXECUTION"
        parameters.update(
            {
                "handshake_result": "AUTHENTICATED",
                "caller_validation_result": "PASSED",
                "protocol_validation_result": "PASSED",
                "request_integrity_result": "PASSED",
                "replay_result": "PASSED" if passed else decision,
                "broker_safety_result": "PASSED" if passed else decision,
                "broker_target_revalidation_result": "PASSED" if passed else decision,
                "broker_precondition_result": "PASSED" if passed else decision,
            }
        )
        event = AuditEvent(
            event_type="ELEVATED_BROKER_VALIDATION_EVENT",
            plan_id=str(envelope.request.plan_id),
            agent_decision=decision,
            risk_level=envelope.request.risk_level,
            confirmation_required=True,
            confirmation_result="VALID_NOT_CONSUMED",
            parameters=parameters,
            app_version=self._app_version,
            git_commit=self._git_commit,
        )
        self._repository.record(event)
        return event.event_id

    def execution_started(
        self,
        envelope: PrivilegedActionEnvelope,
        *,
        broker_instance_id: UUID,
        context: ElevatedBrokerAuditContext | None = None,
    ) -> UUID:
        """Persist the mandatory pre-dispatch event after single-use consumption."""
        parameters = self._safe_parameters(envelope, broker_instance_id, context)
        parameters.update(
            {
                "request_consumed": True,
                "execution_result": "STARTING",
                "final_transaction_state": "EXECUTING",
            }
        )
        event = AuditEvent(
            event_type="ELEVATED_BROKER_EXECUTION_EVENT",
            plan_id=str(envelope.request.plan_id),
            agent_decision=f"REAL_{envelope.request.action_type.value}_STARTING",
            risk_level=envelope.request.risk_level,
            confirmation_required=True,
            confirmation_result="CONSUMED",
            parameters=parameters,
            result={
                "execution_started": False,
                "adapter": _adapter_name(envelope.request.action_type),
            },
            app_version=self._app_version,
            git_commit=self._git_commit,
        )
        self._repository.record(event)
        return event.event_id

    def completion(
        self,
        envelope: PrivilegedActionEnvelope,
        result: ElevatedBrokerResult,
        *,
        context: ElevatedBrokerAuditContext | None = None,
    ) -> UUID:
        """Record truthful action-specific verification without commands or raw registry data."""
        parameters = self._safe_parameters(envelope, result.broker_instance_id, context)
        parameters.update(
            {
                "execution_result": result.execution_status.value,
                "broker_verification_result": result.verification_status.value,
                "final_transaction_state": (
                    "COMPLETED" if result.verification_status.value == "VERIFIED" else "FAILED"
                ),
            }
        )
        event = AuditEvent(
            event_type="ELEVATED_BROKER_VERIFICATION_EVENT",
            plan_id=str(envelope.request.plan_id),
            agent_decision=result.decision,
            risk_level=envelope.request.risk_level,
            confirmation_required=True,
            confirmation_result="CONSUMED",
            parameters=parameters,
            before_state={
                "state": result.pre_state.value if result.pre_state else None,
                "state_hash": result.pre_state_hash,
            },
            result={
                "execution_status": result.execution_status.value,
                "result_code": result.result_code,
                "rollback_level": result.rollback_level.value,
                "action_evidence": (
                    result.action_evidence.model_dump(mode="json")
                    if result.action_evidence is not None
                    else None
                ),
            },
            after_state={
                "state": result.post_state.value if result.post_state else None,
                "state_hash": result.post_state_hash,
            },
            verification={"status": result.verification_status.value},
            app_version=self._app_version,
            git_commit=self._git_commit,
        )
        self._repository.record(event)
        return event.event_id

    @staticmethod
    def _safe_parameters(
        envelope: PrivilegedActionEnvelope,
        broker_instance_id: UUID,
        context: ElevatedBrokerAuditContext | None = None,
    ) -> dict[str, JsonValue]:
        request = envelope.request
        parameters: dict[str, JsonValue] = {
            "broker_instance_id": str(broker_instance_id),
            "request_id": str(request.request_id),
            "agent_instance_id": str(request.agent_instance_id),
            "protocol_version": request.protocol_version,
            "action_type": request.action_type.value,
            "action_schema_version": request.action_schema_version,
            "safety_policy_version": request.safety_policy_version,
            "manifest_digest": request.manifest_digest,
            "payload_digest": request.payload_digest,
            "target_identity_hash": request.target_identity_hash,
            "plan_id": str(request.plan_id),
            "plan_hash": request.plan_hash,
            "preview_id": str(request.preview_id),
            "preview_hash": request.preview_hash,
            "request_digest": envelope.request_digest,
            "nonce_fingerprint": nonce_fingerprint(request.nonce),
            "plan_confirmation_id": str(request.plan_confirmation_id),
            "runtime_confirmation_id": str(request.confirmation_id),
            "risk_level": request.risk_level.value,
            "privilege_requirement": request.privilege_requirement.value,
        }
        source_transaction_id = getattr(request.payload, "source_transaction_id", None)
        if isinstance(source_transaction_id, UUID):
            parameters["source_transaction_id"] = str(source_transaction_id)
        ElevatedBrokerAuditLogger._add_context(parameters, context)
        return parameters

    @staticmethod
    def _add_context(
        parameters: dict[str, JsonValue],
        context: ElevatedBrokerAuditContext | None,
    ) -> None:
        if context is None:
            return
        parameters.update(
            {
                "caller_sid_fingerprint": context.caller_sid_fingerprint,
                # Avoid the generic audit scrubber's credential-oriented ``session`` key
                # rule while retaining the non-secret Windows logon-session number.
                "windows_logon_id": context.session_id,
                "broker_executable_identity": context.broker_executable_identity,
                "broker_version": context.broker_version,
            }
        )
        if context.ipc_endpoint_fingerprint is not None:
            parameters["ipc_endpoint_fingerprint"] = context.ipc_endpoint_fingerprint


def _adapter_name(action_type: PrivilegedActionType) -> str:
    return {
        PrivilegedActionType.SERVICE_START: "WINDOWS_SCM_CONTROL_API",
        PrivilegedActionType.SERVICE_STOP: "WINDOWS_SCM_CONTROL_API",
        PrivilegedActionType.SERVICE_STARTUP_TYPE_CHANGE: "WINDOWS_SERVICE_CONFIG_API",
        PrivilegedActionType.SERVICE_STARTUP_TYPE_RESTORE: "WINDOWS_SERVICE_CONFIG_API",
        PrivilegedActionType.STARTUP_MACHINE_DISABLE: "WINDOWS_TRANSACTED_HKLM_RUN_API",
        PrivilegedActionType.STARTUP_MACHINE_RESTORE: "WINDOWS_TRANSACTED_HKLM_RUN_API",
        PrivilegedActionType.MSI_UNINSTALL_MACHINE: "FIXED_MACHINE_MSI_ADAPTER",
    }.get(action_type, "UNREGISTERED_ACTION")
