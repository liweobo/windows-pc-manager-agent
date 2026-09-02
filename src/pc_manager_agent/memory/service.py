"""Deterministic Memory lifecycle service with explicit user-control gates."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from pc_manager_agent.audit.memory import MemoryAuditLogger
from pc_manager_agent.domain.memory import (
    MemoryCandidate,
    MemoryCategory,
    MemoryConfidence,
    MemoryContext,
    MemoryEntry,
    MemoryKey,
    MemoryQuery,
    MemoryScope,
    MemorySensitivity,
    MemorySourceType,
    MemoryWriteDecision,
)
from pc_manager_agent.persistence.memory import MemoryRepository
from pc_manager_agent.safety.memory import MemoryReadPolicy, MemoryWritePolicy


class MemoryServiceError(RuntimeError):
    """Raised when a Memory operation is unconfirmed, disabled, or blocked."""


_SETTING_CLASSIFICATION: dict[MemoryKey, tuple[MemoryCategory, MemoryScope]] = {
    MemoryKey.RESPONSE_LANGUAGE: (
        MemoryCategory.USER_PREFERENCE,
        MemoryScope.GLOBAL_PREFERENCE,
    ),
    MemoryKey.LARGE_FILE_THRESHOLD_BYTES: (
        MemoryCategory.TASK_PREFERENCE,
        MemoryScope.FILE,
    ),
    MemoryKey.INACTIVE_DAYS: (MemoryCategory.TASK_PREFERENCE, MemoryScope.FILE),
    MemoryKey.OFFICE_OUTPUT_FORMAT: (
        MemoryCategory.TASK_PREFERENCE,
        MemoryScope.OFFICE,
    ),
    MemoryKey.OFFICE_SAVE_MODE: (MemoryCategory.TASK_PREFERENCE, MemoryScope.OFFICE),
    MemoryKey.VOICE_OUTPUT_MODE: (
        MemoryCategory.USER_PREFERENCE,
        MemoryScope.VOICE,
    ),
    MemoryKey.UI_DEFAULT_TAB: (MemoryCategory.UI_PREFERENCE, MemoryScope.UI),
    MemoryKey.COMMON_DIRECTORY_REF: (
        MemoryCategory.COMMON_DIRECTORY_REFERENCE,
        MemoryScope.FILE,
    ),
    MemoryKey.COMMON_APPLICATION_REF: (
        MemoryCategory.COMMON_APPLICATION_REFERENCE,
        MemoryScope.SOFTWARE,
    ),
    MemoryKey.PROVIDER_NAME: (
        MemoryCategory.PROVIDER_PREFERENCE,
        MemoryScope.GLOBAL_PREFERENCE,
    ),
}


def explicit_setting_candidate(key: MemoryKey, value: str) -> MemoryCandidate:
    """Build one UI-originated candidate; policy still validates and requires confirmation."""
    category, scope = _SETTING_CLASSIFICATION[key]
    return MemoryCandidate(
        category=category,
        scope=scope,
        key=key,
        value=value,
        source_type=MemorySourceType.USER_SETTINGS,
        confidence=MemoryConfidence.HIGH,
        sensitivity=MemorySensitivity.LOW,
        explicit_user_intent=True,
    )


class MemoryService:
    """Apply deterministic policy before every persistent Memory operation."""

    def __init__(
        self,
        repository: MemoryRepository,
        audit: MemoryAuditLogger,
        *,
        write_policy: MemoryWritePolicy | None = None,
        read_policy: MemoryReadPolicy | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._audit = audit
        self._write_policy = write_policy or MemoryWritePolicy()
        self._read_policy = read_policy or MemoryReadPolicy()
        self._now = now or (lambda: datetime.now(UTC))

    @property
    def enabled(self) -> bool:
        """Return whether persistent Memory may be read or written."""
        return self._repository.is_enabled()

    def set_enabled(self, enabled: bool) -> None:
        """Enable or pause Memory; pausing preserves entries for user management."""
        self._repository.set_enabled(enabled, now=self._now())
        self._audit.changed("ENABLED" if enabled else "DISABLED")

    def save(self, candidate: MemoryCandidate, *, confirmed: bool) -> MemoryEntry:
        """Persist only a safe, explicit, exactly confirmed candidate."""
        decision = self._write_policy.decide(candidate)
        reason = {
            MemoryWriteDecision.BLOCK: "MEMORY_WRITE_BLOCKED",
            MemoryWriteDecision.EPHEMERAL_ONLY: "MEMORY_EPHEMERAL_ONLY",
            MemoryWriteDecision.ALLOW: "MEMORY_WRITE_ALLOWED",
            MemoryWriteDecision.REQUIRE_USER_CONFIRMATION: "MEMORY_CONFIRMATION_REQUIRED",
        }[decision]
        self._audit.write_decision(candidate.candidate_id, candidate.key.value, decision, reason)
        if decision is MemoryWriteDecision.BLOCK:
            raise MemoryServiceError("Memory candidate is blocked by policy")
        if decision is MemoryWriteDecision.EPHEMERAL_ONLY:
            raise MemoryServiceError("Memory candidate may be used only in the current task")
        if not self.enabled:
            raise MemoryServiceError("Persistent Memory is disabled")
        if decision is MemoryWriteDecision.REQUIRE_USER_CONFIRMATION and not confirmed:
            raise MemoryServiceError("Memory write requires explicit confirmation")
        entry = self._repository.upsert(candidate, now=self._now())
        self._audit.changed("UPSERT", memory_id=entry.memory_id, scope=entry.scope)
        return entry

    def query(self, query: MemoryQuery) -> MemoryContext:
        """Return only the scopes allowed for the requesting runtime role."""
        self._read_policy.validate(query)
        entries = self._repository.query(query, now=self._now())
        return MemoryContext(
            entries=entries,
            scopes=query.scopes,
            query_reason=query.reason_code,
        )

    def list_for_user(self) -> tuple[MemoryEntry, ...]:
        """List live entries for the dedicated user-control page even when paused."""
        return self._repository.list_all(now=self._now())

    def delete(self, memory_id: UUID, *, confirmed: bool) -> bool:
        """Delete one entry only after object-specific confirmation."""
        if not confirmed:
            self._audit.changed("DELETE", memory_id=memory_id, confirmed=False)
            raise MemoryServiceError("Memory deletion requires explicit confirmation")
        deleted = self._repository.delete(memory_id, now=self._now())
        self._audit.changed(
            "DELETE", memory_id=memory_id, affected_count=int(deleted), confirmed=True
        )
        return deleted

    def clear_scope(self, scope: MemoryScope, *, confirmed: bool) -> int:
        """Delete one exact scope after high-impact local-data confirmation."""
        if not confirmed:
            self._audit.changed("CLEAR_SCOPE", scope=scope, confirmed=False)
            raise MemoryServiceError("Memory scope clearing requires explicit confirmation")
        count = self._repository.clear(scope.value, now=self._now())
        self._audit.changed("CLEAR_SCOPE", scope=scope, affected_count=count, confirmed=True)
        return count

    def clear_all(self, *, confirmed: bool) -> int:
        """Delete every Memory value without deleting the independent audit trail."""
        if not confirmed:
            self._audit.changed("CLEAR_ALL", confirmed=False)
            raise MemoryServiceError("Clearing all Memory requires explicit confirmation")
        count = self._repository.clear(None, now=self._now())
        self._audit.changed("CLEAR_ALL", affected_count=count, confirmed=True)
        return count

    def close(self) -> None:
        """Release Memory database connections."""
        self._repository.close()
