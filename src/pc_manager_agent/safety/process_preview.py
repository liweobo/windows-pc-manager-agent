"""Live read-only impact Preview for controlled process actions."""

from __future__ import annotations

import hashlib

from pc_manager_agent.domain.process_actions import (
    ProcessActionPlan,
    ProcessActionPreview,
    ResolvedProcessTarget,
)
from pc_manager_agent.safety.process_policy import ProcessSafetyPolicy


class ProcessPreviewEngine:
    """Apply policy to every exact target and summarize concrete impact."""

    def __init__(self, policy: ProcessSafetyPolicy) -> None:
        self._policy = policy

    def build(
        self,
        plan: ProcessActionPlan,
        targets: tuple[ResolvedProcessTarget, ...] | None = None,
    ) -> ProcessActionPreview:
        """Build a digest-bound Preview without executing any process mutation."""
        current_targets = targets or plan.targets
        members = tuple(member for target in current_targets for member in target.members)
        assessments = tuple(
            self._policy.assess(
                member,
                plan.action,
                application_has_window=any(item.graceful_supported for item in target.members),
            )
            for target in current_targets
            for member in target.members
        )
        target_set_digest = hashlib.sha256(
            "\n".join(sorted(member.identity.canonical_digest() for member in members)).encode(
                "utf-8"
            )
        ).hexdigest()
        return ProcessActionPreview(
            transaction_id=plan.transaction_id,
            plan_id=plan.plan_id,
            plan_digest=plan.canonical_digest(),
            action=plan.action,
            targets=current_targets,
            assessments=assessments,
            target_set_digest=target_set_digest,
            application_count=len(current_targets),
            process_count=len(members),
            total_memory_rss_bytes=sum(member.memory_rss_bytes for member in members),
            total_cpu_percent=sum(member.cpu_percent for member in members),
            graceful_supported_count=sum(member.graceful_supported for member in members),
        )
