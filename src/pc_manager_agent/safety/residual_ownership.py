"""Structured ownership evidence for Stage 4D3 possible residuals."""

from __future__ import annotations

from pc_manager_agent.domain.software_residuals import (
    ContextPathEvidence,
    OwnershipConfidence,
    OwnershipEvidence,
    OwnershipEvidenceStrength,
)


class ResidualOwnershipEvaluator:
    """Evaluate ownership without equating it to cleanup safety."""

    def evaluate(
        self,
        root_evidence: ContextPathEvidence,
        *,
        shared_location: bool = False,
    ) -> tuple[OwnershipConfidence, tuple[OwnershipEvidence, ...]]:
        """Return confidence derived from exact pre-uninstall path evidence."""
        evidence = [
            OwnershipEvidence(
                code=root_evidence.evidence_code,
                strength=OwnershipEvidenceStrength.STRONG,
                explanation="Path is equal to or below an exact path captured before uninstall.",
            )
        ]
        if root_evidence.related_target_path is not None:
            evidence.append(
                OwnershipEvidence(
                    code="shortcut-target-exact",
                    strength=OwnershipEvidenceStrength.STRONG,
                    explanation=(
                        "Shortcut target was captured before uninstall and points to an "
                        "exact known application path."
                    ),
                )
            )
        if shared_location:
            evidence.append(
                OwnershipEvidence(
                    code="shared-location-warning",
                    strength=OwnershipEvidenceStrength.WEAK,
                    explanation="The path name indicates a location that may be shared.",
                )
            )
            return OwnershipConfidence.MEDIUM, tuple(evidence)
        return OwnershipConfidence.HIGH, tuple(evidence)

    def weak_name_only(self) -> tuple[OwnershipConfidence, tuple[OwnershipEvidence, ...]]:
        """Represent name-only similarity without ever upgrading it to strong evidence."""
        return (
            OwnershipConfidence.LOW,
            (
                OwnershipEvidence(
                    code="name-similarity-only",
                    strength=OwnershipEvidenceStrength.WEAK,
                    explanation="A similar name alone does not establish ownership.",
                ),
            ),
        )
