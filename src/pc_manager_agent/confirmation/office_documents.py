"""One-time Office confirmations independent of optimization and legacy file approvals."""

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from pc_manager_agent.audit.office_documents import OfficeAudit
from pc_manager_agent.persistence.office_documents import OfficeRepository


class DocumentConfirmations:
    """Bind exact canonical documents, output, backup, Preview and purpose."""

    def __init__(
        self,
        repository: OfficeRepository,
        audit: OfficeAudit,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._audit = audit
        self.now = now or (lambda: datetime.now(UTC))

    def request(self, binding: str, purpose: str, expires: datetime) -> UUID:
        """Journal a pending request without retaining the raw content behind its hash."""
        confirmation_id = self._repository.request(binding, purpose, expires)
        self._audit.record("confirmation.requested", str(confirmation_id))
        return confirmation_id

    def resolve(self, confirmation_id: UUID, binding: str, purpose: str, approved: bool) -> None:
        """Record explicit user approval/rejection; never invoked by a model."""
        self._audit.record("confirmation.resolving", str(confirmation_id))
        self._repository.resolve(confirmation_id, binding, purpose, approved, self.now())
        self._audit.record(
            "confirmation.approved" if approved else "confirmation.rejected", str(confirmation_id)
        )

    def consume(self, confirmation_id: UUID, binding: str, purpose: str) -> None:
        """Fail before an operation if audit or atomic one-time consumption is unavailable."""
        self._audit.record("confirmation.consuming", str(confirmation_id))
        self._repository.consume(confirmation_id, binding, purpose, self.now())
