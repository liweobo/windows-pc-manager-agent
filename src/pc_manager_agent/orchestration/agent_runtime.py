"""Stage 5D coordination facade; it cannot confirm or execute domain actions."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from pc_manager_agent.agents.base import AgentIdentityFactory
from pc_manager_agent.agents.domain_agents import DomainPreparationAgent
from pc_manager_agent.agents.safety_reviewer import SafetyReviewerAgent
from pc_manager_agent.context.governance import ContextGovernanceService
from pc_manager_agent.domain.agents import AgentResult, AgentRole
from pc_manager_agent.domain.context import ContextItem
from pc_manager_agent.domain.memory import MemoryQuery
from pc_manager_agent.domain.task_graph import TaskDomain, TaskGraph, TaskNodeType
from pc_manager_agent.domain.task_outcomes import RootTaskStatus, TaskJournalEntry
from pc_manager_agent.domain.user_requests import RequestRoute, UserRequest
from pc_manager_agent.memory.service import MemoryService
from pc_manager_agent.orchestration.agent_selection import AgentSelectionPolicy
from pc_manager_agent.orchestration.resource_locks import TaskResourceLockService
from pc_manager_agent.orchestration.task_graph import TaskCoordinator, TaskGraphBuilder
from pc_manager_agent.safety.agent_capabilities import AgentCapabilityRegistry
from pc_manager_agent.safety.memory import readable_scopes
from pc_manager_agent.safety.task_goal import TaskGoalBoundary, TaskGoalBoundaryPolicy
from pc_manager_agent.safety.task_graph import TaskGraphValidator


class AgentRuntimeError(RuntimeError):
    """Raised when Stage 5D cannot safely prepare or coordinate a task."""


@dataclass(frozen=True, slots=True)
class PreparedAgentTask:
    """Validated coordination result; no plan or execution authorization is present."""

    graph: TaskGraph
    boundary: TaskGoalBoundary
    journal: TaskJournalEntry
    selected_roles: tuple[AgentRole, ...]


class Stage5DAgentRuntime:
    """Create bounded graphs and domain handoffs while preserving original safety flows."""

    def __init__(
        self,
        capabilities: AgentCapabilityRegistry,
        graph_validator: TaskGraphValidator,
        goal_policy: TaskGoalBoundaryPolicy,
        selection: AgentSelectionPolicy,
        builder: TaskGraphBuilder,
        coordinator: TaskCoordinator,
        contexts: ContextGovernanceService,
        memory: MemoryService,
        resources: TaskResourceLockService,
    ) -> None:
        self.capabilities = capabilities
        self.identities = AgentIdentityFactory(capabilities)
        self._graph_validator = graph_validator
        self._goal_policy = goal_policy
        self._selection = selection
        self._builder = builder
        self.coordinator = coordinator
        self.contexts = contexts
        self.memory = memory
        self.resources = resources
        self._boundaries: dict[UUID, TaskGoalBoundary] = {}

    def prepare_request(self, request: UserRequest, route: RequestRoute) -> PreparedAgentTask:
        """Build and review a graph for the exact shared dispatcher decision."""
        domains = self._selection.domains_for_route(route)
        return self.prepare_domains(request.text, domains)

    def prepare_domains(self, user_goal: str, domains: tuple[TaskDomain, ...]) -> PreparedAgentTask:
        """Prepare an explicit multi-domain graph without running any domain action."""
        graph = self._builder.build(user_goal, domains)
        roles = self._selection.roles_for_domains(domains)
        capabilities = tuple(
            sorted(
                {tool for role in roles for tool in self.capabilities.manifest(role).proposed_tools}
            )
        )
        boundary = TaskGoalBoundary(
            task_id=graph.task_id,
            goal_digest=graph.goal_digest,
            allowed_domains=domains,
            allowed_capabilities=capabilities,
        )
        reviewer = SafetyReviewerAgent(
            self.identities.create(AgentRole.SAFETY_REVIEWER),
            self._graph_validator,
            self._goal_policy,
        )
        reviewer.review(graph, boundary)
        journal = self.coordinator.create(graph)
        self._boundaries[graph.task_id] = boundary
        return PreparedAgentTask(graph, boundary, journal, roles)

    def prepare_domain_handoff(
        self,
        task_id: UUID,
        domain: TaskDomain,
        *,
        items: tuple[ContextItem, ...] = (),
    ) -> AgentResult:
        """Create a reference-only proposal for one existing domain preparation UI."""
        graph = self.coordinator.graph(task_id)
        try:
            boundary = self._boundaries[task_id]
        except KeyError as exc:
            raise AgentRuntimeError("Task boundary is unavailable after restart") from exc
        item_references = {item.reference.reference_id for item in items}
        if not item_references.issubset(boundary.allowed_reference_ids):
            raise AgentRuntimeError("Domain handoff expands the approved reference set")
        nodes = tuple(
            node
            for node in graph.nodes
            if node.domain is domain and node.node_type is TaskNodeType.PREPARE_ACTION
        )
        if len(nodes) != 1:
            raise AgentRuntimeError("Task does not contain one matching preparation node")
        node = nodes[0]
        identity = self.identities.create(node.agent_role)
        scopes = readable_scopes(identity.role)
        memory = (
            self.memory.query(
                MemoryQuery(
                    agent_role=identity.role,
                    scopes=scopes,
                    reason_code="DOMAIN_PREPARATION_CONTEXT",
                )
            )
            if scopes
            else None
        )
        context = self.contexts.build(
            identity,
            task_id=task_id,
            node_id=node.node_id,
            user_goal=graph.goal,
            goal_digest=graph.goal_digest,
            items=items,
            memory=memory,
        )
        result = DomainPreparationAgent(identity, domain).prepare(node, context)
        self.coordinator.set_status(
            task_id,
            RootTaskStatus.WAITING_CONFIRMATION,
            completed_count=2,
            event_code="DOMAIN_PREPARATION_HANDOFF",
        )
        return result

    def cancel(self, task_id: UUID) -> TaskJournalEntry:
        """Stop future coordination, clear scheduling locks, and never invoke Undo."""
        self.resources.release_task(task_id)
        self._boundaries.pop(task_id, None)
        return self.coordinator.cancel(task_id)

    def close(self) -> None:
        """Forget volatile boundaries and close independent task and Memory stores."""
        self._boundaries.clear()
        self.coordinator.close()
        self.memory.close()
