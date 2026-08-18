"""Deterministic compiler for one exact service action."""

import re

from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.service_actions import (
    ServiceActionPlan,
    ServiceActionType,
    ServiceObservation,
    ServicePermissionEvidence,
    ServiceStepType,
)


class ServiceActionPlanCompiler:
    """Compile local resolver output; it never accepts model-authored service identity."""

    def compile(
        self,
        user_goal: str,
        target_query: str,
        action: ServiceActionType,
        observation: ServiceObservation,
        permissions: ServicePermissionEvidence,
    ) -> ServiceActionPlan:
        """Create a single-object two-confirmation action plan."""
        steps = {
            ServiceActionType.START: (ServiceStepType.START,),
            ServiceActionType.STOP: (ServiceStepType.STOP,),
            ServiceActionType.RESTART: (ServiceStepType.STOP, ServiceStepType.START),
        }[action]
        risk = RiskLevel.R2_HIGH_IMPACT if action is ServiceActionType.RESTART else RiskLevel.R2
        return ServiceActionPlan(
            user_goal=user_goal,
            summary=(
                f"{action.value.title()} exact Windows service {observation.identity.service_name}"
            ),
            target_query=target_query,
            action=action,
            target_identity=observation.identity,
            target_startup_configuration=observation.startup_configuration,
            expected_state_digest=observation.state_digest(),
            expected_dependency_digest=observation.dependency_digest(),
            expected_permission_digest=permissions.canonical_digest(),
            steps=steps,
            risk_level=risk,
        )


_BATCH_MARKERS = (
    "全部服务",
    "所有服务",
    "没用的服务",
    "unused services",
    "all services",
)


def service_action_intent(text: str) -> ServiceActionType | None:
    """Recognize only finite service state verbs, never a shell command."""
    normalized = text.strip().casefold()
    if not any(marker in normalized for marker in ("服务", "service")):
        return None
    if any(marker in normalized for marker in ("重启", "restart")):
        return ServiceActionType.RESTART
    if any(marker in normalized for marker in ("停止", "停掉", "stop")):
        return ServiceActionType.STOP
    if any(marker in normalized for marker in ("启动", "start")):
        return ServiceActionType.START
    return None


def service_target_query(text: str) -> str:
    """Extract a bounded target hint; local Resolver remains authoritative."""
    normalized = text.strip()
    lowered = normalized.casefold()
    if any(marker in lowered for marker in _BATCH_MARKERS):
        raise ValueError("Stage 4C1 refuses vague or bulk service state changes")
    value = re.sub(
        r"(?i)\b(?:please|service|start|stop|restart)\b|请|帮我|启动|停止|停掉|重启|服务",
        " ",
        normalized,
    )
    value = re.sub(r"(?:的|一下|。|！|!|，|,)+", " ", value)
    value = " ".join(value.split()).strip()
    if not value or value.casefold() in {
        "这个",
        "那个",
        "它",
        "选中的",
        "this",
        "that",
        "it",
    }:
        raise ValueError("Select or name exactly one service; Stage 4C1 will not guess")
    return value
