"""Deterministic validation for bounded, confirmation-aware task graphs."""

from __future__ import annotations

from collections import deque
from uuid import UUID

from pc_manager_agent.config.agents import AgentRuntimeLimits
from pc_manager_agent.domain.agents import AgentRole
from pc_manager_agent.domain.task_graph import (
    TaskDomain,
    TaskGraph,
    TaskNode,
    TaskNodeType,
)


class TaskGraphValidationError(RuntimeError):
    """Raised when a proposed graph is unsafe or malformed."""


_DOMAIN_ROLE = {
    TaskDomain.FILE: AgentRole.FILE,
    TaskDomain.SYSTEM: AgentRole.SYSTEM,
    TaskDomain.SOFTWARE: AgentRole.SOFTWARE,
    TaskDomain.OFFICE: AgentRole.OFFICE,
    TaskDomain.BROWSER: AgentRole.BROWSER,
    TaskDomain.OPTIMIZATION: AgentRole.OPTIMIZATION,
    TaskDomain.MEMORY: AgentRole.MEMORY_MANAGER,
    TaskDomain.PROCESS: AgentRole.SYSTEM,
    TaskDomain.STARTUP: AgentRole.SYSTEM,
    TaskDomain.SERVICE: AgentRole.SYSTEM,
    TaskDomain.RESIDUAL: AgentRole.SOFTWARE,
    TaskDomain.CLEANUP: AgentRole.OPTIMIZATION,
}


class TaskGraphValidator:
    """Validate topology, role ownership, limits, and confirmation ancestry."""

    def __init__(self, limits: AgentRuntimeLimits) -> None:
        self._limits = limits

    def validate(self, graph: TaskGraph) -> None:
        """Fail closed before any node is scheduled."""
        if len(graph.nodes) > self._limits.max_task_nodes:
            raise TaskGraphValidationError("Task graph node limit exceeded")
        by_id = {node.node_id: node for node in graph.nodes}
        parents: dict[UUID, set[UUID]] = {node_id: set() for node_id in by_id}
        children: dict[UUID, set[UUID]] = {node_id: set() for node_id in by_id}
        for edge in graph.dependencies:
            if edge.prerequisite_id not in by_id or edge.dependent_id not in by_id:
                raise TaskGraphValidationError("Task dependency references an unknown node")
            parents[edge.dependent_id].add(edge.prerequisite_id)
            children[edge.prerequisite_id].add(edge.dependent_id)
        self._validate_roles(graph.nodes)
        order, depths = self._topological_order(parents, children)
        if len(order) != len(graph.nodes):
            raise TaskGraphValidationError("Task graph contains a cycle")
        if max(depths.values(), default=1) > self._limits.max_graph_depth:
            raise TaskGraphValidationError("Task graph depth limit exceeded")
        for node in graph.nodes:
            if node.node_type is TaskNodeType.EXECUTE_DOMAIN_ACTION and not self._has_ancestor_type(
                node.node_id, parents, by_id, TaskNodeType.WAIT_FOR_CONFIRMATION
            ):
                raise TaskGraphValidationError(
                    "Domain execution nodes require a confirmation-wait ancestor"
                )

    @staticmethod
    def _validate_roles(nodes: tuple[TaskNode, ...]) -> None:
        for node in nodes:
            expected = _DOMAIN_ROLE.get(node.domain)
            if expected is not None and node.agent_role not in {
                expected,
                AgentRole.ORCHESTRATOR,
                AgentRole.VERIFIER,
            }:
                raise TaskGraphValidationError("Task node role does not own its domain")
            if (
                node.node_type is TaskNodeType.WAIT_FOR_CONFIRMATION
                and node.agent_role is not AgentRole.ORCHESTRATOR
            ):
                raise TaskGraphValidationError("Only the Orchestrator may wait for confirmation")
            if node.node_type is TaskNodeType.VERIFY and node.agent_role not in {
                AgentRole.VERIFIER,
                AgentRole.ORCHESTRATOR,
            }:
                raise TaskGraphValidationError("Verification requires the Verifier boundary")

    @staticmethod
    def _topological_order(
        parents: dict[UUID, set[UUID]], children: dict[UUID, set[UUID]]
    ) -> tuple[list[UUID], dict[UUID, int]]:
        indegree = {node_id: len(values) for node_id, values in parents.items()}
        depths = dict.fromkeys(parents, 1)
        ready = deque(node_id for node_id, count in indegree.items() if count == 0)
        order: list[UUID] = []
        while ready:
            current = ready.popleft()
            order.append(current)
            for child in children[current]:
                depths[child] = max(depths[child], depths[current] + 1)
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
        return order, depths

    @staticmethod
    def _has_ancestor_type(
        node_id: UUID,
        parents: dict[UUID, set[UUID]],
        nodes: dict[UUID, TaskNode],
        required: TaskNodeType,
    ) -> bool:
        pending = list(parents[node_id])
        visited: set[UUID] = set()
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            if nodes[current].node_type is required:
                return True
            pending.extend(parents[current])
        return False
