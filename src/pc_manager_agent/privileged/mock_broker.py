"""Complete Stage 4X1 validation pipeline with fake execution only."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import BaseModel

from pc_manager_agent.audit.privileged_actions import PrivilegedActionAuditLogger
from pc_manager_agent.audit.repository import AuditUnavailableError
from pc_manager_agent.domain.privileged_actions import (
    BrokerDecision,
    MockExecutionStatus,
    PrivilegedActionEnvelope,
    PrivilegedActionPreview,
    PrivilegedActionRequest,
    PrivilegedActionResult,
    PrivilegedActionResultEnvelope,
    PrivilegedCallerContext,
    PrivilegedReplayState,
    PrivilegedTransactionState,
    PrivilegedVerificationStatus,
)
from pc_manager_agent.persistence.privileged_actions import (
    PrivilegedActionRepository,
    PrivilegedActionStoreError,
    PrivilegedAuthorizationSnapshot,
    PrivilegedReplayError,
    PrivilegedRequestReplayStore,
    nonce_fingerprint,
)
from pc_manager_agent.privileged.authentication import PrivilegedRequestAuthenticator
from pc_manager_agent.privileged.registry import (
    PrivilegedActionRegistry,
    PrivilegedActionRegistryError,
)
from pc_manager_agent.privileged.revalidation import (
    FakePrivilegedService,
    PrivilegedRevalidationError,
)
from pc_manager_agent.privileged.serialization import (
    PrivilegedRequestSerializer,
    PrivilegedRequestTooLargeError,
    PrivilegedSerializationError,
    UnsupportedProtocolVersionError,
)


class MockPrivilegedBroker:
    """Validate, atomically consume, mutate fake state, verify, and audit."""

    def __init__(
        self,
        serializer: PrivilegedRequestSerializer,
        authenticator: PrivilegedRequestAuthenticator,
        repository: PrivilegedActionRepository,
        replay_store: PrivilegedRequestReplayStore,
        registry: PrivilegedActionRegistry,
        audit: PrivilegedActionAuditLogger,
        *,
        now: Callable[[], datetime] | None = None,
        before_final_revalidation: Callable[[PrivilegedActionRequest], None] | None = None,
    ) -> None:
        self._serializer = serializer
        self._authenticator = authenticator
        self._repository = repository
        self._replay = replay_store
        self._registry = registry
        self._audit = audit
        self._now = now or (lambda: datetime.now(UTC))
        self._before_final_revalidation = before_final_revalidation

    def dispatch(
        self,
        serialized: bytes,
        authenticated_caller: PrivilegedCallerContext,
    ) -> PrivilegedActionResultEnvelope:
        """Run the full Broker pipeline without invoking Windows or an LLM."""
        try:
            envelope = self._serializer.deserialize(serialized)
        except PrivilegedRequestTooLargeError:
            return self._malformed(BrokerDecision.REQUEST_TOO_LARGE)
        except UnsupportedProtocolVersionError:
            return self._malformed(BrokerDecision.UNSUPPORTED_PROTOCOL_VERSION)
        except PrivilegedSerializationError:
            return self._malformed(BrokerDecision.SCHEMA_INVALID)

        request = envelope.request
        canonical = self._serializer.canonical_request_bytes(request)
        if hashlib.sha256(canonical).hexdigest() != envelope.request_digest or not (
            self._authenticator.verify(canonical, envelope.integrity)
        ):
            return self._reject(envelope, BrokerDecision.INTEGRITY_INVALID, mutate=False)
        now = self._now()
        if now >= request.expires_at:
            return self._reject(
                envelope,
                BrokerDecision.REQUEST_EXPIRED,
                mutate=True,
                expired=True,
            )
        try:
            snapshot = self._replay.inspect(request)
        except PrivilegedReplayError:
            return self._reject(envelope, BrokerDecision.REPLAY_REJECTED, mutate=False)
        except PrivilegedActionStoreError:
            return self._reject(envelope, BrokerDecision.PERSISTENCE_UNAVAILABLE, mutate=False)
        if snapshot.replay_state is not PrivilegedReplayState.CREATED:
            return self._reject(envelope, BrokerDecision.REPLAY_REJECTED, mutate=False)
        if snapshot.transaction_state is not PrivilegedTransactionState.SIGNED:
            if (
                snapshot.plan_confirmation.state.value == "EXPIRED"
                or snapshot.runtime_confirmation.state.value == "EXPIRED"
            ):
                return self._reject(envelope, BrokerDecision.CONFIRMATION_INVALID, mutate=False)
            return self._reject(envelope, BrokerDecision.REPLAY_REJECTED, mutate=False)
        try:
            manifest = self._registry.require(request.action_type)
        except PrivilegedActionRegistryError:
            return self._reject(envelope, BrokerDecision.ACTION_NOT_ALLOWLISTED, mutate=True)
        decision = self._binding_decision(
            envelope,
            authenticated_caller,
            snapshot,
            manifest.payload_model,
            now,
        )
        if decision is not None:
            return self._reject(envelope, decision, mutate=True)
        try:
            fresh = manifest.handler.require(request)
            self._require_preview_fresh(snapshot.preview, fresh)
        except PrivilegedRevalidationError as exc:
            return self._reject(envelope, exc.decision, mutate=True)

        # Mandatory pre-consumption audit: an unavailable audit database grants no authority.
        try:
            self._audit.validation(envelope, BrokerDecision.APPROVED_FOR_MOCK_EXECUTION)
        except AuditUnavailableError:
            return self._reject(envelope, BrokerDecision.PERSISTENCE_UNAVAILABLE, mutate=False)
        try:
            self._replay.consume(request, now=now)
            self._repository.transition(
                request.request_id,
                PrivilegedTransactionState.EXECUTING,
                PrivilegedReplayState.CONSUMING,
                result_code="MOCK_EXECUTION_RESERVED",
            )
        except PrivilegedReplayError:
            return self._reject(envelope, BrokerDecision.REPLAY_REJECTED, mutate=False)
        except PrivilegedActionStoreError:
            return self._reject(envelope, BrokerDecision.PERSISTENCE_UNAVAILABLE, mutate=False)

        pre_state_hash = fresh.state_digest()
        try:
            if self._before_final_revalidation is not None:
                self._before_final_revalidation(request)
            final_fresh = manifest.handler.require(request)
            self._require_preview_fresh(snapshot.preview, final_fresh)
        except PrivilegedRevalidationError as exc:
            self._repository.transition(
                request.request_id,
                PrivilegedTransactionState.REJECTED,
                PrivilegedReplayState.CONSUMED,
                result_code=exc.decision.value,
            )
            try:
                self._audit.execution(envelope, started=False)
            except AuditUnavailableError:
                return self._result_envelope(
                    PrivilegedActionResult(
                        request_id=request.request_id,
                        action_type=request.action_type,
                        broker_decision=BrokerDecision.PERSISTENCE_UNAVAILABLE,
                        pre_state_hash=pre_state_hash,
                        result_code=BrokerDecision.PERSISTENCE_UNAVAILABLE.value,
                        message="Final TOCTOU rejection audit failed",
                    )
                )
            return self._finish(
                envelope,
                PrivilegedActionResult(
                    request_id=request.request_id,
                    action_type=request.action_type,
                    broker_decision=exc.decision,
                    pre_state_hash=pre_state_hash,
                    result_code=exc.decision.value,
                    message=(
                        "Final Mock TOCTOU revalidation failed; the single-use request was consumed"
                    ),
                ),
            )

        try:
            self._audit.execution(envelope, started=True)
        except AuditUnavailableError:
            self._repository.transition(
                request.request_id,
                PrivilegedTransactionState.FAILED,
                PrivilegedReplayState.CONSUMED,
                result_code=BrokerDecision.PERSISTENCE_UNAVAILABLE.value,
            )
            return self._result_envelope(
                PrivilegedActionResult(
                    request_id=request.request_id,
                    action_type=request.action_type,
                    broker_decision=BrokerDecision.PERSISTENCE_UNAVAILABLE,
                    pre_state_hash=pre_state_hash,
                    result_code=BrokerDecision.PERSISTENCE_UNAVAILABLE.value,
                    message="Mandatory pre-execution Mock audit failed",
                )
            )
        started = self._now()
        executed = manifest.handler.execute(request)
        if not executed:
            self._repository.transition(
                request.request_id,
                PrivilegedTransactionState.FAILED,
                PrivilegedReplayState.CONSUMED,
                result_code="MOCK_OPERATION_FAILED",
            )
            return self._finish(
                envelope,
                PrivilegedActionResult(
                    request_id=request.request_id,
                    action_type=request.action_type,
                    broker_decision=BrokerDecision.APPROVED_FOR_MOCK_EXECUTION,
                    execution_started=True,
                    execution_completed=True,
                    execution_status=MockExecutionStatus.OPERATION_FAILED,
                    pre_state_hash=pre_state_hash,
                    result_code="MOCK_OPERATION_FAILED",
                    message="The fake executor failed; no Windows operation was performed",
                    started_at=started,
                ),
            )
        self._repository.transition(
            request.request_id,
            PrivilegedTransactionState.VERIFYING,
            PrivilegedReplayState.CONSUMING,
            result_code="MOCK_EXECUTION_COMPLETED",
        )
        verified = manifest.handler.verify(request)
        if verified is None:
            self._repository.transition(
                request.request_id,
                PrivilegedTransactionState.FAILED,
                PrivilegedReplayState.CONSUMED,
                result_code="VERIFICATION_FAILED",
            )
            return self._finish(
                envelope,
                PrivilegedActionResult(
                    request_id=request.request_id,
                    action_type=request.action_type,
                    broker_decision=BrokerDecision.APPROVED_FOR_MOCK_EXECUTION,
                    execution_started=True,
                    execution_completed=True,
                    execution_status=MockExecutionStatus.MOCK_VALIDATED,
                    verification_status=PrivilegedVerificationStatus.VERIFICATION_FAILED,
                    pre_state_hash=pre_state_hash,
                    result_code="VERIFICATION_FAILED",
                    message="Fresh fake state did not satisfy the expected postcondition",
                    started_at=started,
                ),
            )
        post_state_hash = verified.state_digest()
        self._repository.transition(
            request.request_id,
            PrivilegedTransactionState.COMPLETED,
            PrivilegedReplayState.CONSUMED,
            result_code="MOCK_VALIDATED",
        )
        return self._finish(
            envelope,
            PrivilegedActionResult(
                request_id=request.request_id,
                action_type=request.action_type,
                broker_decision=BrokerDecision.APPROVED_FOR_MOCK_EXECUTION,
                execution_started=True,
                execution_completed=True,
                execution_status=MockExecutionStatus.MOCK_VALIDATED,
                verification_status=PrivilegedVerificationStatus.VERIFIED,
                pre_state_hash=pre_state_hash,
                post_state_hash=post_state_hash,
                result_code="MOCK_VALIDATED",
                message=(
                    "Stage 4X1 Mock Broker validation passed. No real elevated system "
                    "operation was performed."
                ),
                started_at=started,
            ),
        )

    def _binding_decision(
        self,
        envelope: PrivilegedActionEnvelope,
        caller: PrivilegedCallerContext,
        snapshot: PrivilegedAuthorizationSnapshot,
        payload_model: type[BaseModel],
        now: datetime,
    ) -> BrokerDecision | None:
        request = envelope.request
        if (
            caller.context_id != request.caller_context_reference
            or caller.agent_instance_id != request.agent_instance_id
        ):
            return BrokerDecision.CALLER_INVALID
        if not isinstance(request.payload, payload_model):
            return BrokerDecision.ACTION_NOT_ALLOWLISTED
        plan = snapshot.plan
        preview = snapshot.preview
        parent = snapshot.plan_confirmation
        runtime = snapshot.runtime_confirmation
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
            preview.preview_id != request.preview_id
            or preview.canonical_digest() != request.preview_hash
            or preview.plan_hash != request.plan_hash
            or preview.target_identity_hash != request.target_identity_hash
        ):
            return BrokerDecision.PREVIEW_BINDING_INVALID
        if (
            parent.confirmation_id != request.plan_confirmation_id
            or runtime.confirmation_id != request.confirmation_id
            or runtime.parent_confirmation_id != parent.confirmation_id
            or parent.state.value != "APPROVED"
            or runtime.state.value != "APPROVED"
            or now >= parent.expires_at
            or now >= runtime.expires_at
            or runtime.plan_hash != request.plan_hash
            or runtime.preview_hash != request.preview_hash
            or runtime.payload_digest != request.payload_digest
            or runtime.target_identity_hash != request.target_identity_hash
        ):
            return BrokerDecision.CONFIRMATION_INVALID
        if (
            plan.risk_level is not request.risk_level
            or preview.risk_level is not request.risk_level
        ):
            return BrokerDecision.RISK_CHANGED
        if plan.privilege_requirement is not request.privilege_requirement:
            return BrokerDecision.PRIVILEGE_UNSUPPORTED
        return None

    @staticmethod
    def _require_preview_fresh(
        preview: PrivilegedActionPreview,
        fresh: FakePrivilegedService,
    ) -> None:
        if preview.target_state_hash != fresh.state_digest():
            raise PrivilegedRevalidationError(
                BrokerDecision.TARGET_CHANGED, "Fresh target state differs from Preview"
            )
        if preview.safety_digest != fresh.safety_digest:
            raise PrivilegedRevalidationError(
                BrokerDecision.SAFETY_BLOCKED, "Fresh safety evidence differs from Preview"
            )
        if (
            preview.privilege_resolution.canonical_digest()
            != fresh.privilege_resolution.canonical_digest()
        ):
            raise PrivilegedRevalidationError(
                BrokerDecision.PRIVILEGE_UNSUPPORTED,
                "Fresh privilege evidence differs from Preview",
            )

    def _reject(
        self,
        envelope: PrivilegedActionEnvelope,
        decision: BrokerDecision,
        *,
        mutate: bool,
        expired: bool = False,
    ) -> PrivilegedActionResultEnvelope:
        if mutate:
            try:
                self._repository.reject_unconsumed(
                    envelope.request.request_id,
                    result_code=decision.value,
                    expired=expired,
                )
            except PrivilegedReplayError:
                decision = BrokerDecision.REPLAY_REJECTED
            except PrivilegedActionStoreError:
                decision = BrokerDecision.PERSISTENCE_UNAVAILABLE
        try:
            audit_id = self._audit.validation(envelope, decision)
        except AuditUnavailableError:
            audit_id = None
            decision = BrokerDecision.PERSISTENCE_UNAVAILABLE
        return self._result_envelope(
            PrivilegedActionResult(
                request_id=envelope.request.request_id,
                action_type=envelope.request.action_type,
                broker_decision=decision,
                result_code=decision.value,
                message="Privileged Mock request was rejected before execution",
                audit_id=audit_id,
            )
        )

    def _malformed(self, decision: BrokerDecision) -> PrivilegedActionResultEnvelope:
        try:
            audit_id = self._audit.malformed(decision)
        except AuditUnavailableError:
            audit_id = None
            decision = BrokerDecision.PERSISTENCE_UNAVAILABLE
        return self._result_envelope(
            PrivilegedActionResult(
                broker_decision=decision,
                result_code=decision.value,
                message="Malformed privileged protocol input was rejected",
                audit_id=audit_id,
            )
        )

    def _finish(
        self,
        envelope: PrivilegedActionEnvelope,
        result: PrivilegedActionResult,
    ) -> PrivilegedActionResultEnvelope:
        try:
            audit_id = self._audit.verification(envelope, result)
            result = result.model_copy(update={"audit_id": audit_id})
        except AuditUnavailableError:
            result = result.model_copy(
                update={
                    "broker_decision": BrokerDecision.PERSISTENCE_UNAVAILABLE,
                    "result_code": BrokerDecision.PERSISTENCE_UNAVAILABLE.value,
                    "message": "Final Mock audit persistence failed",
                }
            )
        return self._result_envelope(result)

    def _result_envelope(self, result: PrivilegedActionResult) -> PrivilegedActionResultEnvelope:
        canonical = self._serializer.canonical_result_bytes(result)
        return PrivilegedActionResultEnvelope(
            result=result,
            result_digest=hashlib.sha256(canonical).hexdigest(),
            integrity=self._authenticator.sign(canonical),
        )
