from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from pc_manager_agent.confirmation.software_uninstall_execution import (
    MsiUninstallConfirmationError,
    MsiUninstallConfirmationService,
)
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.software_uninstall_analysis import SoftwareTargetQuery
from pc_manager_agent.domain.software_uninstall_execution import (
    MsiExecutionDecision,
    MsiInstallContext,
    MsiPreflightState,
    MsiProductRegistration,
)
from pc_manager_agent.domain.system_diagnostics import SoftwareScope
from pc_manager_agent.orchestration.software_capability import UninstallCapabilityResolver
from pc_manager_agent.orchestration.software_execution_preflight import (
    SoftwareExecutionPreflight,
)
from pc_manager_agent.orchestration.software_inventory import normalize_raw_entry
from pc_manager_agent.orchestration.software_msi_validation import (
    MsiProductValidationCode,
    MsiProductValidationError,
    MsiProductValidator,
)
from pc_manager_agent.safety.software_uninstall_execution_policy import (
    SoftwareUninstallExecutionPolicy,
)
from pc_manager_agent.safety.software_uninstall_policy import SoftwareUninstallSafetyPolicy
from pc_manager_agent.tools.manifest import CancellationToken
from tests.fixtures.msi_uninstall import FakeMsiInventory, build_msi_environment
from tests.fixtures.software_analysis import msi_entry, vendor_entry
from tests.fixtures.system_diagnostics import FakeSystemPlatform


def _validated_product(name: str = "Example App"):
    raw = msi_entry(name=name)
    software = normalize_raw_entry(raw)
    assert software is not None and raw.product_code is not None
    registration = MsiProductRegistration(
        product_code=raw.product_code,
        context=MsiInstallContext.USER_UNMANAGED,
        product_name=raw.display_name,
        version=raw.display_version,
        publisher=raw.publisher,
        install_location=raw.install_location,
        installed=True,
    )
    product = MsiProductValidator(FakeMsiInventory(registration)).validate(
        software,
        UninstallCapabilityResolver().resolve(software, raw),
    )
    return raw, software, product


def test_validator_rejects_non_msi_capability(tmp_path: Path) -> None:
    executable = tmp_path / "vendor.exe"
    executable.touch()
    raw = vendor_entry(executable)
    software = normalize_raw_entry(raw)
    assert software is not None
    with pytest.raises(MsiProductValidationError) as caught:
        MsiProductValidator(FakeMsiInventory.__new__(FakeMsiInventory)).validate(
            software,
            UninstallCapabilityResolver().resolve(software, raw),
        )
    assert caught.value.code is MsiProductValidationCode.NOT_MSI_HIGH_CONFIDENCE


@pytest.mark.parametrize(
    ("registrations", "expected"),
    [
        ((), MsiProductValidationCode.PRODUCT_NOT_REGISTERED),
        ("duplicate", MsiProductValidationCode.PRODUCT_CONTEXT_AMBIGUOUS),
    ],
)
def test_validator_rejects_absent_or_ambiguous_registration(
    registrations: tuple[()] | str,
    expected: MsiProductValidationCode,
) -> None:
    raw = msi_entry()
    software = normalize_raw_entry(raw)
    assert software is not None and raw.product_code is not None
    capability = UninstallCapabilityResolver().resolve(software, raw)
    registration = MsiProductRegistration(
        product_code=raw.product_code,
        context=MsiInstallContext.USER_UNMANAGED,
        product_name=raw.display_name,
        version=raw.display_version,
        publisher=raw.publisher,
        installed=True,
    )

    class Inventory:
        def registrations(self, product_code: str) -> tuple[MsiProductRegistration, ...]:
            return () if registrations == () else (registration, registration)

    with pytest.raises(MsiProductValidationError) as caught:
        MsiProductValidator(Inventory()).validate(software, capability)
    assert caught.value.code is expected


