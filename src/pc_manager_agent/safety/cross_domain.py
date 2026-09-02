"""Finite cross-domain information-flow rules for Stage 5D."""

from __future__ import annotations

from enum import StrEnum

from pc_manager_agent.domain.agents import AgentRole
from pc_manager_agent.domain.context import DataClassification


class DataFlowDecision(StrEnum):
    """Deterministic context transfer result."""

    ALLOW = "ALLOW"
    TASK_SCOPED = "TASK_SCOPED"
    REFERENCE_ONLY = "REFERENCE_ONLY"
    BLOCK = "BLOCK"


class CrossDomainDataFlowPolicy:
    """Decide whether one data class may enter a target Agent context."""

    def decide(
        self,
        classification: DataClassification,
        destination: AgentRole,
        *,
        external_transmission: bool = False,
    ) -> DataFlowDecision:
        """Apply explicit rules; all unlisted combinations fail closed."""
        if classification in {DataClassification.CREDENTIAL, DataClassification.SECRET}:
            return DataFlowDecision.BLOCK
        if external_transmission and classification in {
            DataClassification.DOCUMENT_CONTENT,
            DataClassification.SENSITIVE,
            DataClassification.USER_DATA,
        }:
            return DataFlowDecision.BLOCK
        if classification is DataClassification.LOCAL_SYSTEM_METADATA:
            return (
                DataFlowDecision.TASK_SCOPED
                if destination
                in {
                    AgentRole.SYSTEM,
                    AgentRole.SOFTWARE,
                    AgentRole.OPTIMIZATION,
                    AgentRole.OFFICE,
                    AgentRole.VERIFIER,
                }
                else DataFlowDecision.BLOCK
            )
        if classification is DataClassification.DOCUMENT_CONTENT:
            return (
                DataFlowDecision.TASK_SCOPED
                if destination in {AgentRole.OFFICE, AgentRole.VERIFIER}
                else DataFlowDecision.BLOCK
            )
        if classification is DataClassification.WEB_CONTENT:
            return (
                DataFlowDecision.TASK_SCOPED
                if destination in {AgentRole.BROWSER, AgentRole.VERIFIER}
                else DataFlowDecision.REFERENCE_ONLY
            )
        if classification is DataClassification.SENSITIVE:
            return DataFlowDecision.BLOCK
        if classification in {DataClassification.PUBLIC, DataClassification.USER_DATA}:
            return DataFlowDecision.TASK_SCOPED
        return DataFlowDecision.BLOCK
