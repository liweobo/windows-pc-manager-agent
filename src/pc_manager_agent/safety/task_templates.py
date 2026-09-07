"""Closed safe task-template policy; templates never pre-authorize actions."""

from __future__ import annotations

from dataclasses import dataclass

from pc_manager_agent.domain.computer_tasks import AutonomyLevel, ComputerTaskKind
from pc_manager_agent.domain.task_workflows import DomainType
from pc_manager_agent.safety.final_orchestrator import FinalOrchestratorSafetyError


@dataclass(frozen=True, slots=True)
class SafeTaskTemplate:
    """One versioned high-level task shape with no raw tools or confirmations."""

    code: str
    version: str
    title: str
    kind: ComputerTaskKind
    autonomy: AutonomyLevel
    domains: tuple[DomainType, ...]
    analysis_only: bool


class TaskTemplateRegistry:
    """Finite V1 quick actions; callers cannot register templates at runtime."""

    def __init__(self) -> None:
        templates = (
            SafeTaskTemplate(
                "PC_HEALTH_CHECK",
                "1",
                "电脑健康检查",
                ComputerTaskKind.ANALYSIS_ONLY,
                AutonomyLevel.PLAN_AND_ANALYZE,
                (DomainType.SYSTEM, DomainType.OPTIMIZATION),
                True,
            ),
            SafeTaskTemplate(
                "DISK_SPACE_ANALYSIS",
                "1",
                "磁盘空间分析",
                ComputerTaskKind.ANALYSIS_ONLY,
                AutonomyLevel.PLAN_AND_ANALYZE,
                (DomainType.SYSTEM, DomainType.OPTIMIZATION, DomainType.FILE),
                True,
            ),
            SafeTaskTemplate(
                "STARTUP_REVIEW",
                "1",
                "启动项审查",
                ComputerTaskKind.GUIDED_ACTION,
                AutonomyLevel.GUIDED_EXECUTION,
                (DomainType.STARTUP,),
                False,
            ),
            SafeTaskTemplate(
                "HEALTH_REPORT",
                "1",
                "电脑健康检查与办公报告",
                ComputerTaskKind.ANALYSIS_AND_REPORT,
                AutonomyLevel.GUIDED_EXECUTION,
                (DomainType.SYSTEM, DomainType.OPTIMIZATION, DomainType.OFFICE),
                False,
            ),
            SafeTaskTemplate(
                "WEB_RESEARCH_REPORT",
                "1",
                "网页研究与办公报告",
                ComputerTaskKind.MIXED_COMPUTER_TASK,
                AutonomyLevel.GUIDED_EXECUTION,
                (DomainType.BROWSER, DomainType.OFFICE),
                False,
            ),
        )
        self._templates = {item.code: item for item in templates}

    def get(self, code: str) -> SafeTaskTemplate:
        """Return one immutable allow-listed template or fail closed."""
        try:
            return self._templates[code]
        except KeyError as exc:
            raise FinalOrchestratorSafetyError("Unknown task template") from exc

    def list(self) -> tuple[SafeTaskTemplate, ...]:
        """Return templates in stable declaration order."""
        return tuple(self._templates.values())
