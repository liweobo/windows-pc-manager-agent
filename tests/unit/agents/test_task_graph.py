from __future__ import annotations

import hashlib
from uuid import uuid4

import pytest

from pc_manager_agent.agents.planner import PlannerAgent
from pc_manager_agent.config.agents import AgentRuntimeLimits
from pc_manager_agent.domain.agents import AgentRole
from pc_manager_agent.domain.task_graph import (
    TaskDependency,
    TaskDomain,
    TaskGraph,
    TaskNode,
    TaskNodeType,
)
from pc_manager_agent.orchestration.agent_selection import AgentSelectionPolicy
from pc_manager_agent.orchestration.task_graph import TaskGraphBuilder
from pc_manager_agent.providers.llm.agent_base import (
    AgentGraphDraft,
    AgentGraphDraftDependency,
    AgentGraphDraftNode,
)
from pc_manager_agent.safety.task_goal import (
    TaskGoalBoundary,
    TaskGoalBoundaryError,
    TaskGoalBoundaryPolicy,
)
from pc_manager_agent.safety.task_graph import TaskGraphValidationError, TaskGraphValidator


def test_simple_task_uses_minimum_agents_and_valid_confirmation_chain() -> None:
    graph = TaskGraphBuilder().build("C drive free space", (TaskDomain.SYSTEM,))
    TaskGraphValidator(AgentRuntimeLimits()).validate(graph)
    assert AgentSelectionPolicy().roles_for_domains((TaskDomain.SYSTEM,)) == (
        AgentRole.ORCHESTRATOR,
        AgentRole.SYSTEM,
    )
    assert {node.agent_role for node in graph.nodes} == {
        AgentRole.ORCHESTRATOR,
        AgentRole.SYSTEM,
    }
    assert "C drive free space" not in graph.model_dump_json()


def test_graph_rejects_cycles_limits_and_execution_without_confirmation() -> None:
    first = TaskNode(
        node_type=TaskNodeType.READ,
        domain=TaskDomain.SYSTEM,
        agent_role=AgentRole.SYSTEM,
    )
    second = TaskNode(
        node_type=TaskNodeType.ANALYZE,
        domain=TaskDomain.SYSTEM,
        agent_role=AgentRole.SYSTEM,
    )
    cyclic = TaskGraph.create(
        "goal",
        (first, second),
        (
            TaskDependency(prerequisite_id=first.node_id, dependent_id=second.node_id),
            TaskDependency(prerequisite_id=second.node_id, dependent_id=first.node_id),
        ),
    )
    validator = TaskGraphValidator(AgentRuntimeLimits())
    with pytest.raises(TaskGraphValidationError, match="cycle"):
        validator.validate(cyclic)
    with pytest.raises(TaskGraphValidationError, match="limit"):
        TaskGraphValidator(AgentRuntimeLimits(max_task_nodes=1)).validate(cyclic)

    execute = TaskNode(
        node_type=TaskNodeType.EXECUTE_DOMAIN_ACTION,
        domain=TaskDomain.FILE,
        agent_role=AgentRole.FILE,
    )
    with pytest.raises(TaskGraphValidationError, match="confirmation"):
        validator.validate(TaskGraph.create("move", (execute,)))


def test_graph_rejects_unknown_edges_depth_and_role_boundary_violations() -> None:
    validator = TaskGraphValidator(AgentRuntimeLimits())
    read = TaskNode(
        node_type=TaskNodeType.READ,
        domain=TaskDomain.SYSTEM,
        agent_role=AgentRole.SYSTEM,
    )
    unknown = TaskDependency(prerequisite_id=uuid4(), dependent_id=read.node_id)
    with pytest.raises(TaskGraphValidationError, match="unknown node"):
        validator.validate(TaskGraph.create("read", (read,), (unknown,)))

    wrong_owner = read.model_copy(update={"agent_role": AgentRole.FILE})
    with pytest.raises(TaskGraphValidationError, match="does not own"):
        validator.validate(TaskGraph.create("read", (wrong_owner,)))
    wrong_waiter = read.model_copy(
        update={"node_type": TaskNodeType.WAIT_FOR_CONFIRMATION, "agent_role": AgentRole.SYSTEM}
    )
    with pytest.raises(TaskGraphValidationError, match="Orchestrator"):
        validator.validate(TaskGraph.create("wait", (wrong_waiter,)))
    wrong_verifier = read.model_copy(
        update={"node_type": TaskNodeType.VERIFY, "agent_role": AgentRole.SYSTEM}
    )
    with pytest.raises(TaskGraphValidationError, match="Verifier"):
        validator.validate(TaskGraph.create("verify", (wrong_verifier,)))

    first = read
    second = TaskNode(
        node_type=TaskNodeType.ANALYZE,
        domain=TaskDomain.SYSTEM,
        agent_role=AgentRole.SYSTEM,
    )
    deep = TaskGraph.create(
        "deep",
        (first, second),
        (TaskDependency(prerequisite_id=first.node_id, dependent_id=second.node_id),),
    )
    with pytest.raises(TaskGraphValidationError, match="depth"):
        TaskGraphValidator(AgentRuntimeLimits(max_graph_depth=1, max_delegation_depth=1)).validate(
            deep
        )


