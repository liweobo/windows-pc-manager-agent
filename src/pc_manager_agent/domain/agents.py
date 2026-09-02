"""Agent identities, capabilities, proposals, delegation, and message envelopes."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import Field, JsonValue, model_validator

from pc_manager_agent.domain.context import (
    ContextReference,
    ContextTrustLevel,
    DataClassification,
)
from pc_manager_agent.domain.plans import FrozenModel


class AgentRole(StrEnum):
    """Finite runtime roles; model text cannot create or impersonate one."""

    ORCHESTRATOR = "ORCHESTRATOR"
    PLANNER = "PLANNER"
    SAFETY_REVIEWER = "SAFETY_REVIEWER"
    FILE = "FILE"
    SYSTEM = "SYSTEM"
    SOFTWARE = "SOFTWARE"
    OFFICE = "OFFICE"
    BROWSER = "BROWSER"
    OPTIMIZATION = "OPTIMIZATION"
    VERIFIER = "VERIFIER"
    MEMORY_MANAGER = "MEMORY_MANAGER"
    AUDIT_MANAGER = "AUDIT_MANAGER"


class AgentResultStatus(StrEnum):
    """Finite result states without an authorization state."""

    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


class AgentCapabilityManifest(FrozenModel):
    """Default-deny capabilities attached to one exact runtime role."""

    role: AgentRole
    version: str = Field(min_length=1, max_length=40)
    model_backed: bool = False
    allowed_input_types: tuple[str, ...] = ()
    allowed_output_types: tuple[str, ...] = ()
    readable_data: tuple[DataClassification, ...] = ()
    proposed_tools: tuple[str, ...] = ()
    delegatable_roles: tuple[AgentRole, ...] = ()
    memory_scopes: tuple[str, ...] = ()
    may_propose_actions: bool = False
    may_execute_actions: Literal[False] = False
    may_request_confirmation: Literal[False] = False
    may_propose_memory: bool = False
    may_write_memory: bool = False

    @model_validator(mode="after")
    def require_unique_capabilities(self) -> AgentCapabilityManifest:
        """Reject ambiguous manifests and model-backed authority managers."""
        collections = (
            self.allowed_input_types,
            self.allowed_output_types,
            self.readable_data,
            self.proposed_tools,
            self.delegatable_roles,
            self.memory_scopes,
        )
        if any(len(values) != len(set(values)) for values in collections):
            raise ValueError("Agent capability lists must be unique")
        if self.model_backed and self.may_write_memory:
            raise ValueError("Model-backed Agents cannot write Memory")
        return self

    def canonical_digest(self) -> str:
        """Bind an Agent instance and audit record to one exact manifest."""
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


class AgentRuntimeIdentity(FrozenModel):
    """Identity created by the runtime, never accepted from model output."""

    instance_id: UUID = Field(default_factory=uuid4)
    role: AgentRole
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_version: str = Field(min_length=1, max_length=80)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AgentToolProposal(FrozenModel):
    """Untrusted proposal that must pass capability, schema, goal, and domain review."""

    proposal_id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    node_id: UUID
    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_.-]+$")
    arguments: dict[str, JsonValue]
    rationale_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,79}$")


class DelegationBudget(FrozenModel):
    """Remaining finite work a child Agent may consume."""

    depth: int = Field(ge=0, le=5)
    delegations_remaining: int = Field(ge=0, le=32)
    model_calls_remaining: int = Field(ge=0, le=32)
    context_chars: int = Field(ge=0, le=32_000)


class AgentDelegationRequest(FrozenModel):
    """Typed, expiring delegation with only references and narrowed capabilities."""

    delegation_id: UUID = Field(default_factory=uuid4)
    parent_task_id: UUID
    parent_node_id: UUID
    target_role: AgentRole
    objective_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,79}$")
    goal_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_refs: tuple[ContextReference, ...] = ()
    allowed_capabilities: tuple[str, ...] = ()
    budget: DelegationBudget
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime

    @model_validator(mode="after")
    def require_live_unique_delegation(self) -> AgentDelegationRequest:
        """Reject duplicate capabilities and already-expired delegation objects."""
        if self.expires_at <= self.created_at:
            raise ValueError("Delegation expiry must be after creation")
        if len(self.allowed_capabilities) != len(set(self.allowed_capabilities)):
            raise ValueError("Delegated capabilities must be unique")
        return self


class AgentFinding(FrozenModel):
    """One non-authoritative fact or warning carrying source references."""

    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,79}$")
    summary: str = Field(min_length=1, max_length=500)
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=32)


class DomainPreparationProposal(FrozenModel):
    """Reference-only request to enter an existing domain preparation boundary."""

    task_id: UUID
    node_id: UUID
    domain: str = Field(pattern=r"^[A-Z_]+$")
    action_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,79}$")
    goal_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_refs: tuple[str, ...] = Field(default=(), max_length=32)
    execution_authorized: Literal[False] = False


class AgentResult(FrozenModel):
    """Structured result that intentionally cannot carry execution authorization."""

    task_id: UUID
    node_id: UUID
    status: AgentResultStatus
    findings: tuple[AgentFinding, ...] = ()
    proposed_actions: tuple[AgentToolProposal, ...] = ()
    preparation_proposals: tuple[DomainPreparationProposal, ...] = ()
    warnings: tuple[str, ...] = Field(default=(), max_length=20)
    unresolved_questions: tuple[str, ...] = Field(default=(), max_length=10)


class FindingMessagePayload(FrozenModel):
    """Finding-only Agent message payload."""

    kind: Literal["FINDINGS"] = "FINDINGS"
    result: AgentResult


class DelegationMessagePayload(FrozenModel):
    """Delegation-only Agent message payload."""

    kind: Literal["DELEGATION"] = "DELEGATION"
    request: AgentDelegationRequest


AgentMessagePayload = Annotated[
    FindingMessagePayload | DelegationMessagePayload,
    Field(discriminator="kind"),
]


class AgentMessageEnvelope(FrozenModel):
    """Runtime-authored envelope; role fields are never copied from model output."""

    message_id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    node_id: UUID
    sender_instance_id: UUID
    sender_role: AgentRole
    recipient_role: AgentRole
    goal_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    trust_labels: tuple[ContextTrustLevel, ...]
    payload: AgentMessagePayload
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime
    prompt_version: str = Field(min_length=1, max_length=80)

    @model_validator(mode="after")
    def require_live_message(self) -> AgentMessageEnvelope:
        """Reject replay-ready, unlabelled, or identity-inconsistent messages."""
        if self.expires_at <= self.created_at:
            raise ValueError("Agent message expiry must be after creation")
        if not self.trust_labels or len(self.trust_labels) != len(set(self.trust_labels)):
            raise ValueError("Agent messages require unique trust labels")
        if ContextTrustLevel.SYSTEM_TRUSTED in self.trust_labels:
            raise ValueError("Agent messages cannot claim system trust")
        if isinstance(self.payload, FindingMessagePayload):
            result = self.payload.result
            if result.task_id != self.task_id or result.node_id != self.node_id:
                raise ValueError("Agent result identity does not match its envelope")
        else:
            request = self.payload.request
            if (
                request.parent_task_id != self.task_id
                or request.parent_node_id != self.node_id
                or request.target_role is not self.recipient_role
                or request.goal_digest != self.goal_digest
            ):
                raise ValueError("Delegation identity does not match its envelope")
        return self
