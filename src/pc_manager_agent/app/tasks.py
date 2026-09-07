"""Composition root for the Stage 5E Final Orchestrator."""

from __future__ import annotations

import os
from dataclasses import dataclass

from pc_manager_agent import __version__
from pc_manager_agent.audit.computer_tasks import ComputerTaskAuditLogger
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.config.settings import AppSettings
from pc_manager_agent.domain.computer_tasks import TaskPolicySnapshot
from pc_manager_agent.orchestration.domain_workflows import (
    DomainWorkflowRegistry,
    build_default_domain_workflow_registry,
)
from pc_manager_agent.orchestration.final_orchestrator import FinalOrchestrator
from pc_manager_agent.orchestration.task_graphs import FinalTaskGraphBuilder
from pc_manager_agent.persistence.computer_tasks import ComputerTaskRepository
from pc_manager_agent.safety.task_graph import TaskGraphValidator
from pc_manager_agent.safety.task_templates import TaskTemplateRegistry


@dataclass(frozen=True, slots=True)
class FinalTaskServices:
    """Application-owned task services with no low-level business adapter exposure."""

    orchestrator: FinalOrchestrator
    workflows: DomainWorkflowRegistry
    templates: TaskTemplateRegistry
    interrupted_task_count: int

    def close(self) -> None:
        """Drop volatile task content and release durable task storage."""
        self.orchestrator.close()


def build_final_task_services(
    settings: AppSettings,
    audit_repository: AuditRepository,
) -> FinalTaskServices:
    """Build the disabled-by-authority coordination layer over the shared SQLite file."""
    repository = ComputerTaskRepository(settings.database_path)
    interrupted = repository.initialize()
    workflows = build_default_domain_workflow_registry()
    policy = TaskPolicySnapshot(
        safety_policy_version="stage5e-safety-v1",
        tool_registry_version="stage5e-high-level-workflows-v1",
        agent_capability_version="stage5d-capabilities-v1",
        application_version=__version__,
    )
    graph_builder = FinalTaskGraphBuilder(
        TaskGraphValidator(settings.agent_limits),
        settings.task_limits.max_safe_read_retries,
    )
    orchestrator = FinalOrchestrator(
        repository,
        workflows,
        graph_builder,
        settings.task_limits,
        policy,
        ComputerTaskAuditLogger(
            audit_repository,
            os.getenv("PC_MANAGER_GIT_COMMIT"),
        ),
    )
    return FinalTaskServices(
        orchestrator=orchestrator,
        workflows=workflows,
        templates=TaskTemplateRegistry(),
        interrupted_task_count=len(interrupted),
    )
