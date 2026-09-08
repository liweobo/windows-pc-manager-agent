"""Standard-user coordination for Stage 4X1 Mock privileged requests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from pc_manager_agent.audit.privileged_actions import PrivilegedActionAuditLogger
from pc_manager_agent.confirmation.privileged_actions import (
    PrivilegedActionConfirmation,
    PrivilegedActionConfirmationService,
)
from pc_manager_agent.domain.privileged_actions import (
    PrivilegedActionEnvelope,
    PrivilegedActionPlan,
    PrivilegedActionPreview,
    PrivilegedActionResultEnvelope,
    PrivilegedCallerContext,
    PrivilegedExecutionMode,
    PrivilegedPayload,
    PrivilegeResolution,
    PrivilegeResolutionStatus,
)
from pc_manager_agent.persistence.privileged_actions import PrivilegedActionRepository
from pc_manager_agent.privileged.builder import PrivilegedActionBuilder
from pc_manager_agent.privileged.serialization import PrivilegedRequestSerializer


class PrivilegedActionPreparationError(RuntimeError):
    """Raised when upstream evidence is blocked, stale, or not Administrator-required."""


class MockPrivilegedBrokerPort(Protocol):
    """Minimum in-process Mock dispatch surface; production never imports its implementation."""

    def dispatch(
        self,
        serialized_request: bytes,
        caller: PrivilegedCallerContext,
    ) -> PrivilegedActionResultEnvelope:
        """Dispatch one authenticated synthetic request in explicit developer mode."""
        ...


@dataclass(frozen=True, slots=True)
class PreparedPrivilegedAction:
    """Initial plan, Preview, and pending plan confirmation shown by the UI."""

    plan: PrivilegedActionPlan
    preview: PrivilegedActionPreview
    plan_confirmation: PrivilegedActionConfirmation


@dataclass(frozen=True, slots=True)
class PreparedPrivilegedRuntimeConfirmation:
    """Fresh Preview and pending short-lived immediate confirmation."""

    preview: PrivilegedActionPreview
    runtime_confirmation: PrivilegedActionConfirmation


class PrivilegedActionService:
    """Prepare exact capabilities for either the Mock or external Broker boundary."""

    def __init__(
        self,
        builder: PrivilegedActionBuilder,
        confirmations: PrivilegedActionConfirmationService,
        repository: PrivilegedActionRepository,
        serializer: PrivilegedRequestSerializer,
        broker: MockPrivilegedBrokerPort | None,
        audit: PrivilegedActionAuditLogger,
        caller: PrivilegedCallerContext,
        execution_mode: PrivilegedExecutionMode = PrivilegedExecutionMode.MOCK,
    ) -> None:
        self._builder = builder
        self._confirmations = confirmations
        self._repository = repository
        self._serializer = serializer
        self._broker = broker
        self._audit = audit
        self._caller = caller
        self._execution_mode = execution_mode

    def prepare(
        self,
        *,
        source_plan_id: UUID,
        source_plan_hash: str,
        payload: PrivilegedPayload,
        target_identity_hash: str,
        object_summary: str,
        target_state_hash: str,
        safety_digest: str,
        privilege_resolution: PrivilegeResolution,
    ) -> PreparedPrivilegedAction:
        """Persist a mode-bound Preview and request the first durable confirmation."""
        if privilege_resolution.status is not PrivilegeResolutionStatus.REQUIRED:
            raise PrivilegedActionPreparationError(
                "Only fresh Administrator-required evidence can enter the privileged protocol"
            )
        plan = self._builder.plan(
            source_plan_id=source_plan_id,
            source_plan_hash=source_plan_hash,
            payload=payload,
            target_identity_hash=target_identity_hash,
            object_summary=object_summary,
        )
        preview = self._builder.preview(
            plan,
            target_state_hash=target_state_hash,
            safety_digest=safety_digest,
            privilege_resolution=privilege_resolution,
            execution_mode=self._execution_mode,
        )
        self._repository.create(plan, preview)
        confirmation = self._confirmations.request_plan(plan, preview)
        return PreparedPrivilegedAction(plan, preview, confirmation)

    def resolve_plan_confirmation(
        self,
        confirmation_id: UUID,
        approved: bool,
        plan: PrivilegedActionPlan,
        preview: PrivilegedActionPreview,
    ) -> PrivilegedActionConfirmation:
        """Resolve the first exact confirmation without constructing a request."""
        return self._confirmations.resolve_plan(confirmation_id, approved, plan, preview)

    def prepare_runtime_confirmation(
        self,
        plan_confirmation_id: UUID,
        plan: PrivilegedActionPlan,
        *,
        target_state_hash: str,
        safety_digest: str,
        privilege_resolution: PrivilegeResolution,
    ) -> PreparedPrivilegedRuntimeConfirmation:
        """Create a fresh Preview and its separate short-lived confirmation."""
        preview = self._builder.preview(
            plan,
            target_state_hash=target_state_hash,
            safety_digest=safety_digest,
            privilege_resolution=privilege_resolution,
            execution_mode=self._execution_mode,
        )
        confirmation = self._confirmations.request_runtime(plan_confirmation_id, plan, preview)
        return PreparedPrivilegedRuntimeConfirmation(preview, confirmation)

    def resolve_runtime_confirmation(
        self,
        confirmation_id: UUID,
        approved: bool,
        plan: PrivilegedActionPlan,
        preview: PrivilegedActionPreview,
    ) -> PrivilegedActionConfirmation:
        """Resolve the immediate confirmation without executing fake or real state."""
        return self._confirmations.resolve_runtime(confirmation_id, approved, plan, preview)

    def build_and_register(
        self,
        plan: PrivilegedActionPlan,
        preview: PrivilegedActionPreview,
        *,
        plan_confirmation_id: UUID,
        runtime_confirmation_id: UUID,
    ) -> PrivilegedActionEnvelope:
        """Build, authenticate, persist, and audit one single-use capability."""
        envelope = self._builder.build(
            plan,
            preview,
            plan_confirmation_id=plan_confirmation_id,
            runtime_confirmation_id=runtime_confirmation_id,
            caller=self._caller,
        )
        self._repository.register_request(envelope)
        self._audit.authorized(envelope, self._execution_mode)
        return envelope

    def dispatch_mock(
        self,
        envelope: PrivilegedActionEnvelope,
    ) -> PrivilegedActionResultEnvelope:
        """Send canonical bytes only to the in-process Mock Broker."""
        if self._execution_mode is not PrivilegedExecutionMode.MOCK or self._broker is None:
            raise PrivilegedActionPreparationError(
                "Real privileged requests cannot use Mock dispatch"
            )
        return self._broker.dispatch(
            self._serializer.serialize(envelope),
            self._caller,
        )
