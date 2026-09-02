"""Finite delegation and runtime-authored Agent message construction."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

from pc_manager_agent.agents.base import AgentIdentityFactory
from pc_manager_agent.config.agents import AgentRuntimeLimits
from pc_manager_agent.domain.agents import (
    AgentDelegationRequest,
    AgentMessageEnvelope,
    AgentMessagePayload,
    AgentRole,
    AgentRuntimeIdentity,
    DelegationBudget,
)
from pc_manager_agent.domain.context import ContextReference, ContextTrustLevel
from pc_manager_agent.safety.agent_capabilities import AgentDelegationPolicy
from pc_manager_agent.safety.context import required_trust_for_source
from pc_manager_agent.safety.task_goal import TaskGoalBoundary, TaskGoalBoundaryPolicy


class DelegationError(RuntimeError):
    """Raised for expired, repeated, over-budget, or forged delegation."""


class DelegationCoordinator:
    """Issue single-use child work with capability intersection and small budgets."""

    def __init__(
        self,
        identities: AgentIdentityFactory,
        policy: AgentDelegationPolicy,
        goal_policy: TaskGoalBoundaryPolicy,
        limits: AgentRuntimeLimits,
    ) -> None:
        self._identities = identities
        self._policy = policy
        self._goal_policy = goal_policy
        self._limits = limits
        self._consumed: set[object] = set()
        self._issued: dict[object, tuple[object, str]] = {}
        self._issued_count: dict[object, int] = {}
        self._lock = threading.RLock()

    def create(
        self,
        parent: AgentRuntimeIdentity,
        boundary: TaskGoalBoundary,
        *,
        parent_node_id: object,
        target_role: AgentRole,
        objective_code: str,
        requested_capabilities: tuple[str, ...],
        context_refs: tuple[ContextReference, ...],
        parent_budget: DelegationBudget,
    ) -> AgentDelegationRequest:
        """Create a child request only after narrowing every parent-controlled field."""
        from uuid import UUID

        parent_manifest = self._identities.validate(parent)
        if not isinstance(parent_node_id, UUID):
            raise DelegationError("Delegation requires a UUID parent node")
        if parent_budget.delegations_remaining <= 0:
            raise DelegationError("Delegation budget exhausted")
        if parent_budget.depth >= self._limits.max_delegation_depth:
            raise DelegationError("Delegation depth limit exceeded")
        with self._lock:
            issued_count = self._issued_count.get(boundary.task_id, 0)
            if issued_count >= self._limits.max_agent_delegations:
                raise DelegationError("Root task delegation count limit exceeded")
        narrowed = self._policy.narrow(parent, target_role, requested_capabilities)
        created = datetime.now(UTC)
        request = AgentDelegationRequest(
            parent_task_id=boundary.task_id,
            parent_node_id=parent_node_id,
            target_role=target_role,
            objective_code=objective_code,
            goal_digest=boundary.goal_digest,
            context_refs=context_refs,
            allowed_capabilities=narrowed,
            budget=DelegationBudget(
                depth=parent_budget.depth + 1,
                delegations_remaining=parent_budget.delegations_remaining - 1,
                model_calls_remaining=parent_budget.model_calls_remaining,
                context_chars=min(parent_budget.context_chars, self._limits.max_context_chars),
            ),
            created_at=created,
            expires_at=created + timedelta(seconds=self._limits.delegation_ttl_seconds),
        )
        self._goal_policy.validate_delegation(boundary, request)
        with self._lock:
            self._issued[request.delegation_id] = (
                parent.instance_id,
                parent_manifest.canonical_digest(),
            )
            self._issued_count[boundary.task_id] = issued_count + 1
        return request

    def consume(
        self,
        parent: AgentRuntimeIdentity,
        request: AgentDelegationRequest,
        payload: AgentMessagePayload,
    ) -> tuple[AgentRuntimeIdentity, AgentMessageEnvelope]:
        """Consume one request and bind the sender role from runtime identity."""
        parent_manifest = self._identities.validate(parent)
        current = datetime.now(UTC)
        if current >= request.expires_at:
            raise DelegationError("Delegation expired")
        with self._lock:
            if request.delegation_id in self._consumed:
                raise DelegationError("Delegation was already consumed")
            issued = self._issued.get(request.delegation_id)
            if issued != (parent.instance_id, parent_manifest.canonical_digest()):
                raise DelegationError("Delegation was not issued to this parent Agent")
            child = self._identities.create(request.target_role)
            trust_labels = {
                required_trust_for_source(reference.source_kind)
                for reference in request.context_refs
            }
            trust_labels.add(
                ContextTrustLevel.MODEL_GENERATED
                if parent_manifest.model_backed
                else ContextTrustLevel.LOCAL_STRUCTURED_DATA
            )
            trust_labels.discard(ContextTrustLevel.SYSTEM_TRUSTED)
            envelope = AgentMessageEnvelope(
                task_id=request.parent_task_id,
                node_id=request.parent_node_id,
                sender_instance_id=parent.instance_id,
                sender_role=parent.role,
                recipient_role=child.role,
                goal_digest=request.goal_digest,
                trust_labels=tuple(sorted(trust_labels, key=lambda item: item.value)),
                payload=payload,
                created_at=current,
                expires_at=request.expires_at,
                prompt_version=parent.prompt_version,
            )
            self._consumed.add(request.delegation_id)
            del self._issued[request.delegation_id]
            return child, envelope
