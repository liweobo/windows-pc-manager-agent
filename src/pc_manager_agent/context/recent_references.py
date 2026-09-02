"""Bounded in-memory recent references; never durable execution identity."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pc_manager_agent.domain.context import RecentEntityKind, RecentEntityReference


class RecentReferenceError(RuntimeError):
    """Raised for unknown, expired, or cross-conversation references."""


class RecentEntityReferenceStore:
    """Hold short-lived hints in memory and require domain Fresh resolution."""

    def __init__(
        self,
        ttl_seconds: int = 600,
        max_entries: int = 100,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if ttl_seconds <= 0 or max_entries <= 0:
            raise ValueError("Recent reference limits must be positive")
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._now = now or (lambda: datetime.now(UTC))
        self._entries: dict[UUID, RecentEntityReference] = {}
        self._lock = threading.RLock()

    def remember(
        self,
        conversation_id: UUID,
        kind: RecentEntityKind,
        owner_domain: str,
        identity_digest: str,
    ) -> RecentEntityReference:
        """Create a bounded hint; evict the oldest expired-or-live entry if full."""
        with self._lock:
            self._prune_locked()
            if len(self._entries) >= self._max_entries:
                oldest = min(self._entries.values(), key=lambda item: item.created_at)
                self._entries.pop(oldest.reference_id, None)
            created = self._now()
            reference = RecentEntityReference(
                conversation_id=conversation_id,
                kind=kind,
                owner_domain=owner_domain,
                identity_digest=identity_digest,
                created_at=created,
                expires_at=created + timedelta(seconds=self._ttl_seconds),
            )
            self._entries[reference.reference_id] = reference
            return reference

    def resolve(self, reference_id: UUID, conversation_id: UUID) -> RecentEntityReference:
        """Return one current same-conversation hint; this is never target authority."""
        with self._lock:
            self._prune_locked()
            try:
                reference = self._entries[reference_id]
            except KeyError as exc:
                raise RecentReferenceError("Recent reference is unknown or expired") from exc
            if reference.conversation_id != conversation_id:
                raise RecentReferenceError("Recent reference belongs to another conversation")
            return reference

    def clear(self) -> None:
        """Forget every hint, including on application shutdown."""
        with self._lock:
            self._entries.clear()

    def _prune_locked(self) -> None:
        current = self._now()
        expired = [
            reference_id
            for reference_id, reference in self._entries.items()
            if current >= reference.expires_at
        ]
        for reference_id in expired:
            self._entries.pop(reference_id, None)
