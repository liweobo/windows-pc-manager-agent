"""Branch-complete tests for Stage 4D2B deterministic safety gates."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.software_uninstall_analysis import (
    SoftwareSafetyAssessment,
    SoftwareSafetyClass,
    SoftwareSafetyDecision,
    SoftwareTargetQuery,
)
from pc_manager_agent.domain.system_diagnostics import SoftwareScope
from pc_manager_agent.domain.vendor_uninstall import (
    VendorExecutionDecision,
    VendorPreflightState,
    VendorTrustDecision,
)
from pc_manager_agent.safety import vendor_uninstall_policy as policy_module
from pc_manager_agent.safety.vendor_uninstall_policy import VendorUninstallExecutionPolicy
from pc_manager_agent.safety.vendor_uninstall_preview import VendorUninstallPreviewEngine
from pc_manager_agent.safety.vendor_uninstall_validator import VendorUninstallSafetyValidator
from pc_manager_agent.tools.manifest import ToolManifest
from tests.fixtures.vendor_uninstall import build_vendor_environment


class _RegistryView:
    """Expose exactly the validator-facing registry contract for one test."""

    def __init__(self, names: tuple[str, ...], manifest: ToolManifest) -> None:
        self.names = names
        self._manifest = manifest

    def manifest(self, name: str) -> ToolManifest:
        assert name == "software.uninstall.vendor"
        return self._manifest


def _analysis(
    safety_class: SoftwareSafetyClass,
    decision: SoftwareSafetyDecision = SoftwareSafetyDecision.PREVIEW_ALLOWED,
) -> SoftwareSafetyAssessment:
    return SoftwareSafetyAssessment(
        safety_class=safety_class,
        decision=decision,
        evidence=("synthetic evidence",),
        reasons=("synthetic reason",),
    )


def test_execution_policy_blocks_scope_trust_and_protected_class(tmp_path: Path) -> None:
    environment = build_vendor_environment(tmp_path / "state.sqlite3")
    try:
        prepared = environment.services.service.prepare(
            "卸载 Example App",
            SoftwareTargetQuery(display_name="Example App"),
        )
        assert prepared.preview is not None
        identity = prepared.preview.vendor_identity
        policy = VendorUninstallExecutionPolicy()

        machine = policy.assess(
            SoftwareScope.LOCAL_MACHINE,
            _analysis(SoftwareSafetyClass.USER_APPLICATION),
            identity,
        )
        assert machine.decision is VendorExecutionDecision.BLOCK

        untrusted = identity.model_copy(
            update={
                "trust": identity.trust.model_copy(
                    update={"decision": VendorTrustDecision.INSUFFICIENT_EVIDENCE}
                )
            }
        )
        trust = policy.assess(
            SoftwareScope.CURRENT_USER,
            _analysis(SoftwareSafetyClass.USER_APPLICATION),
            untrusted,
        )
        assert trust.decision is VendorExecutionDecision.BLOCK

        protected = policy.assess(
            SoftwareScope.CURRENT_USER,
            _analysis(
                SoftwareSafetyClass.DEVICE_DRIVER,
                SoftwareSafetyDecision.BLOCKED,
            ),
            identity,
        )
        assert protected.decision is VendorExecutionDecision.BLOCK
        assert protected.risk_level is RiskLevel.R3
    finally:
        environment.close()


@pytest.mark.parametrize(
    "safety_class",
    (SoftwareSafetyClass.DEVELOPER_TOOL, SoftwareSafetyClass.DEVELOPER_RUNTIME),
)
def test_execution_policy_marks_developer_software_high_impact(
    tmp_path: Path,
    safety_class: SoftwareSafetyClass,
) -> None:
    environment = build_vendor_environment(tmp_path / "state.sqlite3")
    try:
        prepared = environment.services.service.prepare(
            "卸载 Example App",
            SoftwareTargetQuery(display_name="Example App"),
        )
        assert prepared.preview is not None
        result = VendorUninstallExecutionPolicy().assess(
            SoftwareScope.CURRENT_USER,
            _analysis(safety_class, SoftwareSafetyDecision.PREVIEW_HIGH_IMPACT),
            prepared.preview.vendor_identity,
        )
        assert result.decision is VendorExecutionDecision.ALLOW
        assert result.risk_level is RiskLevel.R2_HIGH_IMPACT
    finally:
        environment.close()


def test_execution_policy_has_no_implicit_allow_for_unmapped_class(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = build_vendor_environment(tmp_path / "state.sqlite3")
    try:
        prepared = environment.services.service.prepare(
            "卸载 Example App",
            SoftwareTargetQuery(display_name="Example App"),
        )
        assert prepared.preview is not None
        # A copied future enum-like policy result exercises the final default-deny branch by
        # temporarily removing this class from the explicit protected set.
        policy = VendorUninstallExecutionPolicy()
        monkeypatch.setattr(
            policy_module,
            "_BLOCKED_CLASSES",
            frozenset(
                item
                for item in policy_module._BLOCKED_CLASSES
                if item is not SoftwareSafetyClass.BACKGROUND_PLATFORM
            ),
        )
        result = policy.assess(
            SoftwareScope.CURRENT_USER,
            _analysis(SoftwareSafetyClass.BACKGROUND_PLATFORM),
            prepared.preview.vendor_identity,
        )
        assert result.decision is VendorExecutionDecision.BLOCK
    finally:
        environment.close()


def test_preview_engine_rejects_nonpositive_ttl_and_changed_plan(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="TTL"):
        VendorUninstallPreviewEngine(ttl_seconds=0)

    environment = build_vendor_environment(tmp_path / "state.sqlite3")
    try:
        prepared = environment.services.service.prepare(
            "卸载 Example App",
            SoftwareTargetQuery(display_name="Example App"),
        )
        assert prepared.plan is not None and prepared.preview is not None
        changed = prepared.plan.model_copy(update={"identity_digest": "f" * 64})
        current = prepared.preview
        with pytest.raises(ValueError, match="evidence changed"):
            VendorUninstallPreviewEngine().build(
                changed,
                current.target,
                current.capability,
                current.vendor_identity,
                current.execution_assessment,
                current.preflight,
            )
    finally:
        environment.close()


def test_safety_validator_rejects_registry_and_every_changed_binding(tmp_path: Path) -> None:
    environment = build_vendor_environment(tmp_path / "state.sqlite3")
    try:
        prepared = environment.services.service.prepare(
            "卸载 Example App",
            SoftwareTargetQuery(display_name="Example App"),
        )
        assert prepared.plan is not None and prepared.preview is not None
        manifest = environment.services.registry.manifest("software.uninstall.vendor")

        wrong_registry = VendorUninstallSafetyValidator(
            _RegistryView(("software.uninstall.vendor", "unexpected.tool"), manifest)
        )
        assert not wrong_registry.review(prepared.plan, prepared.preview).approved

        weak_manifest = replace(manifest, read_only=True)
        validator = VendorUninstallSafetyValidator(
            _RegistryView(("software.uninstall.vendor",), weak_manifest)
        )
        plan = prepared.plan.model_copy(
            update={
                "plan_id": uuid4(),
                "transaction_id": uuid4(),
                "identity_digest": "1" * 64,
                "capability_digest": "2" * 64,
                "vendor_identity_digest": "3" * 64,
                "execution_assessment_digest": "4" * 64,
                "preflight_digest": "5" * 64,
                "risk_level": RiskLevel.R2_HIGH_IMPACT,
            }
        )
        identity = prepared.preview.vendor_identity.model_copy(
            update={
                "trust": prepared.preview.vendor_identity.trust.model_copy(
                    update={"decision": VendorTrustDecision.INSUFFICIENT_EVIDENCE}
                )
            }
        )
        assessment = prepared.preview.execution_assessment.model_copy(
            update={"decision": VendorExecutionDecision.BLOCK}
        )
        preflight = prepared.preview.preflight.model_copy(
            update={"state": VendorPreflightState.BLOCKED}
        )
        preview = prepared.preview.model_copy(
            update={
                "plan_id": uuid4(),
                "plan_digest": "6" * 64,
                "identity_digest": "7" * 64,
                "vendor_identity": identity,
                "execution_assessment": assessment,
                "preflight": preflight,
                "executable": False,
            }
        )

        review = validator.review(plan, preview)
        assert not review.approved
        assert len(review.issues) == 13
    finally:
        environment.close()
