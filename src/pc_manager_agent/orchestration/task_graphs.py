"""Deterministic Stage 5E task graph construction and persistence conversion."""

from __future__ import annotations

from uuid import UUID

from pc_manager_agent.domain.agents import AgentRole
from pc_manager_agent.domain.computer_tasks import ComputerTaskKind, NodeFailurePolicy
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.task_checkpoints import PersistedTaskGraph
from pc_manager_agent.domain.task_graph import (
    TaskDependency,
    TaskDomain,
    TaskGraph,
    TaskNode,
    TaskNodeType,
)
from pc_manager_agent.domain.task_workflows import DomainType
from pc_manager_agent.orchestration.agent_selection import agent_role_for_domain
from pc_manager_agent.safety.task_graph import TaskGraphValidator

_DOMAIN_MAP = {domain: TaskDomain(domain.value) for domain in DomainType}


class FinalTaskGraphBuilder:
    """Build coordination-only graphs with no global domain execution node."""

    def __init__(self, validator: TaskGraphValidator, safe_read_retry_limit: int) -> None:
        self._validator = validator
        self._safe_read_retry_limit = safe_read_retry_limit

    def build(
        self,
        goal: str,
        domains: tuple[DomainType, ...],
        kind: ComputerTaskKind,
        *,
        version: int = 1,
    ) -> TaskGraph:
        """Create an understood -> domain handoff -> summary graph."""
        if not domains or len(domains) != len(set(domains)):
            raise ValueError("Task domains must be non-empty and unique")
        understand = TaskNode(
            node_type=TaskNodeType.UNDERSTAND,
            domain=TaskDomain.GENERAL,
            agent_role=AgentRole.ORCHESTRATOR,
            risk_hint=RiskLevel.R0,
        )
        nodes: list[TaskNode] = [understand]
        dependencies: list[TaskDependency] = []
        domain_nodes: list[TaskNode] = []
        analysis_only = kind in {
            ComputerTaskKind.ANALYSIS_ONLY,
            ComputerTaskKind.ANALYSIS_AND_REPORT,
            ComputerTaskKind.BROWSER_RESEARCH,
        }
        for domain in domains:
            task_domain = _DOMAIN_MAP[domain]
            node = TaskNode(
                node_type=(TaskNodeType.READ if analysis_only else TaskNodeType.PREPARE_ACTION),
                domain=task_domain,
                agent_role=agent_role_for_domain(task_domain),
                risk_hint=RiskLevel.R0,
                failure_policy=(
                    NodeFailurePolicy.RETRY_SAFE_READ
                    if analysis_only and self._safe_read_retry_limit
                    else NodeFailurePolicy.WAIT_FOR_USER
                ),
                safe_read_retry_limit=(self._safe_read_retry_limit if analysis_only else 0),
                roadmap_label=f"{domain.value}_DOMAIN_HANDOFF",
            )
            nodes.append(node)
            domain_nodes.append(node)
            dependencies.append(
                TaskDependency(
                    prerequisite_id=understand.node_id,
                    dependent_id=node.node_id,
                )
            )
        summarize = TaskNode(
            node_type=TaskNodeType.SUMMARIZE,
            domain=TaskDomain.GENERAL,
            agent_role=AgentRole.ORCHESTRATOR,
            risk_hint=RiskLevel.R0,
            failure_policy=NodeFailurePolicy.CONTINUE_PARTIAL,
        )
        nodes.append(summarize)
        dependencies.extend(
            TaskDependency(prerequisite_id=node.node_id, dependent_id=summarize.node_id)
            for node in domain_nodes
        )
        graph = TaskGraph.create(goal, tuple(nodes), tuple(dependencies)).model_copy(
            update={"version": version}
        )
        self._validator.validate(graph)
        return graph

    @staticmethod
    def persistable(graph: TaskGraph, graph_id: UUID) -> PersistedTaskGraph:
        """Strip the raw goal while retaining an integrity-bound graph version."""
        return PersistedTaskGraph(
            graph_id=graph_id,
            task_id=graph.task_id,
            version=graph.version,
            goal_digest=graph.goal_digest,
            graph_digest=graph.canonical_digest(),
            nodes=graph.nodes,
            dependencies=graph.dependencies,
            created_at=graph.created_at,
        )


def domain_type_for_node(node: TaskNode) -> DomainType | None:
    """Map one graph node to a high-level workflow domain, if any."""
    if node.domain in {TaskDomain.GENERAL, TaskDomain.MEMORY}:
        return None
    return DomainType(node.domain.value)
