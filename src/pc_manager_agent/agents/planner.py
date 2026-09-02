"""Compile untrusted provider graph drafts into bounded local TaskGraph models."""

from __future__ import annotations

from pc_manager_agent.domain.agents import AgentRole
from pc_manager_agent.domain.task_graph import (
    TaskDependency,
    TaskDomain,
    TaskGraph,
    TaskNode,
    TaskNodeType,
)
from pc_manager_agent.orchestration.agent_selection import agent_role_for_domain
from pc_manager_agent.providers.llm.agent_base import AgentGraphDraft
from pc_manager_agent.safety.task_graph import TaskGraphValidator


class PlannerAgent:
    """Compile provider output only; it cannot confirm or dispatch a domain action."""

    def __init__(self, validator: TaskGraphValidator) -> None:
        self._validator = validator

    def compile(self, user_goal: str, draft: AgentGraphDraft) -> TaskGraph:
        """Assign local UUIDs and roles, ignoring provider-supplied role claims."""
        nodes = {
            item.node_key: TaskNode(
                node_type=item.node_type,
                domain=item.domain,
                agent_role=self._runtime_role(item.node_type, item.domain),
                risk_hint=item.risk_hint,
            )
            for item in draft.nodes
        }
        dependencies = tuple(
            TaskDependency(
                prerequisite_id=nodes[item.prerequisite_key].node_id,
                dependent_id=nodes[item.dependent_key].node_id,
            )
            for item in draft.dependencies
        )
        graph = TaskGraph.create(user_goal, tuple(nodes.values()), dependencies)
        self._validator.validate(graph)
        return graph

    @staticmethod
    def _runtime_role(node_type: TaskNodeType, domain: TaskDomain) -> AgentRole:
        """Assign ownership from finite local policy, never from model output."""
        if node_type is TaskNodeType.WAIT_FOR_CONFIRMATION:
            return AgentRole.ORCHESTRATOR
        if node_type is TaskNodeType.VERIFY:
            return AgentRole.VERIFIER
        if domain is not TaskDomain.GENERAL:
            return agent_role_for_domain(domain)
        if node_type is TaskNodeType.UNDERSTAND:
            return AgentRole.PLANNER
        if node_type is TaskNodeType.SUMMARIZE:
            return AgentRole.ORCHESTRATOR
        raise ValueError("General task node type has no runtime Agent owner")
