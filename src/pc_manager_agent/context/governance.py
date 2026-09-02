"""Minimum-context construction with trust, classification, and budget enforcement."""

from __future__ import annotations

import hashlib

from pc_manager_agent.config.agents import AgentRuntimeLimits
from pc_manager_agent.domain.agents import AgentRuntimeIdentity
from pc_manager_agent.domain.context import (
    ContextBudget,
    ContextItem,
    ContextPackage,
    ContextReference,
    ContextSourceKind,
    ContextTrustLevel,
    DataClassification,
)
from pc_manager_agent.domain.memory import MemoryContext
from pc_manager_agent.safety.agent_capabilities import AgentCapabilityRegistry
from pc_manager_agent.safety.context import ContextSafetyError, ContextTaintPolicy
from pc_manager_agent.safety.cross_domain import (
    CrossDomainDataFlowPolicy,
    DataFlowDecision,
)


class ContextGovernanceService:
    """Build one task-relevant package without exposing global conversation state."""

    def __init__(
        self,
        capabilities: AgentCapabilityRegistry,
        limits: AgentRuntimeLimits,
        *,
        taint: ContextTaintPolicy | None = None,
        data_flow: CrossDomainDataFlowPolicy | None = None,
    ) -> None:
        self._capabilities = capabilities
        self._limits = limits
        self._taint = taint or ContextTaintPolicy()
        self._data_flow = data_flow or CrossDomainDataFlowPolicy()

    def build(
        self,
        identity: AgentRuntimeIdentity,
        *,
        task_id: object,
        node_id: object,
        user_goal: str,
        goal_digest: str,
        items: tuple[ContextItem, ...] = (),
        memory: MemoryContext | None = None,
        external_transmission: bool = False,
    ) -> ContextPackage:
        """Select allowed items and fail closed on secrets or stale Agent identity."""
        from uuid import UUID

        if not isinstance(task_id, UUID) or not isinstance(node_id, UUID):
            raise ContextSafetyError("Context package requires UUID task and node identities")
        if hashlib.sha256(user_goal.encode()).hexdigest() != goal_digest:
            raise ContextSafetyError("Context goal digest mismatch")
        self._taint.validate_text(user_goal)
        manifest = self._capabilities.manifest(identity.role)
        if identity.manifest_digest != manifest.canonical_digest():
            raise ContextSafetyError("Agent identity does not match its capability manifest")

        candidates = (*items, *self._memory_items(memory))
        selected: list[ContextItem] = []
        redactions: list[str] = []
        used_chars = len(user_goal)
        document_count = 0
        web_count = 0
        memory_count = 0
        references = 0
        for item in candidates:
            self._taint.validate_source(item)
            if not set(item.classifications).issubset(manifest.readable_data):
                redactions.append("CAPABILITY_DATA_FILTERED")
                continue
            decisions = tuple(
                self._data_flow.decide(
                    classification,
                    identity.role,
                    external_transmission=external_transmission,
                )
                for classification in item.classifications
            )
            if DataFlowDecision.BLOCK in decisions:
                redactions.append("CROSS_DOMAIN_DATA_BLOCKED")
                continue
            if DataFlowDecision.REFERENCE_ONLY in decisions:
                item = item.model_copy(
                    update={"content": f"reference:{item.reference.reference_id}"}
                )
                redactions.append("CONTENT_REDUCED_TO_REFERENCE")

            is_document = item.reference.source_kind is ContextSourceKind.DOCUMENT_CHUNK
            is_web = item.reference.source_kind is ContextSourceKind.WEB_CHUNK
            is_memory = item.reference.source_kind is ContextSourceKind.MEMORY_ENTRY
            next_references = 1 + len(item.source_references)
            over_budget = (
                len(selected) >= self._limits.max_messages
                or used_chars + len(item.content) > self._limits.max_context_chars
                or document_count + int(is_document) > self._limits.max_document_chunks
                or web_count + int(is_web) > self._limits.max_web_chunks
                or memory_count + int(is_memory) > self._limits.max_memory_entries
                or references + next_references > self._limits.max_structured_references
            )
            if over_budget:
                redactions.append("CONTEXT_ITEM_OMITTED_BUDGET")
                continue
            selected.append(item)
            used_chars += len(item.content)
            document_count += int(is_document)
            web_count += int(is_web)
            memory_count += int(is_memory)
            references += next_references

        return ContextPackage(
            task_id=task_id,
            node_id=node_id,
            agent_role=identity.role.value,
            user_goal=user_goal,
            goal_digest=goal_digest,
            items=tuple(selected),
            redactions_applied=tuple(dict.fromkeys(redactions)),
            budget=ContextBudget(
                max_messages=self._limits.max_messages,
                max_chars=self._limits.max_context_chars,
                max_document_chunks=self._limits.max_document_chunks,
                max_web_chunks=self._limits.max_web_chunks,
                max_memory_entries=self._limits.max_memory_entries,
                max_structured_references=self._limits.max_structured_references,
            ),
            prompt_version=identity.prompt_version,
        )

    @staticmethod
    def _memory_items(memory: MemoryContext | None) -> tuple[ContextItem, ...]:
        if memory is None:
            return ()
        items = []
        for entry in memory.entries:
            content = f"{entry.key.value}={entry.value}"
            items.append(
                ContextItem(
                    reference=ContextReference(
                        reference_id=str(entry.memory_id),
                        source_kind=ContextSourceKind.MEMORY_ENTRY,
                        owner_domain=entry.scope.value,
                        content_digest=hashlib.sha256(content.encode()).hexdigest(),
                    ),
                    content=content,
                    trust_labels=(ContextTrustLevel.USER_SUPPLIED,),
                    classifications=(DataClassification.USER_DATA,),
                    source_references=(str(entry.memory_id),),
                )
            )
        return tuple(items)
