"""Composition root for Stage 5D coordination, Context, and Memory services."""

from __future__ import annotations

import os

from pc_manager_agent.audit.memory import MemoryAuditLogger
from pc_manager_agent.audit.multi_agent import MultiAgentAuditLogger
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.config.settings import AppSettings
from pc_manager_agent.context.governance import ContextGovernanceService
from pc_manager_agent.context.recent_references import RecentEntityReferenceStore
from pc_manager_agent.memory.service import MemoryService
from pc_manager_agent.orchestration.agent_runtime import Stage5DAgentRuntime
from pc_manager_agent.orchestration.agent_selection import AgentSelectionPolicy
from pc_manager_agent.orchestration.delegation import DelegationCoordinator
from pc_manager_agent.orchestration.resource_locks import TaskResourceLockService
from pc_manager_agent.orchestration.task_graph import TaskCoordinator, TaskGraphBuilder
from pc_manager_agent.persistence.memory import MemoryRepository
from pc_manager_agent.persistence.task_runtime import TaskJournalRepository
from pc_manager_agent.safety.agent_capabilities import (
    AgentDelegationPolicy,
    build_agent_capability_registry,
)
from pc_manager_agent.safety.task_goal import TaskGoalBoundaryPolicy
from pc_manager_agent.safety.task_graph import TaskGraphValidator


class AgentServices:
    """Application-wide bounded coordination services with no business Executor."""

    def __init__(
        self,
        runtime: Stage5DAgentRuntime,
        delegation: DelegationCoordinator,
        recent_references: RecentEntityReferenceStore,
        interrupted_task_count: int,
    ) -> None:
        self.runtime = runtime
        self.delegation = delegation
        self.recent_references = recent_references
        self.interrupted_task_count = interrupted_task_count

    def close(self) -> None:
        """Forget ephemeral references and close independent local stores."""
        self.recent_references.clear()
        self.runtime.close()


def build_agent_services(settings: AppSettings, audit: AuditRepository) -> AgentServices:
    """Build a default-deny Stage 5D runtime without touching domain registries."""
    capabilities = build_agent_capability_registry()
    memory_repository = MemoryRepository(settings.database_path)
    memory_repository.initialize()
    task_repository = TaskJournalRepository(settings.database_path)
    interrupted = task_repository.initialize()
    commit = os.getenv("PC_MANAGER_GIT_COMMIT")
    memory = MemoryService(memory_repository, MemoryAuditLogger(audit, commit))
    task_coordinator = TaskCoordinator(
        task_repository,
        MultiAgentAuditLogger(audit, commit),
        capabilities,
    )
    graph_validator = TaskGraphValidator(settings.agent_limits)
    goal_policy = TaskGoalBoundaryPolicy()
    runtime = Stage5DAgentRuntime(
        capabilities,
        graph_validator,
        goal_policy,
        AgentSelectionPolicy(),
        TaskGraphBuilder(),
        task_coordinator,
        ContextGovernanceService(capabilities, settings.agent_limits),
        memory,
        TaskResourceLockService(),
    )
    delegation = DelegationCoordinator(
        runtime.identities,
        AgentDelegationPolicy(capabilities),
        goal_policy,
        settings.agent_limits,
    )
    return AgentServices(
        runtime,
        delegation,
        RecentEntityReferenceStore(ttl_seconds=settings.agent_limits.recent_reference_ttl_seconds),
        len(interrupted),
    )
