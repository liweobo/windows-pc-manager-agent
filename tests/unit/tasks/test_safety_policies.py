from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

import pytest
from pydantic import ValidationError

from pc_manager_agent.domain.agents import AgentRole
from pc_manager_agent.domain.computer_tasks import (
    AutonomyLevel,
    ComputerTaskState,
    TaskRevisionRequest,
)
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.task_checkpoints import (
    PersistedTaskGraph,
    TaskCheckpoint,
    TaskNodeDispatch,
    TaskNodeSnapshot,
)
from pc_manager_agent.domain.task_graph import (
    TaskDependency,
    TaskGraph,
    TaskNode,
    TaskNodeStatus,
    TaskNodeType,
)
from pc_manager_agent.domain.task_workflows import (
    DomainPreparationResult,
    DomainPreparationStatus,
    DomainReconciliationResult,
    DomainReconciliationStatus,
    DomainResultReceipt,
    DomainResultStatus,
    DomainType,
    DomainVerificationStatus,
)
from pc_manager_agent.safety.final_orchestrator import (
    ActionContinuationPolicy,
    ContinuationDecision,
    FinalOrchestratorSafetyError,
    GlobalSafetyInvariantGuard,
    TaskPlanConfirmationPolicy,
    canonical_scope_digest,
)
from pc_manager_agent.safety.task_revision import TaskRevisionValidator
from pc_manager_agent.safety.task_staleness import (
    StalenessDecision,
    StalenessKind,
    TaskStalenessPolicy,
)


def _task_and_graph(task_services):
    template = task_services.templates.get("STARTUP_REVIEW")
    task = task_services.orchestrator.create_task(
        template.title,
        template.domains,
        root_request_id=uuid4(),
        kind=template.kind,
        autonomy=template.autonomy,
    )
    goal = task_services.orchestrator._volatile_goals[task.task_id]
    graph = task_services.orchestrator._require_volatile_graph(task)
    return task, graph, goal


def test_confirmation_policy_rejects_state_graph_scope_expiry_replay_and_binding(
    task_services,
) -> None:
    task, graph, _ = _task_and_graph(task_services)
    policy = TaskPlanConfirmationPolicy()
    wrong_state = task.model_copy(update={"state": ComputerTaskState.READY})
    with pytest.raises(FinalOrchestratorSafetyError):
        policy.request(wrong_state, graph, scope_digest="a" * 64, ttl_seconds=60)
    with pytest.raises(FinalOrchestratorSafetyError):
        policy.request(
            task,
            graph.model_copy(update={"version": 2}),
            scope_digest="a" * 64,
            ttl_seconds=60,
        )
    with pytest.raises(FinalOrchestratorSafetyError):
        policy.request(task, graph, scope_digest="bad", ttl_seconds=60)
    now = datetime.now(UTC)
    confirmation = policy.request(
        task,
        graph,
        scope_digest="a" * 64,
        ttl_seconds=60,
        now=now,
    )
    expired = policy.resolve(confirmation, task, approved=True, now=now + timedelta(minutes=2))
    assert expired.state.value == "EXPIRED"
    resolved = policy.resolve(confirmation, task, approved=False, now=now)
    assert resolved.state.value == "REJECTED"
    with pytest.raises(FinalOrchestratorSafetyError):
        policy.resolve(resolved, task, approved=True, now=now)
    changed_task = task.model_copy(update={"graph_id": uuid4()})
    with pytest.raises(FinalOrchestratorSafetyError):
        policy.resolve(confirmation, changed_task, approved=True, now=now)


def test_confirmation_policy_rejects_graph_with_no_read_only_nodes(task_services) -> None:
    task, graph, goal = _task_and_graph(task_services)
    execute = TaskNode(
        node_type=TaskNodeType.EXECUTE_DOMAIN_ACTION,
        domain=graph.nodes[1].domain,
        agent_role=AgentRole.ORCHESTRATOR,
        risk_hint=RiskLevel.R1,
    )
    no_read = TaskGraph.create(goal, (execute,)).model_copy(update={"task_id": task.task_id})
    changed = task.model_copy(update={"graph_digest": no_read.canonical_digest()})
    with pytest.raises(FinalOrchestratorSafetyError):
        TaskPlanConfirmationPolicy().request(
            changed,
            no_read,
            scope_digest="a" * 64,
            ttl_seconds=60,
        )


