from __future__ import annotations

from uuid import uuid4

import pytest

from pc_manager_agent.agents.base import AgentIdentityFactory
from pc_manager_agent.agents.verifier import TaskOutcomeAggregator
from pc_manager_agent.config.agents import AgentRuntimeLimits
from pc_manager_agent.domain.agents import (
    AgentFinding,
    AgentResult,
    AgentResultStatus,
    AgentRole,
    DelegationBudget,
    FindingMessagePayload,
)
from pc_manager_agent.domain.context import (
    ContextReference,
    ContextSourceKind,
    ContextTrustLevel,
)
from pc_manager_agent.domain.task_graph import (
    ResourceAccess,
    ResourceIdentity,
    TaskDomain,
)
from pc_manager_agent.domain.task_outcomes import (
    FactProvenance,
    TaskFact,
    TaskOutcomeStatus,
)
from pc_manager_agent.orchestration.delegation import DelegationCoordinator, DelegationError
from pc_manager_agent.orchestration.resource_locks import (
    TaskResourceConflictError,
    TaskResourceLockService,
)
from pc_manager_agent.safety.agent_capabilities import (
    AgentCapabilityError,
    AgentDelegationPolicy,
    build_agent_capability_registry,
)
from pc_manager_agent.safety.task_goal import TaskGoalBoundary, TaskGoalBoundaryPolicy


def test_delegation_narrows_capabilities_binds_runtime_role_and_rejects_replay() -> None:
    capabilities = build_agent_capability_registry()
    identities = AgentIdentityFactory(capabilities)
    parent = identities.create(AgentRole.ORCHESTRATOR)
    task_id, node_id = uuid4(), uuid4()
    boundary = TaskGoalBoundary(
        task_id=task_id,
        goal_digest="a" * 64,
        allowed_domains=(TaskDomain.FILE,),
        allowed_capabilities=("file.scan",),
    )
    coordinator = DelegationCoordinator(
        identities,
        AgentDelegationPolicy(capabilities),
        TaskGoalBoundaryPolicy(),
        AgentRuntimeLimits(),
    )
    request = coordinator.create(
        parent,
        boundary,
        parent_node_id=node_id,
        target_role=AgentRole.FILE,
        objective_code="SCAN_APPROVED_ROOT",
        requested_capabilities=("file.scan",),
        context_refs=(),
        parent_budget=DelegationBudget(
            depth=0,
            delegations_remaining=2,
            model_calls_remaining=1,
            context_chars=1_000,
        ),
    )
    result = AgentResult(
        task_id=task_id,
        node_id=node_id,
        status=AgentResultStatus.COMPLETED,
        findings=(AgentFinding(code="READ_ONLY", summary="Read-only proposal"),),
    )
    child, envelope = coordinator.consume(parent, request, FindingMessagePayload(result=result))
    assert child.role is AgentRole.FILE
    assert envelope.sender_role is AgentRole.ORCHESTRATOR
    assert envelope.recipient_role is AgentRole.FILE
    assert envelope.trust_labels == (ContextTrustLevel.LOCAL_STRUCTURED_DATA,)
    with pytest.raises(DelegationError, match="already"):
        coordinator.consume(parent, request, FindingMessagePayload(result=result))
    with pytest.raises(AgentCapabilityError, match="ESCALATION"):
        coordinator.create(
            parent,
            boundary,
            parent_node_id=node_id,
            target_role=AgentRole.FILE,
            objective_code="ESCALATE",
            requested_capabilities=("admin.shell",),
            context_refs=(),
            parent_budget=DelegationBudget(
                depth=0,
                delegations_remaining=1,
                model_calls_remaining=1,
                context_chars=1_000,
            ),
        )


