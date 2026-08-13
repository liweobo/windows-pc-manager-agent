"""Deterministic Stage 4A intent classification and process-action plan compilation."""

from __future__ import annotations

import re
from uuid import UUID

from pc_manager_agent.domain.process_actions import (
    ProcessActionPlan,
    ProcessActionType,
    ProcessTargetQuery,
    ProcessTargetQueryType,
    ResolvedProcessTarget,
)
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.orchestration.process_target_resolver import ProcessTargetResolver

_PID_PATTERN = re.compile(r"\bpid\s*[:#]?\s*(\d{1,10})\b", re.IGNORECASE)
_ACTION_WORDS = (
    "强制终止",
    "强制结束",
    "force terminate",
    "force kill",
    "结束",
    "终止",
    "关闭",
    "关掉",
    "退出",
    "kill",
    "close",
)


def is_process_action_request(user_goal: str) -> bool:
    """Return whether text asks to mutate current process state."""
    normalized = user_goal.casefold()
    return any(word in normalized for word in _ACTION_WORDS)


def preferred_process_action(user_goal: str) -> ProcessActionType:
    """Apply graceful-first unless the user explicitly requested force termination."""
    normalized = user_goal.casefold()
    force_words = ("强制终止", "强制结束", "force terminate", "force kill")
    return (
        ProcessActionType.FORCE_TERMINATE
        if any(word in normalized for word in force_words)
        else ProcessActionType.REQUEST_GRACEFUL_EXIT
    )


def process_target_query(user_goal: str) -> ProcessTargetQuery:
    """Extract a finite local query; reject vague pronouns and broad batch wording."""
    normalized = user_goal.strip()
    lowered = normalized.casefold()
    if any(term in lowered for term in ("所有", "全部", "all processes", "everything")):
        raise ValueError("Vague bulk process termination is not available; select exact targets")
    pid_match = _PID_PATTERN.search(normalized)
    if pid_match:
        return ProcessTargetQuery(
            query_type=ProcessTargetQueryType.PID,
            pid=int(pid_match.group(1)),
            include_application_group=False,
        )
    if lowered in {"关掉它", "关闭它", "结束它", "kill it", "close it"}:
        raise ValueError("The previous message did not provide one locally bound target; select it")
    target = normalized
    for word in sorted(_ACTION_WORDS, key=len, reverse=True):
        target = target.replace(word, "").replace(word.title(), "")
    target = target.strip(" ：:，,。.!！？?")
    prefixes = ("请把", "请", "帮我把", "帮我", "麻烦把", "麻烦", "把", "将")
    suffixes = ("一下", "可以吗", "行吗", "好吗")
    changed = True
    while changed:
        changed = False
        for prefix in prefixes:
            if target.startswith(prefix):
                target = target.removeprefix(prefix).strip(" ：:，,。.!！？?")
                changed = True
        for suffix in suffixes:
            if target.endswith(suffix):
                target = target.removesuffix(suffix).strip(" ：:，,。.!！？?")
                changed = True
    if not target:
        raise ValueError("Choose a specific current process or application")
    return ProcessTargetQuery(query_type=ProcessTargetQueryType.NAME, text=target)


class ProcessActionPlanCompiler:
    """Resolve fresh local identities and compile one finite immutable process plan."""

    def __init__(self, resolver: ProcessTargetResolver) -> None:
        self._resolver = resolver

    def compile(
        self,
        user_goal: str,
        query: ProcessTargetQuery,
        action: ProcessActionType,
        *,
        parent_transaction_id: UUID | None = None,
    ) -> ProcessActionPlan:
        """Resolve exact current targets and attach deterministic action risk."""
        targets = self._resolver.resolve(query)
        count = sum(len(target.members) for target in targets)
        risk = (
            RiskLevel.R2
            if action is ProcessActionType.REQUEST_GRACEFUL_EXIT
            else RiskLevel.R2_HIGH_IMPACT
        )
        return ProcessActionPlan(
            parent_transaction_id=parent_transaction_id,
            user_goal=user_goal,
            summary=(
                "Request graceful Windows application exit"
                if action is ProcessActionType.REQUEST_GRACEFUL_EXIT
                else "Force terminate an explicitly selected ordinary-user process"
            ),
            action=action,
            target_query=query,
            targets=targets,
            risk_level=risk,
            estimated_processes_affected=count,
        )

    def compile_from_text(self, user_goal: str) -> ProcessActionPlan:
        """Classify finite local intent without allowing an LLM to author a PID."""
        return self.compile(
            user_goal,
            process_target_query(user_goal),
            preferred_process_action(user_goal),
        )

    def compile_resolved(
        self,
        user_goal: str,
        query: ProcessTargetQuery,
        action: ProcessActionType,
        targets: tuple[ResolvedProcessTarget, ...],
        *,
        parent_transaction_id: UUID | None = None,
    ) -> ProcessActionPlan:
        """Compile only freshly resolver-produced targets for a new transaction."""
        count = sum(len(target.members) for target in targets)
        if not targets or count == 0:
            raise ValueError("No current process remains for the requested action")
        risk = (
            RiskLevel.R2
            if action is ProcessActionType.REQUEST_GRACEFUL_EXIT
            else RiskLevel.R2_HIGH_IMPACT
        )
        return ProcessActionPlan(
            parent_transaction_id=parent_transaction_id,
            user_goal=user_goal,
            summary=(
                "Request graceful Windows application exit"
                if action is ProcessActionType.REQUEST_GRACEFUL_EXIT
                else "Force terminate an explicitly selected ordinary-user process"
            ),
            action=action,
            target_query=query,
            targets=targets,
            risk_level=risk,
            estimated_processes_affected=count,
        )
