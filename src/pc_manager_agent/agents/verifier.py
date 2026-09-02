"""Deterministic task aggregation where domain evidence outranks model opinion."""

from __future__ import annotations

from uuid import UUID

from pc_manager_agent.domain.task_outcomes import (
    FactProvenance,
    TaskFact,
    TaskOutcome,
    TaskOutcomeStatus,
)


class TaskOutcomeAggregator:
    """Select the strongest fact per code and truthfully report partial work."""

    def aggregate(
        self,
        task_id: UUID,
        facts: tuple[TaskFact, ...],
        *,
        completed: tuple[UUID, ...],
        incomplete: tuple[UUID, ...],
        cancelled: bool = False,
        blocked: bool = False,
    ) -> TaskOutcome:
        """Build an outcome without majority voting or exit-code inference."""
        strongest: dict[str, TaskFact] = {}
        for fact in facts:
            current = strongest.get(fact.code)
            if current is None or fact.provenance.precedence > current.provenance.precedence:
                strongest[fact.code] = fact
        selected = tuple(sorted(strongest.values(), key=lambda item: item.code))
        if blocked:
            status, code = TaskOutcomeStatus.BLOCKED, "TASK_BLOCKED"
        elif cancelled and not completed:
            status, code = TaskOutcomeStatus.CANCELLED, "TASK_CANCELLED"
        elif incomplete:
            status, code = TaskOutcomeStatus.PARTIALLY_COMPLETED, "TASK_PARTIAL"
        else:
            status, code = TaskOutcomeStatus.COMPLETED, "TASK_COMPLETED"
        if any(
            fact.provenance is FactProvenance.MODEL_INFERENCE and fact.code.endswith("_VERIFIED")
            for fact in selected
        ):
            status, code = TaskOutcomeStatus.PARTIALLY_COMPLETED, "MODEL_CANNOT_VERIFY"
        return TaskOutcome(
            task_id=task_id,
            status=status,
            facts=selected,
            completed_node_ids=completed,
            incomplete_node_ids=incomplete,
            code=code,
        )
