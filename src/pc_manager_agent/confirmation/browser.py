"""Digest-bound browser plan confirmation service."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from pc_manager_agent.domain.browser_plans import BrowserConfirmationRecord, BrowserTaskPlan
from pc_manager_agent.persistence.browser import BrowserConfirmationRepository


class BrowserConfirmationService:
    """Issue, resolve, and atomically consume exact browser plan confirmations."""

    def __init__(
        self,
        repository: BrowserConfirmationRepository,
        ttl_seconds: int,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("Browser confirmation TTL must be positive")
        self._repository = repository
        self._ttl = timedelta(seconds=ttl_seconds)

    def request(
        self, plan: BrowserTaskPlan, *, now: datetime | None = None
    ) -> BrowserConfirmationRecord:
        """Persist a pending approval bound to exact action and page generation."""
        issued = now or datetime.now(UTC)
        record = BrowserConfirmationRecord(
            plan_id=plan.plan_id,
            session_id=plan.action.session_id,
            page_id=plan.action.page_id,
            navigation_id=plan.action.navigation_id,
            plan_digest=plan.canonical_digest(),
            action_digest=plan.action.canonical_digest(),
            origin=plan.allowed_origin,
            expires_at=issued + self._ttl,
            created_at=issued,
        )
        self._repository.create(record)
        return record

    def resolve(
        self,
        confirmation_id: UUID,
        *,
        approved: bool,
        now: datetime | None = None,
    ) -> None:
        """Record the user's explicit decision."""
        self._repository.resolve(
            confirmation_id,
            approved=approved,
            now=now or datetime.now(UTC),
        )

    def consume(
        self,
        confirmation_id: UUID,
        plan: BrowserTaskPlan,
        *,
        now: datetime | None = None,
    ) -> None:
        """Atomically consume an approval only if every binding remains identical."""
        self._repository.consume(
            confirmation_id,
            plan_digest=plan.canonical_digest(),
            action_digest=plan.action.canonical_digest(),
            session_id=plan.action.session_id,
            page_id=plan.action.page_id,
            navigation_id=plan.action.navigation_id,
            origin=plan.allowed_origin,
            now=now or datetime.now(UTC),
        )
