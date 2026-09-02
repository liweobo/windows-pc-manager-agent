from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from pc_manager_agent.context.recent_references import (
    RecentEntityReferenceStore,
    RecentReferenceError,
)
from pc_manager_agent.domain.context import RecentEntityKind


def test_recent_reference_is_conversation_scoped_and_requires_fresh_resolution() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    conversation = uuid4()
    store = RecentEntityReferenceStore(ttl_seconds=60, now=lambda: now)
    reference = store.remember(
        conversation,
        RecentEntityKind.FILE_HINT,
        "FILE",
        "a" * 64,
    )
    assert store.resolve(reference.reference_id, conversation) == reference
    assert reference.requires_fresh_resolution is True
    with pytest.raises(RecentReferenceError, match="another conversation"):
        store.resolve(reference.reference_id, uuid4())
    store.clear()
    with pytest.raises(RecentReferenceError, match="unknown"):
        store.resolve(reference.reference_id, conversation)


def test_recent_reference_expires_and_oldest_entry_is_evicted() -> None:
    current = datetime(2026, 1, 1, tzinfo=UTC)
    store = RecentEntityReferenceStore(ttl_seconds=60, max_entries=1, now=lambda: current)
    conversation = uuid4()
    first = store.remember(conversation, RecentEntityKind.FILE_HINT, "FILE", "b" * 64)
    current += timedelta(seconds=1)
    second = store.remember(conversation, RecentEntityKind.DOCUMENT_HINT, "OFFICE", "c" * 64)
    with pytest.raises(RecentReferenceError, match="unknown"):
        store.resolve(first.reference_id, conversation)
    assert store.resolve(second.reference_id, conversation) == second
    current += timedelta(seconds=61)
    with pytest.raises(RecentReferenceError, match="expired"):
        store.resolve(second.reference_id, conversation)


@pytest.mark.parametrize(("ttl", "maximum"), [(0, 1), (1, 0)])
def test_recent_reference_rejects_non_positive_limits(ttl: int, maximum: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        RecentEntityReferenceStore(ttl_seconds=ttl, max_entries=maximum)
