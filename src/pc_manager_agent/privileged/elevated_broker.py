"""One-shot deterministic Stage 4X2 Broker execution core."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from pc_manager_agent import __version__
from pc_manager_agent.audit.elevated_broker import (
    ElevatedBrokerAuditContext,
    ElevatedBrokerAuditLogger,
)
from pc_manager_agent.audit.repository import AuditUnavailableError
from pc_manager_agent.confirmation.privileged_actions import PrivilegedConfirmationState
from pc_manager_agent.domain.elevated_broker import (
    BrokerBinaryIdentity,
    ElevatedBrokerResult,
    ElevatedBrokerResultEnvelope,
    ElevatedExecutionStatus,
    ElevatedVerificationStatus,
    WindowsProcessIdentity,
    canonical_broker_bytes,
)
from pc_manager_agent.domain.privileged_actions import (
    BrokerDecision,
    PrivilegedActionEnvelope,
    PrivilegedActionType,
    PrivilegedExecutionMode,
    PrivilegedReplayState,
    PrivilegedTransactionState,
)
from pc_manager_agent.persistence.privileged_actions import (
    PrivilegedActionRepository,
    PrivilegedActionStoreError,
    PrivilegedAuthorizationSnapshot,
    PrivilegedReplayError,
    nonce_fingerprint,
)
from pc_manager_agent.privileged.broker_identity import BrokerTrustError, BrokerTrustPolicy
from pc_manager_agent.privileged.ipc_protocol import IpcSessionAuthenticator
from pc_manager_agent.privileged.revalidation import (
    PrivilegedRevalidationError,
)
from pc_manager_agent.privileged.serialization import PrivilegedRequestSerializer
from pc_manager_agent.privileged.service_handler import WindowsServicePrivilegedHandler
from pc_manager_agent.tools.manifest import CancellationToken


class ElevatedBrokerExecutionError(RuntimeError):
    """Fail-closed Broker core error before a result can be authenticated."""


class ElevatedPrivilegedBroker:
    """Validate, consume, execute, verify, audit, and sign one exact service action."""

    def __init__(
        self,
        serializer: PrivilegedRequestSerializer,
        repository: PrivilegedActionRepository,
        handler: WindowsServicePrivilegedHandler,
        audit: ElevatedBrokerAuditLogger,
        trust_policy: BrokerTrustPolicy,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._serializer = serializer
        self._repository = repository
        self._handler = handler
        self._audit = audit
        self._trust = trust_policy
        self._now = now or (lambda: datetime.now(UTC))

    def dispatch(
        self,
        envelope: PrivilegedActionEnvelope,
        *,
        session: IpcSessionAuthenticator,
        broker_instance_id: UUID,
        caller_identity: WindowsProcessIdentity,
        expected_caller_identity: WindowsProcessIdentity,
        broker_identity: WindowsProcessIdentity,
        expected_broker_binary: BrokerBinaryIdentity,
    ) -> ElevatedBrokerResultEnvelope:
        """Perform all gates in a fixed order and never reopen a consumed request."""
        request = envelope.request
        self._trust.require_binary(expected_broker_binary)
        self._trust.require_pipe_peers(
            caller=caller_identity,
            expected_caller=expected_caller_identity,
            broker=broker_identity,
            expected_broker_binary=expected_broker_binary,
        )
        self._require_transport_integrity(envelope, session)
        audit_context = ElevatedBrokerAuditContext(
            caller_sid_fingerprint=hashlib.sha256(
                caller_identity.user_sid.encode("utf-8")
            ).hexdigest(),
            session_id=caller_identity.session_id,
            broker_executable_identity=expected_broker_binary.canonical_digest(),
            broker_version=__version__,
        )
        if request.action_type not in {
            PrivilegedActionType.SERVICE_START,
            PrivilegedActionType.SERVICE_STOP,
        }:
            return self._reject(
                envelope,
                broker_instance_id,
                session,
                BrokerDecision.ACTION_NOT_ALLOWLISTED,
                context=audit_context,
            )
        now = self._now()
        if now >= request.expires_at:
            return self._reject(
                envelope,
                broker_instance_id,
                session,
                BrokerDecision.REQUEST_EXPIRED,
                expired=True,
                context=audit_context,
            )
        try:
            snapshot = self._repository.snapshot(request)
        except (PrivilegedActionStoreError, PrivilegedReplayError):
            return self._reject(
                envelope,
                broker_instance_id,
                session,
                BrokerDecision.REPLAY_REJECTED,
                context=audit_context,
            )
        decision = self._binding_decision(envelope, snapshot, now)
        if decision is not None:
            return self._reject(
                envelope,
                broker_instance_id,
                session,
                decision,
                context=audit_context,
            )
        try:
            fresh = self._handler.require(request)
            self._require_preview_fresh(snapshot, fresh)
            self._audit.validation(
                envelope,
                broker_instance_id=broker_instance_id,
                decision=BrokerDecision.APPROVED_FOR_REAL_EXECUTION.value,
                context=audit_context,
            )
            self._repository.consume(request, now=now)
            # Every write gate is consumed before this second full validation. Drift now
            # fails without reopening the capability or either confirmation.
            final = self._handler.require(request)
            self._require_preview_fresh(snapshot, final)
            self._repository.transition(
                request.request_id,
                PrivilegedTransactionState.EXECUTING,
                PrivilegedReplayState.CONSUMED,
                result_code="REAL_EXECUTION_STARTING",
            )
            self._audit.execution_started(
                envelope,
                broker_instance_id=broker_instance_id,
                context=audit_context,
            )
        except PrivilegedRevalidationError as exc:
            if self._is_consumed(request.request_id):
                return self._finish_failure_after_consumption(
                    envelope,
                    broker_instance_id,
                    session,
                    exc.decision,
                    str(exc),
                    context=audit_context,
                )
            return self._reject(
                envelope,
                broker_instance_id,
                session,
                exc.decision,
                context=audit_context,
            )
        except (PrivilegedActionStoreError, AuditUnavailableError) as exc:
            if self._is_consumed(request.request_id):
                return self._finish_failure_after_consumption(
                    envelope,
                    broker_instance_id,
                    session,
                    BrokerDecision.PERSISTENCE_UNAVAILABLE,
                    "Mandatory persistence failed after authorization consumption",
                    context=audit_context,
                )
            raise ElevatedBrokerExecutionError(
                "Mandatory Broker persistence failed before execution"
            ) from exc

        started_at = self._now()
        dispatch_observed = False

        def mark_dispatched() -> None:
            nonlocal dispatch_observed
            dispatch_observed = True

        try:
            step = self._handler.execute(
                request,
                final,
                CancellationToken(),
                mark_dispatched,
            )
            self._repository.transition(
                request.request_id,
                PrivilegedTransactionState.VERIFYING,
                PrivilegedReplayState.CONSUMED,
                result_code="REAL_EXECUTION_DISPATCHED",
            )
            verified = self._handler.verify(request)
            success = step.control_dispatched and step.verified and verified is not None
            result = ElevatedBrokerResult(
                request_id=request.request_id,
                broker_instance_id=broker_instance_id,
                action_type=request.action_type,
                target_identity_hash=request.target_identity_hash,
                decision=(
                    BrokerDecision.APPROVED_FOR_REAL_EXECUTION.value
                    if success
                    else BrokerDecision.CLIENT_VERIFICATION_FAILED.value
                ),
                execution_status=(
                    ElevatedExecutionStatus.COMPLETED
                    if success
                    else (
                        ElevatedExecutionStatus.FAILED
                        if step.control_dispatched
                        else ElevatedExecutionStatus.NOT_STARTED
                    )
                ),
                verification_status=(
                    ElevatedVerificationStatus.VERIFIED
                    if success
                    else ElevatedVerificationStatus.FAILED
                ),
                execution_started=step.control_dispatched,
                pre_state=step.before_state,
                post_state=verified.state if verified else step.after_state,
                pre_state_hash=final.observation.state_digest(),
                post_state_hash=verified.state_digest() if verified else None,
                result_code="REAL_SERVICE_ACTION_VERIFIED" if success else "VERIFICATION_FAILED",
                message=(
                    "The exact SCM action and Broker postcondition were verified"
                    if success
                    else "The exact service postcondition could not be verified"
                ),
                started_at=started_at,
                completed_at=self._now(),
            )
        except Exception:
            result = ElevatedBrokerResult(
                request_id=request.request_id,
                broker_instance_id=broker_instance_id,
                action_type=request.action_type,
                target_identity_hash=request.target_identity_hash,
                decision=BrokerDecision.PRECONDITION_FAILED.value,
                execution_status=(
                    ElevatedExecutionStatus.INTERRUPTED
                    if dispatch_observed
                    else ElevatedExecutionStatus.NOT_STARTED
                ),
                verification_status=ElevatedVerificationStatus.UNCERTAIN,
                execution_started=dispatch_observed,
                pre_state=final.observation.state,
                pre_state_hash=final.observation.state_digest(),
                result_code="SCM_EXECUTION_INTERRUPTED",
                message="The Broker lost a complete SCM result; fresh reconciliation is required",
                started_at=started_at,
                completed_at=self._now(),
            )
        terminal = (
            PrivilegedTransactionState.COMPLETED
            if result.verification_status is ElevatedVerificationStatus.VERIFIED
            else PrivilegedTransactionState.FAILED
        )
        try:
            self._repository.transition(
                request.request_id,
                terminal,
                PrivilegedReplayState.CONSUMED,
                result_code=result.result_code,
            )
            self._audit.completion(envelope, result, context=audit_context)
        except (PrivilegedActionStoreError, AuditUnavailableError):
            result = result.model_copy(
                update={
                    "decision": BrokerDecision.PERSISTENCE_UNAVAILABLE.value,
                    "result_code": "FINAL_AUDIT_OR_STATE_UNAVAILABLE",
                    "message": (
                        "The service operation completed but final durable audit/state failed; "
                        "fresh reconciliation is required"
                    ),
                    "verification_status": ElevatedVerificationStatus.UNCERTAIN,
                }
            )
        return self._result(result, session)

    def _require_transport_integrity(
        self,
        envelope: PrivilegedActionEnvelope,
        session: IpcSessionAuthenticator,
    ) -> None:
        canonical = self._serializer.canonical_request_bytes(envelope.request)
        digest = hashlib.sha256(canonical).hexdigest()
        if digest != envelope.request_digest or not session.verify(canonical, envelope.integrity):
            raise BrokerTrustError("Privileged request session integrity is invalid")

    @staticmethod
    def _binding_decision(
        envelope: PrivilegedActionEnvelope,
        snapshot: PrivilegedAuthorizationSnapshot,
        now: datetime,
    ) -> BrokerDecision | None:
        request = envelope.request
        plan = snapshot.plan
        preview = snapshot.preview
        parent = snapshot.plan_confirmation
        runtime = snapshot.runtime_confirmation
        if (
            snapshot.transaction_state is not PrivilegedTransactionState.SIGNED
            or snapshot.replay_state is not PrivilegedReplayState.CREATED
        ):
            return BrokerDecision.REPLAY_REJECTED
        if (
            snapshot.stored_request_digest != envelope.request_digest
            or snapshot.nonce_fingerprint != nonce_fingerprint(request.nonce)
            or plan.plan_id != request.plan_id
            or plan.canonical_digest() != request.plan_hash
            or plan.action_type is not request.action_type
            or plan.payload_digest != request.payload_digest
            or plan.target_identity_hash != request.target_identity_hash
            or plan.object_summary_digest != request.object_summary_digest
        ):
            return BrokerDecision.PLAN_BINDING_INVALID
        if (
            preview.execution_mode is not PrivilegedExecutionMode.WINDOWS_ELEVATED
            or preview.mock_only
            or preview.preview_id != request.preview_id
            or preview.canonical_digest() != request.preview_hash
            or preview.plan_hash != request.plan_hash
            or preview.target_identity_hash != request.target_identity_hash
        ):
            return BrokerDecision.PREVIEW_BINDING_INVALID
        if (
            parent.confirmation_id != request.plan_confirmation_id
            or runtime.confirmation_id != request.confirmation_id
            or runtime.parent_confirmation_id != parent.confirmation_id
            or parent.state is not PrivilegedConfirmationState.APPROVED
            or runtime.state is not PrivilegedConfirmationState.APPROVED
            or now >= parent.expires_at
            or now >= runtime.expires_at
            or runtime.plan_hash != request.plan_hash
            or runtime.preview_hash != request.preview_hash
            or runtime.payload_digest != request.payload_digest
            or runtime.target_identity_hash != request.target_identity_hash
        ):
            return BrokerDecision.CONFIRMATION_INVALID
        return None

    @staticmethod
    def _require_preview_fresh(
        snapshot: PrivilegedAuthorizationSnapshot,
        fresh: object,
    ) -> None:
        from pc_manager_agent.privileged.service_handler import ValidatedServiceRequest

        if not isinstance(fresh, ValidatedServiceRequest):
            raise PrivilegedRevalidationError(
                BrokerDecision.PRECONDITION_FAILED,
                "Broker handler returned invalid fresh evidence",
            )
        if (
            snapshot.preview.target_state_hash != fresh.observation.state_digest()
            or snapshot.preview.safety_digest != fresh.safety_digest
            or fresh.dependency_digest != snapshot.plan.payload.expected_dependency_digest  # type: ignore[union-attr]
        ):
            raise PrivilegedRevalidationError(
                BrokerDecision.TARGET_CHANGED,
                "Fresh Preview, safety, or dependency evidence changed",
            )

    def _reject(
        self,
        envelope: PrivilegedActionEnvelope,
        broker_instance_id: UUID,
        session: IpcSessionAuthenticator,
        decision: BrokerDecision,
        *,
        expired: bool = False,
        context: ElevatedBrokerAuditContext | None = None,
    ) -> ElevatedBrokerResultEnvelope:
        try:
            self._repository.reject_unconsumed(
                envelope.request.request_id,
                result_code=decision.value,
                expired=expired,
                invalidate_confirmations=True,
            )
        except (PrivilegedActionStoreError, PrivilegedReplayError):
            decision = BrokerDecision.REPLAY_REJECTED
        result = self._not_started(
            envelope,
            broker_instance_id,
            decision,
            "The privileged request was rejected before SCM execution",
        )
        try:
            self._audit.validation(
                envelope,
                broker_instance_id=broker_instance_id,
                decision=decision.value,
                context=context,
            )
        except AuditUnavailableError:
            result = result.model_copy(
                update={
                    "decision": BrokerDecision.PERSISTENCE_UNAVAILABLE.value,
                    "result_code": BrokerDecision.PERSISTENCE_UNAVAILABLE.value,
                }
            )
        return self._result(result, session)

    def _finish_failure_after_consumption(
        self,
        envelope: PrivilegedActionEnvelope,
        broker_instance_id: UUID,
        session: IpcSessionAuthenticator,
        decision: BrokerDecision,
        message: str,
        *,
        context: ElevatedBrokerAuditContext | None = None,
    ) -> ElevatedBrokerResultEnvelope:
        result = self._not_started(envelope, broker_instance_id, decision, message)
        try:
            self._repository.transition(
                envelope.request.request_id,
                PrivilegedTransactionState.FAILED,
                PrivilegedReplayState.CONSUMED,
                result_code=decision.value,
            )
            self._audit.completion(envelope, result, context=context)
        except (PrivilegedActionStoreError, AuditUnavailableError):
            result = result.model_copy(
                update={
                    "decision": BrokerDecision.PERSISTENCE_UNAVAILABLE.value,
                    "result_code": BrokerDecision.PERSISTENCE_UNAVAILABLE.value,
                }
            )
        return self._result(result, session)

    def _is_consumed(self, request_id: UUID) -> bool:
        try:
            # A synthetic request is not available here; repository transitions after consume
            # are attempted by the caller. Treat any replay error on rejection as consumed.
            return self._repository.request_is_consumed(request_id)
        except PrivilegedActionStoreError:
            return True

    @staticmethod
    def _not_started(
        envelope: PrivilegedActionEnvelope,
        broker_instance_id: UUID,
        decision: BrokerDecision,
        message: str,
    ) -> ElevatedBrokerResult:
        return ElevatedBrokerResult(
            request_id=envelope.request.request_id,
            broker_instance_id=broker_instance_id,
            action_type=envelope.request.action_type,
            target_identity_hash=envelope.request.target_identity_hash,
            decision=decision.value,
            execution_status=ElevatedExecutionStatus.NOT_STARTED,
            verification_status=ElevatedVerificationStatus.NOT_RUN,
            result_code=decision.value,
            message=message,
            completed_at=datetime.now(UTC),
        )

    @staticmethod
    def _result(
        result: ElevatedBrokerResult,
        session: IpcSessionAuthenticator,
    ) -> ElevatedBrokerResultEnvelope:
        digest = result.canonical_digest()
        return ElevatedBrokerResultEnvelope(
            broker_instance_id=result.broker_instance_id,
            request_id=result.request_id,
            result=result,
            result_digest=digest,
            integrity=session.sign(canonical_broker_bytes(result.model_dump(mode="json"))),
        )
