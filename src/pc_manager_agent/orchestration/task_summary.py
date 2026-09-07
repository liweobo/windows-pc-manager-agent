"""Deterministic cross-domain task summary construction."""

from __future__ import annotations

from pc_manager_agent.domain.computer_tasks import ComputerTask
from pc_manager_agent.domain.task_summaries import StructuredTaskSummary
from pc_manager_agent.domain.task_workflows import (
    DomainResultReceipt,
    DomainResultStatus,
)
from pc_manager_agent.orchestration.domain_workflows import DomainWorkflowRegistry


class TaskSummaryBuilder:
    """Aggregate only owning-domain receipts; never infer success from UI or exit state."""

    def __init__(self, workflows: DomainWorkflowRegistry) -> None:
        self._workflows = workflows

    def build(
        self,
        task: ComputerTask,
        receipts: tuple[DomainResultReceipt, ...],
    ) -> StructuredTaskSummary:
        """Return counts, fixed fact codes, and domain-owned recovery descriptions."""
        statuses = tuple(receipt.status for receipt in receipts)
        recovery = tuple(
            self._workflows.require(receipt.domain).recovery_summary(
                receipt.domain_transaction_ref,
                receipt.rollback_level,
            )
            for receipt in receipts
            if receipt.domain_transaction_ref is not None
        )
        facts = tuple(dict.fromkeys(receipt.result_code for receipt in receipts))
        return StructuredTaskSummary(
            task_id=task.task_id,
            state=task.state,
            completed_verified=statuses.count(DomainResultStatus.COMPLETED_VERIFIED),
            completed_unverified=statuses.count(DomainResultStatus.COMPLETED_UNVERIFIED),
            partial=statuses.count(DomainResultStatus.PARTIAL),
            blocked=statuses.count(DomainResultStatus.BLOCKED),
            failed=statuses.count(DomainResultStatus.FAILED),
            cancelled=statuses.count(DomainResultStatus.CANCELLED),
            changed_count=sum(receipt.changed_count for receipt in receipts),
            unchanged_count=sum(receipt.unchanged_count for receipt in receipts),
            fact_codes=facts,
            recovery=recovery,
        )
