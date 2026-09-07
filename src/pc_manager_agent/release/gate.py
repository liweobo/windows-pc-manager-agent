"""Deterministic release gate; an LLM cannot supply or waive required evidence."""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar

from pydantic import BaseModel, ConfigDict


class ReadinessLevel(StrEnum):
    """Monotonic release-readiness levels used by Stage 7A and Stage 7B."""

    DEV_READY = "DEV_READY"
    PRIVATE_RC_READY = "PRIVATE_RC_READY"
    PUBLIC_RC_READY = "PUBLIC_RC_READY"
    V1_READY = "V1_READY"


class ReleaseCheck(StrEnum):
    """Closed evidence vocabulary for release qualification."""

    QUALITY = "quality"
    TYPE_CHECK = "type_check"
    TESTS = "tests"
    COVERAGE = "coverage"
    SECURITY = "security"
    DEPENDENCIES = "dependencies"
    SECRETS = "secrets"
    PRODUCTION_CONFIG = "production_config"
    MIGRATIONS = "migrations"
    PACKAGE = "package"
    INSTALLER = "installer"
    ARTIFACT_INSPECTION = "artifact_inspection"
    INSTALLED_SMOKE = "installed_smoke"
    DOCUMENTATION = "documentation"
    PERFORMANCE = "performance"
    PRIVACY = "privacy"
    LICENSES = "licenses"
    SIGNING = "signing"
    WINDOWS_MANUAL = "windows_manual"
    UAC_MANUAL = "uac_manual"
    BRANCH_PROTECTION = "branch_protection"
    FINAL_REGRESSION = "final_regression"
    RELEASE_TAG = "release_tag"


class EvidenceStatus(StrEnum):
    """Truthful state of one externally produced check."""

    PASSED = "PASSED"
    FAILED = "FAILED"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    NOT_RUN = "NOT_RUN"


class ReleaseEvidence(BaseModel):
    """One content-free check result bound to an immutable reference."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    check: ReleaseCheck
    status: EvidenceStatus
    reference: str


class ReleaseGateResult(BaseModel):
    """Highest proven level plus every missing check for the requested level."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    requested: ReadinessLevel
    achieved: ReadinessLevel
    passed: bool
    missing_or_failed: tuple[ReleaseCheck, ...]


class ReleaseGateEvaluator:
    """Require exact PASS evidence and never infer success from absent records."""

    _DEV = frozenset(
        {
            ReleaseCheck.QUALITY,
            ReleaseCheck.TYPE_CHECK,
            ReleaseCheck.TESTS,
            ReleaseCheck.COVERAGE,
            ReleaseCheck.SECURITY,
            ReleaseCheck.DEPENDENCIES,
            ReleaseCheck.SECRETS,
        }
    )
    _PRIVATE = _DEV | frozenset(
        {
            ReleaseCheck.PRODUCTION_CONFIG,
            ReleaseCheck.MIGRATIONS,
            ReleaseCheck.PACKAGE,
            ReleaseCheck.INSTALLER,
            ReleaseCheck.ARTIFACT_INSPECTION,
            ReleaseCheck.INSTALLED_SMOKE,
            ReleaseCheck.DOCUMENTATION,
            ReleaseCheck.PERFORMANCE,
            ReleaseCheck.PRIVACY,
        }
    )
    _PUBLIC = _PRIVATE | frozenset(
        {
            ReleaseCheck.LICENSES,
            ReleaseCheck.SIGNING,
            ReleaseCheck.WINDOWS_MANUAL,
            ReleaseCheck.UAC_MANUAL,
            ReleaseCheck.BRANCH_PROTECTION,
        }
    )
    _V1 = _PUBLIC | frozenset({ReleaseCheck.FINAL_REGRESSION, ReleaseCheck.RELEASE_TAG})
    _REQUIRED: ClassVar[dict[ReadinessLevel, frozenset[ReleaseCheck]]] = {
        ReadinessLevel.DEV_READY: _DEV,
        ReadinessLevel.PRIVATE_RC_READY: _PRIVATE,
        ReadinessLevel.PUBLIC_RC_READY: _PUBLIC,
        ReadinessLevel.V1_READY: _V1,
    }
    _ORDER = (
        ReadinessLevel.DEV_READY,
        ReadinessLevel.PRIVATE_RC_READY,
        ReadinessLevel.PUBLIC_RC_READY,
        ReadinessLevel.V1_READY,
    )

    @classmethod
    def required_checks(cls, level: ReadinessLevel) -> frozenset[ReleaseCheck]:
        """Return the immutable evidence set required for one readiness level."""
        return cls._REQUIRED[level]

    def evaluate(
        self, requested: ReadinessLevel, evidence: tuple[ReleaseEvidence, ...]
    ) -> ReleaseGateResult:
        """Evaluate unique evidence and return the highest fully proven level."""
        by_check: dict[ReleaseCheck, EvidenceStatus] = {}
        for item in evidence:
            if item.check in by_check:
                raise ValueError(f"Duplicate release evidence: {item.check.value}")
            if not item.reference.strip():
                raise ValueError("Release evidence requires a non-empty reference")
            by_check[item.check] = item.status
        required = self._REQUIRED[requested]
        missing = tuple(
            sorted(
                (check for check in required if by_check.get(check) is not EvidenceStatus.PASSED),
                key=lambda item: item.value,
            )
        )
        achieved = ReadinessLevel.DEV_READY
        for level in self._ORDER:
            if all(by_check.get(check) is EvidenceStatus.PASSED for check in self._REQUIRED[level]):
                achieved = level
            else:
                break
        return ReleaseGateResult(
            requested=requested,
            achieved=achieved,
            passed=not missing,
            missing_or_failed=missing,
        )