def test_graph_accepts_transitive_confirmation_ancestor() -> None:
    wait = TaskNode(
        node_type=TaskNodeType.WAIT_FOR_CONFIRMATION,
        domain=TaskDomain.FILE,
        agent_role=AgentRole.ORCHESTRATOR,
    )
    prepare = TaskNode(
        node_type=TaskNodeType.PREPARE_ACTION,
        domain=TaskDomain.FILE,
        agent_role=AgentRole.FILE,
    )
    execute = TaskNode(
        node_type=TaskNodeType.EXECUTE_DOMAIN_ACTION,
        domain=TaskDomain.FILE,
        agent_role=AgentRole.FILE,
    )
    graph = TaskGraph.create(
        "move",
        (wait, prepare, execute),
        (
            TaskDependency(prerequisite_id=wait.node_id, dependent_id=prepare.node_id),
            TaskDependency(prerequisite_id=prepare.node_id, dependent_id=execute.node_id),
        ),
    )
    TaskGraphValidator(AgentRuntimeLimits()).validate(graph)


def test_goal_boundary_blocks_domain_expansion() -> None:
    graph = TaskGraphBuilder().build("inspect system", (TaskDomain.SYSTEM,))
    boundary = TaskGoalBoundary(
        task_id=graph.task_id,
        goal_digest=graph.goal_digest,
        allowed_domains=(TaskDomain.FILE,),
    )
    with pytest.raises(TaskGoalBoundaryError, match="domain"):
        TaskGoalBoundaryPolicy().validate_graph(boundary, graph)


def test_goal_boundary_rejects_empty_duplicates_identity_delegation_and_proposals() -> None:
    graph = TaskGraphBuilder().build("inspect system", (TaskDomain.SYSTEM,))
    with pytest.raises(ValueError, match="at least one"):
        TaskGoalBoundary(task_id=graph.task_id, goal_digest=graph.goal_digest, allowed_domains=())
    with pytest.raises(ValueError, match="unique"):
        TaskGoalBoundary(
            task_id=graph.task_id,
            goal_digest=graph.goal_digest,
            allowed_domains=(TaskDomain.SYSTEM, TaskDomain.SYSTEM),
        )

    boundary = TaskGoalBoundary(
        task_id=graph.task_id,
        goal_digest=graph.goal_digest,
        allowed_domains=(TaskDomain.SYSTEM,),
        allowed_capabilities=("system.info",),
        allowed_reference_ids=("ref-1",),
    )
    wrong_graph = graph.model_copy(update={"task_id": uuid4()})
    with pytest.raises(TaskGoalBoundaryError, match="root goal"):
        TaskGoalBoundaryPolicy().validate_graph(boundary, wrong_graph)

    from datetime import UTC, datetime, timedelta

    from pc_manager_agent.domain.agents import (
        AgentDelegationRequest,
        AgentToolProposal,
        DelegationBudget,
    )
    from pc_manager_agent.domain.context import ContextReference, ContextSourceKind

    now = datetime.now(UTC)
    request = AgentDelegationRequest(
        parent_task_id=graph.task_id,
        parent_node_id=graph.nodes[0].node_id,
        target_role=AgentRole.SYSTEM,
        objective_code="READ_SYSTEM",
        goal_digest=graph.goal_digest,
        allowed_capabilities=("system.info",),
        budget=DelegationBudget(
            depth=1, delegations_remaining=1, model_calls_remaining=1, context_chars=256
        ),
        created_at=now,
        expires_at=now + timedelta(seconds=30),
    )
    policy = TaskGoalBoundaryPolicy()
    policy.validate_delegation(boundary, request)
    with pytest.raises(TaskGoalBoundaryError, match="root goal"):
        policy.validate_delegation(boundary, request.model_copy(update={"parent_task_id": uuid4()}))
    with pytest.raises(TaskGoalBoundaryError, match="capabilities"):
        policy.validate_delegation(
            boundary,
            request.model_copy(update={"allowed_capabilities": ("system.processes",)}),
        )
    reference = ContextReference(
        reference_id="outside",
        source_kind=ContextSourceKind.STRUCTURED_RESULT,
        owner_domain="SYSTEM",
        content_digest="0" * 64,
    )
    with pytest.raises(TaskGoalBoundaryError, match="references"):
        policy.validate_delegation(
            boundary, request.model_copy(update={"context_refs": (reference,)})
        )

    proposal = AgentToolProposal(
        task_id=graph.task_id,
        node_id=graph.nodes[0].node_id,
        tool_name="system.info",
        arguments={},
        rationale_code="READ_SYSTEM",
    )
    policy.validate_proposal(boundary, proposal)
    with pytest.raises(TaskGoalBoundaryError, match="another task"):
        policy.validate_proposal(boundary, proposal.model_copy(update={"task_id": uuid4()}))
    with pytest.raises(TaskGoalBoundaryError, match="capability"):
        policy.validate_proposal(
            boundary, proposal.model_copy(update={"tool_name": "system.processes"})
        )


