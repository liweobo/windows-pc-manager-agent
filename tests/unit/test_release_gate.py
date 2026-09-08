"""Deterministic release evidence gate tests."""

from __future__ import annotations

import pytest

from pc_manager_agent.release.gate import (
    EvidenceStatus,
    ReadinessLevel,
    ReleaseCheck,
    ReleaseEvidence,
    ReleaseGateEvaluator,
)


def _evidence(checks: frozenset[ReleaseCheck]) -> tuple[ReleaseEvidence, ...]:
    return tuple(
        ReleaseEvidence(check=check, status=EvidenceStatus.PASSED, reference=f"evidence:{check}")
        for check in checks
    )


def test_private_gate_does_not_infer_missing_installer_evidence() -> None:
    evaluator = ReleaseGateEvaluator()
    evidence = _evidence(evaluator.required_checks(ReadinessLevel.DEV_READY))

    result = evaluator.evaluate(ReadinessLevel.PRIVATE_RC_READY, evidence)

    assert not result.passed
    assert result.achieved is ReadinessLevel.DEV_READY
    assert ReleaseCheck.INSTALLER in result.missing_or_failed


def test_failed_development_evidence_does_not_claim_development_ready() -> None:
    evidence = (
        ReleaseEvidence(
            check=ReleaseCheck.QUALITY,
            status=EvidenceStatus.FAILED,
            reference="ruff:failed",
        ),
    )

    result = ReleaseGateEvaluator().evaluate(ReadinessLevel.DEV_READY, evidence)

    assert not result.passed
    assert result.achieved is None
    assert ReleaseCheck.QUALITY in result.missing_or_failed


def test_not_configured_signing_blocks_public_but_not_complete_private_gate() -> None:
    evaluator = ReleaseGateEvaluator()
    evidence = list(_evidence(evaluator.required_checks(ReadinessLevel.PRIVATE_RC_READY)))
    evidence.append(
        ReleaseEvidence(
            check=ReleaseCheck.SIGNING,
            status=EvidenceStatus.NOT_CONFIGURED,
            reference="signing:not-configured",
        )
    )

    private = evaluator.evaluate(ReadinessLevel.PRIVATE_RC_READY, tuple(evidence))
    public = evaluator.evaluate(ReadinessLevel.PUBLIC_RC_READY, tuple(evidence))

    assert private.passed
    assert private.achieved is ReadinessLevel.PRIVATE_RC_READY
    assert not public.passed
    assert ReleaseCheck.SIGNING in public.missing_or_failed


def test_duplicate_evidence_is_rejected() -> None:
    item = ReleaseEvidence(
        check=ReleaseCheck.QUALITY,
        status=EvidenceStatus.PASSED,
        reference="ci:1",
    )

    with pytest.raises(ValueError, match="Duplicate release evidence"):
        ReleaseGateEvaluator().evaluate(ReadinessLevel.DEV_READY, (item, item))