def test_continuation_and_global_guard_cover_every_fail_closed_branch() -> None:
    base = {
        "task_id": uuid4(),
        "node_id": uuid4(),
        "graph_version": 1,
        "domain": DomainType.FILE,
        "requires_domain_confirmation": False,
        "risk_level": RiskLevel.R0,
    }
    policy = ActionContinuationPolicy()
    ready = DomainPreparationResult(status=DomainPreparationStatus.READY_FOR_READ, **base)
    blocked = DomainPreparationResult(status=DomainPreparationStatus.BLOCKED, **base)
    assert policy.decide(blocked, task_plan_confirmed=True) is ContinuationDecision.STOP
    assert policy.decide(ready, task_plan_confirmed=True) is ContinuationDecision.CONTINUE_READ_ONLY
    assert policy.decide(ready, task_plan_confirmed=False) is ContinuationDecision.WAIT_FOR_USER
    write = DomainPreparationResult(
        status=DomainPreparationStatus.NEEDS_DOMAIN_CONFIRMATION,
        **{**base, "risk_level": RiskLevel.R2, "requires_domain_confirmation": True},
    )
    assert (
        policy.decide(write, task_plan_confirmed=True)
        is ContinuationDecision.WAIT_FOR_DOMAIN_CONFIRMATION
    )
    unsafe = DomainPreparationResult.model_construct(
        status=DomainPreparationStatus.READY_FOR_REVIEW,
        execution_authorized=False,
        **{**base, "risk_level": RiskLevel.R1},
    )
    with pytest.raises(FinalOrchestratorSafetyError):
        policy.decide(unsafe, task_plan_confirmed=True)
    assert policy.may_retry(risk=RiskLevel.R0, read_only=True, attempts=0, limit=1)
    assert not policy.may_retry(risk=RiskLevel.R1, read_only=True, attempts=0, limit=1)

    guard = GlobalSafetyInvariantGuard()
    guard.require_guided_autonomy(AutonomyLevel.GUIDED_EXECUTION)
    with pytest.raises(FinalOrchestratorSafetyError):
        guard.require_guided_autonomy(cast(AutonomyLevel, "FULL_UNATTENDED"))
    guard.validate_preparation(ready)
    smuggled = ready.model_construct(**{**ready.__dict__, "execution_authorized": True})
    with pytest.raises(FinalOrchestratorSafetyError):
        guard.validate_preparation(smuggled)
    with pytest.raises(FinalOrchestratorSafetyError):
        guard.validate_preparation(unsafe)
    receipt = DomainResultReceipt(
        task_id=base["task_id"],
        node_id=base["node_id"],
        graph_version=1,
        domain=DomainType.FILE,
        status=DomainResultStatus.BLOCKED,
        verification_status=DomainVerificationStatus.UNVERIFIED,
        result_code="BLOCKED_BY_TEST",
    )
    with pytest.raises(FinalOrchestratorSafetyError):
        guard.validate_receipt(
            receipt,
            task_id=receipt.task_id,
            node_id=uuid4(),
            graph_version=1,
        )
    with pytest.raises(FinalOrchestratorSafetyError):
        canonical_scope_digest(("same", "same"))


def test_revision_validator_rejects_all_implicit_or_stale_changes(task_services) -> None:
    task, _, _ = _task_and_graph(task_services)
    validator = TaskRevisionValidator()
    base = {
        "task_id": task.task_id,
        "expected_graph_version": 1,
        "new_goal": "new safe goal",
        "requested_domain_codes": ("STARTUP",),
    }
    with pytest.raises(FinalOrchestratorSafetyError):
        validator.validate(
            task,
            TaskRevisionRequest(**{**base, "expected_graph_version": 2}),
            current_domains=(DomainType.STARTUP,),
        )
    implicit = TaskRevisionRequest.model_construct(**{**base, "user_initiated": False})
    with pytest.raises(FinalOrchestratorSafetyError):
        validator.validate(task, implicit, current_domains=(DomainType.STARTUP,))
    for domains in (("UNKNOWN",), (), ("STARTUP", "STARTUP")):
        request = TaskRevisionRequest(**{**base, "requested_domain_codes": domains})
        with pytest.raises(FinalOrchestratorSafetyError):
            validator.validate(task, request, current_domains=(DomainType.STARTUP,))
    same_goal = TaskRevisionRequest(**{**base, "new_goal": task.safe_goal_summary})
    with pytest.raises(FinalOrchestratorSafetyError):
        validator.validate(task, same_goal, current_domains=(DomainType.STARTUP,))
    expanded = TaskRevisionRequest(**{**base, "requested_domain_codes": ("STARTUP", "SERVICE")})
    with pytest.raises(FinalOrchestratorSafetyError):
        validator.validate(task, expanded, current_domains=(DomainType.STARTUP,))
    approved = expanded.model_copy(update={"scope_expansion_approved": True})
    assert validator.validate(task, approved, current_domains=(DomainType.STARTUP,)) == (
        DomainType.STARTUP,
        DomainType.SERVICE,
    )


