"""Advisory Agent wrapper around deterministic graph and goal policies."""

from pc_manager_agent.domain.agents import (
    AgentFinding,
    AgentResult,
    AgentResultStatus,
    AgentRole,
    AgentRuntimeIdentity,
)
from pc_manager_agent.domain.task_graph import TaskGraph
from pc_manager_agent.safety.task_goal import TaskGoalBoundary, TaskGoalBoundaryPolicy
from pc_manager_agent.safety.task_graph import TaskGraphValidator


class SafetyReviewerAgent:
    """Report policy results; it cannot approve, confirm, or execute a task."""

    def __init__(
        self,
        identity: AgentRuntimeIdentity,
        graph_validator: TaskGraphValidator,
        goal_policy: TaskGoalBoundaryPolicy,
    ) -> None:
        if identity.role is not AgentRole.SAFETY_REVIEWER:
            raise ValueError("SafetyReviewerAgent requires its runtime-owned role")
        self._identity = identity
        self._graph_validator = graph_validator
        self._goal_policy = goal_policy

    @property
    def identity(self) -> AgentRuntimeIdentity:
        """Return the runtime-created safety-review identity."""
        return self._identity

    def review(self, graph: TaskGraph, boundary: TaskGoalBoundary) -> AgentResult:
        """Run deterministic checks and return a non-authoritative finding."""
        self._graph_validator.validate(graph)
        self._goal_policy.validate_graph(boundary, graph)
        return AgentResult(
            task_id=graph.task_id,
            node_id=graph.nodes[0].node_id,
            status=AgentResultStatus.COMPLETED,
            findings=(
                AgentFinding(
                    code="DETERMINISTIC_REVIEW_PASSED",
                    summary="Graph structure and goal boundaries passed deterministic review.",
                ),
            ),
        )
