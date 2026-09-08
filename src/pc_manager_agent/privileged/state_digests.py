"""Shared content-free state digests used by Main preparation and Broker revalidation."""

from __future__ import annotations

from pc_manager_agent.domain.privileged_actions import canonical_model_digest
from pc_manager_agent.domain.software_uninstall_execution import ValidatedMsiProduct


def machine_msi_state_digest(
    product: ValidatedMsiProduct,
    assessment_digest: str,
    preflight_digest: str,
) -> str:
    """Bind a privileged Preview to stable product, policy, and preflight evidence."""
    return canonical_model_digest(
        {
            "product_evidence": product.evidence_digest(),
            "assessment_digest": assessment_digest,
            "preflight_digest": preflight_digest,
        }
    )