def test_delegation_binds_issuer_preserves_taint_and_enforces_root_count() -> None:
    capabilities = build_agent_capability_registry()
    identities = AgentIdentityFactory(capabilities)
    parent = identities.create(AgentRole.ORCHESTRATOR)
    coordinator = DelegationCoordinator(
        identities,
        AgentDelegationPolicy(capabilities),
        TaskGoalBoundaryPolicy(),
        AgentRuntimeLimits(max_agent_delegations=1),
    )
    task_id, node_id = uuid4(), uuid4()
    reference = ContextReference(
        reference_id="web-ref",
        source_kind=ContextSourceKind.WEB_CHUNK,
        owner_domain="BROWSER",
        content_digest="d" * 64,
    )
    boundary = TaskGoalBoundary(
        task_id=task_id,
        goal_digest="e" * 64,
        allowed_domains=(TaskDomain.FILE,),
        allowed_capabilities=("file.scan",),
        allowed_reference_ids=("web-ref",),
    )
    budget = DelegationBudget(
        depth=0,
        delegations_remaining=2,
        model_calls_remaining=1,
        context_chars=1_000,
    )
    request = coordinator.create(
        parent,
        boundary,
        parent_node_id=node_id,
        target_role=AgentRole.FILE,
        objective_code="SCAN_APPROVED_ROOT",
        requested_capabilities=("file.scan",),
        context_refs=(reference,),
        parent_budget=budget,
    )
    with pytest.raises(DelegationError, match="count"):
        coordinator.create(
            parent,
            boundary,
            parent_node_id=node_id,
            target_role=AgentRole.FILE,
            objective_code="SCAN_AGAIN",
            requested_capabilities=("file.scan",),
            context_refs=(reference,),
            parent_budget=budget,
        )
    result = AgentResult(task_id=task_id, node_id=node_id, status=AgentResultStatus.COMPLETED)
    with pytest.raises(DelegationError, match="not issued"):
        coordinator.consume(
            identities.create(AgentRole.ORCHESTRATOR),
            request,
            FindingMessagePayload(result=result),
        )
    _, envelope = coordinator.consume(parent, request, FindingMessagePayload(result=result))
    assert set(envelope.trust_labels) == {
        ContextTrustLevel.LOCAL_STRUCTURED_DATA,
        ContextTrustLevel.UNTRUSTED_WEB,
    }


def test_expired_and_depth_limited_delegation_fails() -> None:
    capabilities = build_agent_capability_registry()
    identities = AgentIdentityFactory(capabilities)
    coordinator = DelegationCoordinator(
        identities,
        AgentDelegationPolicy(capabilities),
        TaskGoalBoundaryPolicy(),
        AgentRuntimeLimits(max_delegation_depth=1),
    )
    boundary = TaskGoalBoundary(
        task_id=uuid4(),
        goal_digest="b" * 64,
        allowed_domains=(TaskDomain.FILE,),
        allowed_capabilities=("file.scan",),
    )
    with pytest.raises(DelegationError, match="depth"):
        coordinator.create(
            identities.create(AgentRole.ORCHESTRATOR),
            boundary,
            parent_node_id=uuid4(),
            target_role=AgentRole.FILE,
            objective_code="SCAN",
            requested_capabilities=("file.scan",),
            context_refs=(),
            parent_budget=DelegationBudget(
                depth=1,
                delegations_remaining=1,
                model_calls_remaining=0,
                context_chars=100,
            ),
        )


def test_resource_locks_parallelize_reads_and_serialize_writes() -> None:
    service = TaskResourceLockService()
    identity = "c" * 64
    read = ResourceIdentity(
        domain=TaskDomain.OFFICE,
        kind="DOCUMENT",
        identity_digest=identity,
        access=ResourceAccess.READ,
    )
    write = read.model_copy(update={"access": ResourceAccess.WRITE})
    first = service.acquire(uuid4(), uuid4(), (read,))
    second = service.acquire(uuid4(), uuid4(), (read,))
    with pytest.raises(TaskResourceConflictError, match="already"):
        service.acquire(uuid4(), uuid4(), (write,))
    service.release(first)
    service.release(second)
    writer = service.acquire(uuid4(), uuid4(), (write,))
    with pytest.raises(TaskResourceConflictError):
        service.acquire(uuid4(), uuid4(), (read,))
    service.release(writer)


def test_deterministic_fact_outranks_model_and_no_majority_vote() -> None:
    task_id, node = uuid4(), uuid4()
    facts = (
        TaskFact(
            code="ACTION_VERIFIED",
            summary="model says success",
            provenance=FactProvenance.MODEL_INFERENCE,
        ),
        TaskFact(
            code="ACTION_VERIFIED",
            summary="domain observed target still present",
            provenance=FactProvenance.DOMAIN_VERIFIED,
        ),
    )
    outcome = TaskOutcomeAggregator().aggregate(
        task_id,
        facts,
        completed=(node,),
        incomplete=(),
    )
    assert outcome.status is TaskOutcomeStatus.COMPLETED
    assert outcome.facts[0].summary == "domain observed target still present"
