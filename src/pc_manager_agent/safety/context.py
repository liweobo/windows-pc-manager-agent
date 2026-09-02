"""Context taint propagation and conservative secret detection."""

from __future__ import annotations

import re

from pc_manager_agent.domain.context import (
    ContextItem,
    ContextSourceKind,
    ContextTrustLevel,
    DataClassification,
)


class ContextSafetyError(RuntimeError):
    """Raised when context cannot safely cross an Agent boundary."""


_KNOWN_SECRET = re.compile(
    r"(?i)(?:api[_-]?key|authorization|bearer|cookie|password|secret|token|mfa)\s*[:=]\s*\S+"
)
_REQUIRED_TRUST = {
    ContextSourceKind.USER_GOAL: ContextTrustLevel.USER_SUPPLIED,
    ContextSourceKind.SYSTEM_POLICY: ContextTrustLevel.SYSTEM_TRUSTED,
    ContextSourceKind.STRUCTURED_RESULT: ContextTrustLevel.LOCAL_STRUCTURED_DATA,
    ContextSourceKind.DOCUMENT_CHUNK: ContextTrustLevel.UNTRUSTED_DOCUMENT,
    ContextSourceKind.WEB_CHUNK: ContextTrustLevel.UNTRUSTED_WEB,
    ContextSourceKind.MODEL_SUMMARY: ContextTrustLevel.MODEL_GENERATED,
    ContextSourceKind.MEMORY_ENTRY: ContextTrustLevel.USER_SUPPLIED,
    ContextSourceKind.RECENT_REFERENCE: ContextTrustLevel.LOCAL_STRUCTURED_DATA,
}


def required_trust_for_source(source: ContextSourceKind) -> ContextTrustLevel:
    """Return the immutable minimum trust label for one context origin."""
    return _REQUIRED_TRUST[source]


class ContextTaintPolicy:
    """Preserve provenance and prevent untrusted data from becoming policy."""

    def validate_source(self, item: ContextItem) -> None:
        """Reject contradictory labels and known credential material."""
        source = item.reference.source_kind
        labels = set(item.trust_labels)
        expected = required_trust_for_source(source)
        if expected not in labels:
            raise ContextSafetyError("Context source is missing its required trust label")
        if (
            source is not ContextSourceKind.SYSTEM_POLICY
            and ContextTrustLevel.SYSTEM_TRUSTED in labels
        ):
            raise ContextSafetyError("Data context cannot claim system trust")
        if set(item.classifications) & {
            DataClassification.CREDENTIAL,
            DataClassification.SECRET,
        }:
            raise ContextSafetyError("Credentials and secrets cannot enter Agent context")
        if _KNOWN_SECRET.search(item.content):
            raise ContextSafetyError("Known secret pattern detected in Agent context")

    def validate_text(self, value: str) -> None:
        """Reject known secret assignments in a user goal or generated summary."""
        if _KNOWN_SECRET.search(value):
            raise ContextSafetyError("Known secret pattern detected in Agent context")

    def derived_labels(
        self, inputs: tuple[ContextItem, ...], *, model_generated: bool
    ) -> tuple[ContextTrustLevel, ...]:
        """Return the union of input labels and preserve model-generated taint."""
        labels = {label for item in inputs for label in item.trust_labels}
        if model_generated:
            labels.add(ContextTrustLevel.MODEL_GENERATED)
        labels.discard(ContextTrustLevel.SYSTEM_TRUSTED)
        return tuple(sorted(labels, key=lambda item: item.value))
