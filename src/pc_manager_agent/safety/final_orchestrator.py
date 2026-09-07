"""Global Stage 5E policies that keep coordination separate from authorization."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID

from pc_manager_agent.domain.computer_tasks import (
    AutonomyLevel,
    ComputerTask,
    ComputerTaskState,
    TaskPlanConfirmation,
    TaskPlanConfirmationState,
)
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.task_graph import TaskGraph, TaskNodeType
from pc_manager_agent.domain.task_workflows import DomainPreparationResult, DomainResultReceipt
from pc_manager_agent.safety.voice import SensitiveTranscriptRedactor


class FinalOrchestratorSafetyError(RuntimeError):
    """Raised when a global task boundary would be weakened or confused."""


class ContinuationDecision(StrEnum):
    """Finite next-step decisions; none directly executes a domain action."""

    CONTINUE_READ_ONLY = "CONTINUE_READ_ONLY"
    WAIT_FOR_DOMAIN_CONFIRMATION = "WAIT_FOR_DOMAIN_CONFIRMATION"
    WAIT_FOR_USER = "WAIT_FOR_USER"
    STOP = "STOP"


class SafeTaskSummaryPolicy:
    """Persist a short local label without copying a sensitive raw request."""

    _assignment = re.compile(
        r"(?i)(api[_ -]?key|password|passwd|cookie|authorization|bearer|token|mfa)\s*[:=]"
    )

    def summarize(self, goal: str, fallback: str = "受控电脑任务") -> str:
        """Return a bounded display label or a generic label when content looks sensitive."""
        normalized = " ".join(unicodedata.normalize("NFKC", goal).split())
        if (
            not normalized
            or SensitiveTranscriptRedactor().contains_sensitive(normalized)
            or self._assignment.search(normalized)
        ):
            return fallback
        return normalized[:240]


class TaskPlanConfirmationPolicy:
    """Issue and resolve consent for exact R0 coordination nodes only."""

    def request(
        self,
        task: ComputerTask,
        graph: TaskGraph,
        *,
        scope_digest: str,
        ttl_seconds: int,
        now: datetime | None = None,
    ) -> TaskPlanConfirmation:
        """Build a non-write confirmation bound to task, graph, policy, and scope."""
        observed = now or datetime.now(UTC)
        if task.state is not ComputerTaskState.AWAITING_PLAN_CONFIRMATION:
            raise FinalOrchestratorSafetyError("Task is not awaiting plan confirmation")
        if task.graph_version != graph.version or task.graph_digest != graph.canonical_digest():
            raise FinalOrchestratorSafetyError("Task graph changed before plan confirmation")
        read_only = tuple(
            node.node_id
            for node in graph.nodes
            if node.risk_hint is RiskLevel.R0
            and node.node_type is not TaskNodeType.EXECUTE_DOMAIN_ACTION
        )
        if not read_only:
            raise FinalOrchestratorSafetyError("Task plan contains no confirmable read-only work")
        if len(scope_digest) != 64:
            raise FinalOrchestratorSafetyError("Task plan scope digest is invalid")
        return TaskPlanConfirmation(
            task_id=task.task_id,
            graph_id=task.graph_id,
            graph_version=task.graph_version,
            goal_digest=task.goal_digest,
            graph_digest=task.graph_digest,
            policy_digest=task.policy.canonical_digest(),
            scope_digest=scope_digest,
            read_only_node_ids=read_only,
            created_at=observed,
            expires_at=observed + timedelta(seconds=ttl_seconds),
        )

    def resolve(
        self,
        confirmation: TaskPlanConfirmation,
        task: ComputerTask,
        *,
        approved: bool,
        now: datetime | None = None,
    ) -> TaskPlanConfirmation:
        """Resolve one exact pending consent; it still cannot authorize a domain write."""
        observed = now or datetime.now(UTC)
        if confirmation.state is not TaskPlanConfirmationState.PENDING:
            raise FinalOrchestratorSafetyError("Task plan confirmation was already resolved")
        if observed >= confirmation.expires_at:
            return confirmation.model_copy(
                update={"state": TaskPlanConfirmationState.EXPIRED, "resolved_at": observed}
            )
        expected = (
            task.task_id,
            task.graph_id,
            task.graph_version,
            task.goal_digest,
            task.graph_digest,
            task.policy.canonical_digest(),
        )
        actual = (
            confirmation.task_id,
            confirmation.graph_id,
            confirmation.graph_version,
            confirmation.goal_digest,
            confirmation.graph_digest,
            confirmation.policy_digest,
        )
        if actual != expected:
            raise FinalOrchestratorSafetyError("Task plan confirmation binding changed")
        return confirmation.model_copy(
            update={
                "state": (
                    TaskPlanConfirmationState.APPROVED
                    if approved
                    else TaskPlanConfirmationState.REJECTED
                ),
                "resolved_at": observed,
            }
        )


class ActionContinuationPolicy:
    """Allow automatic continuation only across already-confirmed R0 coordination."""

    def decide(
        self,
        preparation: DomainPreparationResult,
        *,
        task_plan_confirmed: bool,
    ) -> ContinuationDecision:
        """Return a wait decision for every new write-capable domain action."""
        if preparation.status.value in {"BLOCKED", "FAILED", "CANCELLED"}:
            return ContinuationDecision.STOP
        if preparation.risk_level is RiskLevel.R0:
            return (
                ContinuationDecision.CONTINUE_READ_ONLY
                if task_plan_confirmed
                else ContinuationDecision.WAIT_FOR_USER
            )
        if preparation.requires_domain_confirmation:
            return ContinuationDecision.WAIT_FOR_DOMAIN_CONFIRMATION
        raise FinalOrchestratorSafetyError("Write preparation omitted domain confirmation")

    def may_retry(self, *, risk: RiskLevel, read_only: bool, attempts: int, limit: int) -> bool:
        """Permit only bounded retries of side-effect-free R0 work."""
        return risk is RiskLevel.R0 and read_only and attempts < limit


class GlobalSafetyInvariantGuard:
    """Validate cross-domain receipts without becoming a new execution authority."""

    def require_guided_autonomy(self, level: AutonomyLevel) -> None:
        """Reject values outside the closed V1 autonomy enum."""
        if level not in {
            AutonomyLevel.EXPLAIN_ONLY,
            AutonomyLevel.PLAN_AND_ANALYZE,
            AutonomyLevel.GUIDED_EXECUTION,
        }:
            raise FinalOrchestratorSafetyError("Unsupported autonomy level")

    def validate_preparation(self, result: DomainPreparationResult) -> None:
        """Reject authority or confirmation downgrades in a domain handoff."""
        if result.execution_authorized:
            raise FinalOrchestratorSafetyError("Domain preparation claimed execution authority")
        if result.risk_level.severity >= RiskLevel.R1.severity and not (
            result.requires_domain_confirmation
        ):
            raise FinalOrchestratorSafetyError("Domain write omitted its confirmation boundary")

    def validate_receipt(
        self,
        receipt: DomainResultReceipt,
        *,
        task_id: UUID,
        node_id: UUID,
        graph_version: int,
    ) -> None:
        """Require exact task/node/version ownership before accepting domain truth."""
        if (receipt.task_id, receipt.node_id, receipt.graph_version) != (
            task_id,
            node_id,
            graph_version,
        ):
            raise FinalOrchestratorSafetyError("Domain receipt belongs to another task node")


def canonical_scope_digest(values: tuple[str, ...]) -> str:
    """Hash a unique sorted scope without persisting its underlying sensitive content."""
    if len(values) != len(set(values)):
        raise FinalOrchestratorSafetyError("Task scope references must be unique")
    return hashlib.sha256("\x00".join(sorted(values)).encode()).hexdigest()