def test_planner_compiles_local_ids_and_rejects_unsafe_draft() -> None:
    draft = AgentGraphDraft(
        nodes=(
            AgentGraphDraftNode(
                node_key="prepare",
                node_type=TaskNodeType.PREPARE_ACTION,
                domain=TaskDomain.FILE,
                agent_role=AgentRole.FILE,
            ),
            AgentGraphDraftNode(
                node_key="execute",
                node_type=TaskNodeType.EXECUTE_DOMAIN_ACTION,
                domain=TaskDomain.FILE,
                agent_role=AgentRole.FILE,
            ),
        ),
        dependencies=(
            AgentGraphDraftDependency(prerequisite_key="prepare", dependent_key="execute"),
        ),
    )
    with pytest.raises(TaskGraphValidationError, match="confirmation"):
        PlannerAgent(TaskGraphValidator(AgentRuntimeLimits())).compile("move", draft)

    goal = "exact goal"
    with pytest.raises(ValueError, match="digest"):
        TaskGraph(
            task_id=uuid4(),
            goal=goal,
            goal_digest=hashlib.sha256(b"different").hexdigest(),
            nodes=(
                TaskNode(
                    node_type=TaskNodeType.READ,
                    domain=TaskDomain.SYSTEM,
                    agent_role=AgentRole.SYSTEM,
                ),
            ),
        )


def test_planner_ignores_model_role_claims_and_assigns_runtime_owners() -> None:
    draft = AgentGraphDraft(
        nodes=(
            AgentGraphDraftNode(
                node_key="understand",
                node_type=TaskNodeType.UNDERSTAND,
                domain=TaskDomain.GENERAL,
                agent_role=AgentRole.AUDIT_MANAGER,
            ),
            AgentGraphDraftNode(
                node_key="read",
                node_type=TaskNodeType.READ,
                domain=TaskDomain.SYSTEM,
                agent_role=AgentRole.SOFTWARE,
            ),
            AgentGraphDraftNode(
                node_key="verify",
                node_type=TaskNodeType.VERIFY,
                domain=TaskDomain.SYSTEM,
                agent_role=AgentRole.SYSTEM,
            ),
            AgentGraphDraftNode(
                node_key="summary",
                node_type=TaskNodeType.SUMMARIZE,
                domain=TaskDomain.GENERAL,
                agent_role=AgentRole.FILE,
            ),
        ),
        dependencies=(
            AgentGraphDraftDependency(prerequisite_key="understand", dependent_key="read"),
            AgentGraphDraftDependency(prerequisite_key="read", dependent_key="verify"),
            AgentGraphDraftDependency(prerequisite_key="verify", dependent_key="summary"),
        ),
    )
    graph = PlannerAgent(TaskGraphValidator(AgentRuntimeLimits())).compile("inspect", draft)
    assert tuple(node.agent_role for node in graph.nodes) == (
        AgentRole.PLANNER,
        AgentRole.SYSTEM,
        AgentRole.VERIFIER,
        AgentRole.ORCHESTRATOR,
    )

    invalid_general = AgentGraphDraft(
        nodes=(
            AgentGraphDraftNode(
                node_key="read",
                node_type=TaskNodeType.READ,
                domain=TaskDomain.GENERAL,
                agent_role=AgentRole.ORCHESTRATOR,
            ),
        )
    )
    with pytest.raises(ValueError, match="no runtime Agent owner"):
        PlannerAgent(TaskGraphValidator(AgentRuntimeLimits())).compile("read", invalid_general)