@pytest.mark.parametrize(
    ("update", "expected"),
    [
        ({"publisher": None}, MsiProductValidationCode.PUBLISHER_REQUIRED),
        ({"version": "9.9"}, MsiProductValidationCode.PRODUCT_METADATA_MISMATCH),
        (
            {"context": MsiInstallContext.UNKNOWN},
            MsiProductValidationCode.SCOPE_MISMATCH,
        ),
    ],
)
def test_validator_rejects_incomplete_or_changed_registration(
    update: dict[str, object],
    expected: MsiProductValidationCode,
) -> None:
    raw = msi_entry()
    software = normalize_raw_entry(raw)
    assert software is not None and raw.product_code is not None
    registration = MsiProductRegistration(
        product_code=raw.product_code,
        context=MsiInstallContext.USER_UNMANAGED,
        product_name=raw.display_name,
        version=raw.display_version,
        publisher=raw.publisher,
        installed=True,
    ).model_copy(update=update)
    with pytest.raises(MsiProductValidationError) as caught:
        MsiProductValidator(FakeMsiInventory(registration)).validate(
            software,
            UninstallCapabilityResolver().resolve(software, raw),
        )
    assert caught.value.code is expected


@pytest.mark.parametrize(
    ("name", "risk", "allowed"),
    [
        ("Example App", RiskLevel.R2, True),
        ("Example Developer SDK", RiskLevel.R2_HIGH_IMPACT, True),
        ("Python Runtime 3.13", RiskLevel.R2_HIGH_IMPACT, True),
        ("PostgreSQL Database Server", RiskLevel.R3, False),
    ],
)
def test_execution_policy_maps_only_narrow_classes(
    name: str,
    risk: RiskLevel,
    allowed: bool,
) -> None:
    _raw, software, product = _validated_product(name)
    analysis = SoftwareUninstallSafetyPolicy(Path("D:/Agent"), Path("C:/Windows")).assess(software)
    assessment = SoftwareUninstallExecutionPolicy().assess(product, analysis)
    assert assessment.risk_level is risk
    assert (assessment.decision is MsiExecutionDecision.ALLOW) is allowed


def test_execution_policy_blocks_non_current_user_even_if_otherwise_allowed() -> None:
    _raw, software, product = _validated_product()
    product = product.model_copy(update={"scope": SoftwareScope.LOCAL_MACHINE})
    analysis = SoftwareUninstallSafetyPolicy(Path("D:/Agent"), Path("C:/Windows")).assess(software)
    assessment = SoftwareUninstallExecutionPolicy().assess(product, analysis)
    assert assessment.decision is MsiExecutionDecision.BLOCK
    assert assessment.risk_level is RiskLevel.R3


def test_preflight_missing_location_and_cancel_fail_closed() -> None:
    _raw, software, product = _validated_product()
    without_location = software.model_copy(update={"install_location": None})
    missing = SoftwareExecutionPreflight(FakeSystemPlatform()).inspect(
        without_location,
        product,
        CancellationToken(),
    )
    assert missing.state is MsiPreflightState.UNKNOWN
    token = CancellationToken()
    token.cancel()
    cancelled = SoftwareExecutionPreflight(FakeSystemPlatform()).inspect(
        software,
        product,
        token,
    )
    assert cancelled.state is MsiPreflightState.BLOCKED
    assert any("cancelled" in value.casefold() for value in cancelled.blockers)


def test_expired_confirmation_and_changed_risk_are_rejected(tmp_path: Path) -> None:
    environment = build_msi_environment(tmp_path / "state.sqlite3")
    try:
        prepared = environment.services.service.prepare(
            "卸载软件 Example App",
            SoftwareTargetQuery(display_name="Example App"),
        )
        assert prepared.plan and prepared.preview and prepared.plan_confirmation
        changed_plan = prepared.plan.model_copy(update={"risk_level": RiskLevel.R2_HIGH_IMPACT})
        with pytest.raises(MsiUninstallConfirmationError):
            environment.services.service.resolve_plan_confirmation(
                prepared.plan_confirmation.confirmation_id,
                True,
                changed_plan,
                prepared.preview,
            )

        expired_service = MsiUninstallConfirmationService(
            environment.repository,
            now=lambda: prepared.plan_confirmation.expires_at + timedelta(seconds=1),
        )
        with pytest.raises(MsiUninstallConfirmationError):
            expired_service.resolve_plan(
                prepared.plan_confirmation.confirmation_id,
                True,
                prepared.plan,
                prepared.preview,
            )
    finally:
        environment.close()
