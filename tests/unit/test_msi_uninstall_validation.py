from __future__ import annotations

from pathlib import Path

import pytest

from pc_manager_agent.domain.software_uninstall_analysis import SoftwareSafetyClass
from pc_manager_agent.domain.software_uninstall_execution import (
    MsiInstallContext,
    MsiProductRegistration,
)
from pc_manager_agent.orchestration.software_capability import UninstallCapabilityResolver
from pc_manager_agent.orchestration.software_inventory import normalize_raw_entry
from pc_manager_agent.orchestration.software_msi_validation import (
    MsiProductValidationCode,
    MsiProductValidationError,
    MsiProductValidator,
    normalize_product_code,
)
from pc_manager_agent.safety.software_uninstall_policy import SoftwareUninstallSafetyPolicy
from tests.fixtures.msi_uninstall import FakeMsiInventory
from tests.fixtures.software_analysis import msi_entry


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            "{12345678-1234-1234-1234-1234567890ab}",
            "{12345678-1234-1234-1234-1234567890AB}",
        ),
        (
            "{12345678-1234-1234-1234-1234567890AB}",
            "{12345678-1234-1234-1234-1234567890AB}",
        ),
    ],
)
def test_product_code_accepts_only_exact_braced_guid(value: str, expected: str) -> None:
    assert normalize_product_code(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "12345678-1234-1234-1234-1234567890AB",
        "{GUID}",
        "{12345678-1234-1234-1234-1234567890AB} /quiet",
        "{12345678-1234-1234-1234-1234567890AB} & calc.exe",
        "{12345678-1234-1234-1234-1234567890AB}; powershell",
        "｛12345678-1234-1234-1234-1234567890AB｝",
    ],
)
def test_product_code_rejects_arguments_shell_syntax_and_unicode(value: str) -> None:
    with pytest.raises(MsiProductValidationError) as caught:
        normalize_product_code(value)
    assert caught.value.code is MsiProductValidationCode.PRODUCT_CODE_INVALID


def test_validator_binds_registry_and_windows_installer_metadata() -> None:
    raw = msi_entry()
    software = normalize_raw_entry(raw)
    assert software is not None and raw.product_code is not None
    capability = UninstallCapabilityResolver().resolve(software, raw)
    platform = FakeMsiInventory(
        MsiProductRegistration(
            product_code=raw.product_code,
            context=MsiInstallContext.USER_UNMANAGED,
            product_name=raw.display_name,
            version=raw.display_version,
            publisher=raw.publisher,
            install_location=raw.install_location,
            installed=True,
        )
    )
    product = MsiProductValidator(platform).validate(software, capability)
    assert product.product_code == raw.product_code
    assert product.identity_digest == software.identity.canonical_digest()
    assert product.publisher == "Example Publisher"


@pytest.mark.parametrize(
    ("context", "code"),
    [
        (MsiInstallContext.MACHINE, MsiProductValidationCode.MACHINE_SCOPE_BLOCKED),
        (MsiInstallContext.USER_MANAGED, MsiProductValidationCode.MANAGED_SCOPE_BLOCKED),
    ],
)
def test_validator_blocks_machine_and_managed_contexts(
    context: MsiInstallContext,
    code: MsiProductValidationCode,
) -> None:
    raw = msi_entry()
    software = normalize_raw_entry(raw)
    assert software is not None and raw.product_code is not None
    registration = MsiProductRegistration(
        product_code=raw.product_code,
        context=context,
        product_name=raw.display_name,
        version=raw.display_version,
        publisher=raw.publisher,
        install_location=raw.install_location,
        installed=True,
    )
    with pytest.raises(MsiProductValidationError) as caught:
        MsiProductValidator(FakeMsiInventory(registration)).validate(
            software,
            UninstallCapabilityResolver().resolve(software, raw),
        )
    assert caught.value.code is code


@pytest.mark.parametrize(
    "name",
    [
        "Microsoft Visual C++ 2022 Redistributable",
        "Shared Runtime Libraries",
    ],
)
def test_shared_runtime_classification_precedes_developer_runtime(name: str) -> None:
    software = normalize_raw_entry(msi_entry(name=name, publisher="Microsoft Corporation"))
    assert software is not None
    assessment = SoftwareUninstallSafetyPolicy(Path("D:/Agent"), Path("C:/Windows")).assess(
        software
    )
    assert assessment.safety_class is SoftwareSafetyClass.SHARED_RUNTIME
