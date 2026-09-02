"""Deterministic task-graph construction and content-free lifecycle coordination."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from uuid import UUID

from pc_manager_agent.audit.multi_agent import MultiAgentAuditLogger
from pc_manager_agent.domain.agents import AgentRole
from pc_manager_agent.domain.task_graph import (
    TaskDependency,
    TaskDomain,
    TaskGraph,
    TaskNode,
    TaskNodeStatus,
    TaskNodeType,
)
from pc_manager_agent.domain.task_outcomes import RootTaskStatus, TaskJournalEntry
from pc_manager_agent.orchestration.agent_selection import agent_role_for_domain
from pc_manager_agent.persistence.task_runtime import TaskJournalRepository
from pc_manager_agent.safety.agent_capabilities import AgentCapabilityRegistry


class TaskCoordinationError(RuntimeError):
    """Raised when a task state transition is unknown or unsafe."""


class TaskGraphBuilder:
    """Build confirmation-aware graphs without model-created authority nodes."""

    def build(self, goal: str, domains: tuple[TaskDomain, ...]) -> TaskGraph:
        """Create independent domain branches followed by deterministic aggregation."""
        if not domains or len(domains) != len(set(domains)):
            raise ValueError("Task domains must be non-empty and unique")
        understand = TaskNode(
            node_type=TaskNodeType.UNDERSTAND,
            domain=TaskDomain.GENERAL,
            agent_role=(AgentRole.ORCHESTRATOR if len(domains) == 1 else AgentRole.PLANNER),
        )
        nodes = [understand]
        dependencies: list[TaskDependency] = []
        verification_nodes = []
        for domain in domains:
            role = agent_role_for_domain(domain)
            prepare = TaskNode(
                node_type=TaskNodeType.PREPARE_ACTION,
                domain=domain,
                agent_role=role,
            )
            wait = TaskNode(
                node_type=TaskNodeType.WAIT_FOR_CONFIRMATION,
                domain=domain,
                agent_role=AgentRole.ORCHESTRATOR,
            )
            execute = TaskNode(
                node_type=TaskNodeType.EXECUTE_DOMAIN_ACTION,
                domain=domain,
                agent_role=role,
            )
            verify = TaskNode(
                node_type=TaskNodeType.VERIFY,
                domain=domain,
                agent_role=(AgentRole.ORCHESTRATOR if len(domains) == 1 else AgentRole.VERIFIER),
            )
            nodes.extend((prepare, wait, execute, verify))
            dependencies.extend(
                (
                    TaskDependency(
                        prerequisite_id=understand.node_id, dependent_id=prepare.node_id
                    ),
                    TaskDependency(prerequisite_id=prepare.node_id, dependent_id=wait.node_id),
                    TaskDependency(prerequisite_id=wait.node_id, dependent_id=execute.node_id),
                    TaskDependency(prerequisite_id=execute.node_id, dependent_id=verify.node_id),
                )
            )
            verification_nodes.append(verify)
        summarize = TaskNode(
            node_type=TaskNodeType.SUMMARIZE,
            domain=TaskDomain.GENERAL,
            agent_role=AgentRole.ORCHESTRATOR,
        )
        nodes.append(summarize)
        dependencies.extend(
            TaskDependency(prerequisite_id=verify.node_id, dependent_id=summarize.node_id)
            for verify in verification_nodes
        )
        return TaskGraph.create(goal, tuple(nodes), tuple(dependencies))


class TaskCoordinator:
    """Track graph metadata; it has no tool, confirmation, or domain execution method."""

    def __init__(
        self,
        repository: TaskJournalRepository,
        audit: MultiAgentAuditLogger,
        capabilities: AgentCapabilityRegistry,
    ) -> None:
        self._repository = repository
        self._audit = audit
        self._capabilities = capabilities
        self._graphs: dict[UUID, TaskGraph] = {}
        self._lock = threading.RLock()

    def create(self, graph: TaskGraph) -> TaskJournalEntry:
        """Store a content-free summary while keeping the user goal only in memory."""
        roles = tuple(sorted({node.agent_role.value for node in graph.nodes}))
        now = datetime.now(UTC)
        entry = TaskJournalEntry(
            task_id=graph.task_id,
            graph_digest=graph.canonical_digest(),
            goal_digest=graph.goal_digest,
            status=RootTaskStatus.RUNNING,
            node_count=len(graph.nodes),
            agent_roles=roles,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._repository.create(entry)
            self._graphs[graph.task_id] = graph
        self._audit_entry(entry, "TASK_CREATED")
        return entry

    def graph(self, task_id: UUID) -> TaskGraph:
        """Return an active in-memory graph; restart deliberately loses its goal body."""
        with self._lock:
            try:
                return self._graphs[task_id]
            except KeyError as exc:
                raise TaskCoordinationError("Task graph is unavailable or interrupted") from exc

    def set_status(
        self,
        task_id: UUID,
        status: RootTaskStatus,
        *,
        completed_count: int | None = None,
        blocked_count: int | None = None,
        event_code: str = "TASK_STATUS_CHANGED",
    ) -> TaskJournalEntry:
        """Persist one compare-and-swap transition without changing domain truth."""
        with self._lock:
            current = self._repository.get(task_id)
            if current.status in {
                RootTaskStatus.COMPLETED,
                RootTaskStatus.FAILED,
                RootTaskStatus.CANCELLED,
                RootTaskStatus.BLOCKED,
                RootTaskStatus.INTERRUPTED,
            }:
                raise TaskCoordinationError("Task is already terminal")
            changed = current.model_copy(
                update={
                    "status": status,
                    "completed_count": (
                        current.completed_count if completed_count is None else completed_count
                    ),
                    "blocked_count": (
                        current.blocked_count if blocked_count is None else blocked_count
                    ),
                    "revision": current.revision + 1,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._repository.save(changed, expected_revision=current.revision)
            if status in {
                RootTaskStatus.COMPLETED,
                RootTaskStatus.FAILED,
                RootTaskStatus.CANCELLED,
                RootTaskStatus.BLOCKED,
                RootTaskStatus.INTERRUPTED,
            }:
                self._graphs.pop(task_id, None)
        self._audit_entry(changed, event_code)
        return changed

    def cancel(self, task_id: UUID) -> TaskJournalEntry:
        """Cancel future coordination only; completed domain actions are never undone."""
        current = self._repository.get(task_id)
        return self.set_status(
            task_id,
            RootTaskStatus.CANCELLED,
            completed_count=current.completed_count,
            event_code="TASK_CANCELLED_FUTURE_ONLY",
        )

    def list_recent(self, limit: int = 100) -> tuple[TaskJournalEntry, ...]:
        """Return recent content-free summaries for the Task Center."""
        return self._repository.list_recent(limit)

    def active_node_view(self, task_id: UUID) -> tuple[TaskNode, ...]:
        """Expose graph structure without mutating or authorizing nodes."""
        graph = self.graph(task_id)
        return tuple(
            node.model_copy(
                update={
                    "status": (
                        TaskNodeStatus.COMPLETED
                        if node.node_type is TaskNodeType.UNDERSTAND
                        else TaskNodeStatus.READY
                        if node.node_type is TaskNodeType.PREPARE_ACTION
                        else TaskNodeStatus.PENDING
                    )
                }
            )
            for node in graph.nodes
        )

    def close(self) -> None:
        """Drop volatile goals and release the content-free journal."""
        with self._lock:
            self._graphs.clear()
        self._repository.close()

    def _audit_entry(self, entry: TaskJournalEntry, event_code: str) -> None:
        roles = tuple(AgentRole(role) for role in entry.agent_roles)
        manifests = tuple(self._capabilities.manifest(role) for role in roles)
        self._audit.task_event(
            entry.task_id,
            event_code=event_code,
            status=entry.status,
            graph_digest=entry.graph_digest,
            goal_digest=entry.goal_digest,
            node_count=entry.node_count,
            roles=roles,
            manifest_digests=tuple(manifest.canonical_digest() for manifest in manifests),
            prompt_versions=tuple(
                f"{manifest.role.value.casefold()}-{manifest.version}" for manifest in manifests
            ),
        )
