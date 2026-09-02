"""Deterministic Memory value, write, and least-scope read policies."""

from __future__ import annotations

import re
from uuid import UUID

from pc_manager_agent.domain.agents import AgentRole
from pc_manager_agent.domain.memory import (
    MemoryCandidate,
    MemoryCategory,
    MemoryConfidence,
    MemoryKey,
    MemoryQuery,
    MemoryScope,
    MemorySensitivity,
    MemorySourceType,
    MemoryWriteDecision,
)


class MemoryPolicyError(RuntimeError):
    """Raised when Memory access or persistence violates policy."""


_UNSAFE_DIRECTIVE = re.compile(
    r"(?i)(ignore|bypass|skip|disable|never ask|always allow|admin(?:istrator)?\s+shell|"
    r"忽略|绕过|不再确认|无需确认|总是允许|直接删除|管理员直接执行)"
)
_SECRET = re.compile(
    r"(?i)(api[_-]?key|authorization|bearer|cookie|password|secret|token|mfa)\s*[:=]\s*\S+"
)
_SAFE_ENUM_VALUES: dict[MemoryKey, frozenset[str]] = {
    MemoryKey.RESPONSE_LANGUAGE: frozenset({"zh-CN", "en-US"}),
    MemoryKey.OFFICE_OUTPUT_FORMAT: frozenset({"DOCX", "XLSX", "CSV", "MARKDOWN", "PDF"}),
    MemoryKey.OFFICE_SAVE_MODE: frozenset({"SAVE_AS", "IN_PLACE_REVIEW"}),
    MemoryKey.VOICE_OUTPUT_MODE: frozenset({"OFF", "SAFE_SUMMARY"}),
    MemoryKey.PROVIDER_NAME: frozenset({"disabled", "openai"}),
}
_EXPECTED_SCOPE: dict[MemoryKey, frozenset[MemoryScope]] = {
    MemoryKey.RESPONSE_LANGUAGE: frozenset({MemoryScope.GLOBAL_PREFERENCE}),
    MemoryKey.LARGE_FILE_THRESHOLD_BYTES: frozenset({MemoryScope.FILE}),
    MemoryKey.INACTIVE_DAYS: frozenset({MemoryScope.FILE}),
    MemoryKey.OFFICE_OUTPUT_FORMAT: frozenset({MemoryScope.OFFICE}),
    MemoryKey.OFFICE_SAVE_MODE: frozenset({MemoryScope.OFFICE}),
    MemoryKey.VOICE_OUTPUT_MODE: frozenset({MemoryScope.VOICE}),
    MemoryKey.UI_DEFAULT_TAB: frozenset({MemoryScope.UI}),
    MemoryKey.COMMON_DIRECTORY_REF: frozenset(
        {MemoryScope.FILE, MemoryScope.OFFICE, MemoryScope.GLOBAL_PREFERENCE}
    ),
    MemoryKey.COMMON_APPLICATION_REF: frozenset({MemoryScope.SOFTWARE}),
    MemoryKey.PROVIDER_NAME: frozenset({MemoryScope.GLOBAL_PREFERENCE}),
}
_EXPECTED_CATEGORY: dict[MemoryKey, frozenset[MemoryCategory]] = {
    MemoryKey.RESPONSE_LANGUAGE: frozenset({MemoryCategory.USER_PREFERENCE}),
    MemoryKey.LARGE_FILE_THRESHOLD_BYTES: frozenset({MemoryCategory.TASK_PREFERENCE}),
    MemoryKey.INACTIVE_DAYS: frozenset({MemoryCategory.TASK_PREFERENCE}),
    MemoryKey.OFFICE_OUTPUT_FORMAT: frozenset({MemoryCategory.TASK_PREFERENCE}),
    MemoryKey.OFFICE_SAVE_MODE: frozenset({MemoryCategory.TASK_PREFERENCE}),
    MemoryKey.VOICE_OUTPUT_MODE: frozenset({MemoryCategory.USER_PREFERENCE}),
    MemoryKey.UI_DEFAULT_TAB: frozenset({MemoryCategory.UI_PREFERENCE}),
    MemoryKey.COMMON_DIRECTORY_REF: frozenset({MemoryCategory.COMMON_DIRECTORY_REFERENCE}),
    MemoryKey.COMMON_APPLICATION_REF: frozenset({MemoryCategory.COMMON_APPLICATION_REFERENCE}),
    MemoryKey.PROVIDER_NAME: frozenset({MemoryCategory.PROVIDER_PREFERENCE}),
}