def test_staleness_policy_is_object_specific() -> None:
    policy = TaskStalenessPolicy()
    now = datetime.now(UTC)
    cases = (
        (StalenessKind.PROCESS_IDENTITY, now, False, StalenessDecision.INVALIDATE_REFERENCE),
        (StalenessKind.BROWSER_DOM, now, False, StalenessDecision.INVALIDATE_REFERENCE),
        (
            StalenessKind.FILE_IDENTITY,
            now,
            False,
            StalenessDecision.REQUIRE_FRESH_RESOLUTION,
        ),
        (
            StalenessKind.OPTIMIZATION_REPORT,
            now,
            False,
            StalenessDecision.REQUIRE_FRESH_ANALYSIS,
        ),
        (
            StalenessKind.SOFTWARE_INVENTORY,
            now - timedelta(minutes=6),
            False,
            StalenessDecision.REQUIRE_FRESH_RESOLUTION,
        ),
        (
            StalenessKind.SYSTEM_OBSERVATION,
            now - timedelta(minutes=2),
            False,
            StalenessDecision.REQUIRE_FRESH_ANALYSIS,
        ),
        (
            StalenessKind.SYSTEM_OBSERVATION,
            now,
            False,
            StalenessDecision.CURRENT_CONTEXT_ONLY,
        ),
        (
            StalenessKind.SOFTWARE_INVENTORY,
            now,
            True,
            StalenessDecision.REQUIRE_FRESH_RESOLUTION,
        ),
    )
    for kind, created, for_write, expected in cases:
        result = policy.assess(
            kind,
            DomainType.SYSTEM,
            created,
            now=now,
            for_write=for_write,
        )
        assert result.decision is expected


def test_checkpoint_graph_dispatch_and_receipt_models_reject_contradictions() -> None:
    node = TaskNode(
        node_type=TaskNodeType.READ,
        domain="SYSTEM",
        agent_role=AgentRole.SYSTEM,
    )
    with pytest.raises(ValidationError):
        PersistedTaskGraph(
            task_id=uuid4(),
            version=1,
            goal_digest="a" * 64,
            graph_digest="b" * 64,
            nodes=(),
        )
    with pytest.raises(ValidationError):
        PersistedTaskGraph(
            task_id=uuid4(),
            version=1,
            goal_digest="a" * 64,
            graph_digest="b" * 64,
            nodes=(node,),
            dependencies=(TaskDependency(prerequisite_id=node.node_id, dependent_id=uuid4()),),
        )
    snapshot = TaskNodeSnapshot(
        node_id=node.node_id,
        domain=DomainType.SYSTEM,
        status=TaskNodeStatus.COMPLETED,
    )
    common = {
        "task_id": uuid4(),
        "graph_id": uuid4(),
        "graph_version": 1,
        "graph_digest": "a" * 64,
        "policy_digest": "b" * 64,
        "nodes": (snapshot,),
        "checkpoint_reason": "TEST_CHECKPOINT",
    }
    with pytest.raises(ValidationError):
        TaskCheckpoint(**common, completed_node_ids=(uuid4(),))
    with pytest.raises(ValidationError):
        TaskCheckpoint(**common, node_result_refs=("same", "same"))
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        TaskNodeDispatch(
            task_id=uuid4(),
            graph_version=1,
            node_id=uuid4(),
            domain=DomainType.FILE,
            read_only=True,
            created_at=now,
            updated_at=now - timedelta(seconds=1),
        )
    with pytest.raises(ValidationError):
        TaskNodeDispatch(
            task_id=uuid4(),
            graph_version=1,
            node_id=uuid4(),
            domain=DomainType.FILE,
            read_only=False,
            attempt_count=2,
        )
    with pytest.raises(ValidationError):
        DomainResultReceipt(
            task_id=uuid4(),
            node_id=uuid4(),
            graph_version=1,
            domain=DomainType.FILE,
            status=DomainResultStatus.COMPLETED_VERIFIED,
            verification_status=DomainVerificationStatus.UNVERIFIED,
            result_code="FALSE_SUCCESS",
        )
    with pytest.raises(ValidationError):
        DomainResultReceipt(
            task_id=uuid4(),
            node_id=uuid4(),
            graph_version=1,
            domain=DomainType.FILE,
            status=DomainResultStatus.COMPLETED_UNVERIFIED,
            verification_status=DomainVerificationStatus.VERIFIED,
            result_code="CONTRADICTORY_SUCCESS",
        )
    with pytest.raises(ValidationError):
        DomainReconciliationResult(
            reconciliation_id=uuid4(),
            task_id=uuid4(),
            node_id=uuid4(),
            domain=DomainType.FILE,
            status=DomainReconciliationStatus.CONFIRMED_COMPLETED,
            reason_code="REPLAY_FORBIDDEN",
            action_replayed=True,
        )