class MemoryWritePolicy:
    """Classify finite preference candidates without trusting an LLM decision."""

    def decide(self, candidate: MemoryCandidate) -> MemoryWriteDecision:
        """Return a policy decision after key, value, source, and sensitivity checks."""
        if candidate.sensitivity in {
            MemorySensitivity.SENSITIVE,
            MemorySensitivity.PROHIBITED,
        }:
            return MemoryWriteDecision.BLOCK
        if _SECRET.search(candidate.value) or _UNSAFE_DIRECTIVE.search(candidate.value):
            return MemoryWriteDecision.BLOCK
        if candidate.scope not in _EXPECTED_SCOPE[candidate.key]:
            return MemoryWriteDecision.BLOCK
        if candidate.category not in _EXPECTED_CATEGORY[candidate.key]:
            return MemoryWriteDecision.BLOCK
        if not self._valid_value(candidate.key, candidate.value):
            return MemoryWriteDecision.BLOCK
        if (
            candidate.source_type
            in {
                MemorySourceType.MODEL_CANDIDATE,
                MemorySourceType.OBSERVED_BEHAVIOR,
            }
            or candidate.confidence is not MemoryConfidence.HIGH
        ):
            return MemoryWriteDecision.EPHEMERAL_ONLY
        if not candidate.explicit_user_intent:
            return MemoryWriteDecision.EPHEMERAL_ONLY
        return MemoryWriteDecision.REQUIRE_USER_CONFIRMATION

    @staticmethod
    def _valid_value(key: MemoryKey, value: str) -> bool:
        normalized = value.strip()
        if key in _SAFE_ENUM_VALUES:
            return normalized in _SAFE_ENUM_VALUES[key]
        if key is MemoryKey.LARGE_FILE_THRESHOLD_BYTES:
            return (
                normalized.isascii()
                and normalized.isdigit()
                and 1_048_576 <= int(normalized) <= 10**15
            )
        if key is MemoryKey.INACTIVE_DAYS:
            return normalized.isascii() and normalized.isdigit() and 1 <= int(normalized) <= 3_650
        if key is MemoryKey.COMMON_DIRECTORY_REF:
            try:
                UUID(normalized)
            except ValueError:
                return False
            return True
        if key is MemoryKey.COMMON_APPLICATION_REF:
            return bool(re.fullmatch(r"[\w .()&+\-]{1,120}", normalized, flags=re.UNICODE))
        if key is MemoryKey.UI_DEFAULT_TAB:
            return bool(re.fullmatch(r"[A-Z][A-Z0-9_]{1,39}", normalized))
        return False


_READ_SCOPES: dict[AgentRole, frozenset[MemoryScope]] = {
    AgentRole.ORCHESTRATOR: frozenset({MemoryScope.GLOBAL_PREFERENCE}),
    AgentRole.PLANNER: frozenset({MemoryScope.GLOBAL_PREFERENCE}),
    AgentRole.FILE: frozenset({MemoryScope.GLOBAL_PREFERENCE, MemoryScope.FILE}),
    AgentRole.SYSTEM: frozenset(
        {MemoryScope.GLOBAL_PREFERENCE, MemoryScope.SYSTEM, MemoryScope.UI}
    ),
    AgentRole.SOFTWARE: frozenset({MemoryScope.GLOBAL_PREFERENCE, MemoryScope.SOFTWARE}),
    AgentRole.OFFICE: frozenset({MemoryScope.GLOBAL_PREFERENCE, MemoryScope.OFFICE}),
    AgentRole.BROWSER: frozenset({MemoryScope.GLOBAL_PREFERENCE, MemoryScope.BROWSER}),
    AgentRole.OPTIMIZATION: frozenset({MemoryScope.GLOBAL_PREFERENCE, MemoryScope.SYSTEM}),
    AgentRole.MEMORY_MANAGER: frozenset(MemoryScope),
    AgentRole.SAFETY_REVIEWER: frozenset(),
    AgentRole.VERIFIER: frozenset(),
    AgentRole.AUDIT_MANAGER: frozenset(),
}


class MemoryReadPolicy:
    """Reject broad or cross-role Memory queries."""

    def validate(self, query: MemoryQuery) -> None:
        """Require every requested scope to belong to the runtime role matrix."""
        if not set(query.scopes).issubset(_READ_SCOPES[query.agent_role]):
            raise MemoryPolicyError("Agent requested Memory outside its scope")


def readable_scopes(role: AgentRole) -> tuple[MemoryScope, ...]:
    """Return the deterministic role scope matrix for UI and tests."""
    return tuple(sorted(_READ_SCOPES[role], key=lambda item: item.value))
