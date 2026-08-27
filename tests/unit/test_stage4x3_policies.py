"""Stage 4X3 policy tests that prove elevation never widens business safety."""

from __future__ import annotations

from pathlib import Path

from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.software_uninstall_analysis import (
    SoftwareSafetyAssessment,
    SoftwareSafetyClass,
    SoftwareSafetyDecision,
    canonical_digest,
)
from pc_manager_agent.domain.software_uninstall_execution import (
    MsiExecutionDecision,
    MsiInstallContext,
    ValidatedMsiProduct,
)
from pc_manager_agent.domain.startup_actions import (
    RegistryStartupIdentity,
    StartupActionType,
    StartupEntryStatus,
    StartupIdentity,
    StartupManagementMode,
    StartupObservation,
    StartupSafetyDecision,
    StartupSource,
)
from pc_manager_agent.domain.system_diagnostics import SoftwareArchitecture, SoftwareScope
from pc_manager_agent.safety.machine_msi_policy import MachineMsiExecutionPolicy
from pc_manager_agent.safety.machine_startup_policy import MachineStartupSafetyPolicy


def _machine_startup(executable: Path) -> StartupObservation:
    digest = "a" * 64
    return StartupObservation(
        identity=StartupIdentity(
            source=StartupSource.HKLM_RUN,
            registry=RegistryStartupIdentity(
                hive="HKLM",
                key_path=r"Software\Microsoft\Windows\CurrentVersion\Run",
                value_name="Example",
                value_type=1,
                value_data_digest=digest,
                command_fingerprint=digest,
                resolved_executable_path=executable,
                registry_view="64",
            ),
        ),
        display_name="Example App",
        publisher="Example Corporation",
        executable_path=executable,
        command_summary="example.exe (0 arguments)",
        scope="ALL_USERS",
        status=StartupEntryStatus.ENABLED,
        status_evidence="Exact HKLM Run value",
        management_mode=StartupManagementMode.READ_ONLY,
    )


def _machine_product() -> ValidatedMsiProduct:
    code = "{12345678-1234-1234-1234-1234567890AB}"
    return ValidatedMsiProduct(
        product_code=code,
        product_code_digest=canonical_digest(code),
        identity_digest="1" * 64,
        metadata_digest="2" * 64,
        capability_digest="3" * 64,
        registration_digest="4" * 64,
        install_context=MsiInstallContext.MACHINE,
        display_name="Example App",
        display_version="1.0",
        publisher="Example Publisher",
        scope=SoftwareScope.LOCAL_MACHINE,
        architecture=SoftwareArchitecture.X64,
        source_anchor_digest="5" * 64,
    )


def _analysis(safety_class: SoftwareSafetyClass) -> SoftwareSafetyAssessment:
    return SoftwareSafetyAssessment(
        safety_class=safety_class,
        decision=(
            SoftwareSafetyDecision.BLOCKED
            if safety_class
            in {
                SoftwareSafetyClass.SECURITY_SOFTWARE,
                SoftwareSafetyClass.SHARED_RUNTIME,
            }
            else SoftwareSafetyDecision.PREVIEW_ALLOWED
        ),
        evidence=("Synthetic deterministic classification",),
        reasons=("Synthetic test",),
    )


def test_machine_startup_policy_allows_only_exact_third_party_hklm_run(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "example.exe"
    executable.write_bytes(b"MZ")
    policy = MachineStartupSafetyPolicy(
        agent_root=tmp_path / "agent",
        windows_directory=tmp_path / "Windows",
    )
    allowed = policy.assess(_machine_startup(executable), StartupActionType.DISABLE)
    assert allowed.decision is StartupSafetyDecision.ALLOW

    for changed in (
        _machine_startup(executable).model_copy(update={"publisher": None}),
        _machine_startup(executable).model_copy(update={"scope": "CURRENT_USER"}),
        _machine_startup(executable).model_copy(update={"display_name": "Endpoint Security"}),
        _machine_startup(executable).model_copy(
            update={
                "identity": _machine_startup(executable).identity.model_copy(
                    update={
                        "registry": _machine_startup(executable).identity.registry.model_copy(
                            update={"registry_view": "NATIVE"}
                        )
                    }
                )
            }
        ),
    ):
        assert policy.assess(changed, StartupActionType.DISABLE).decision is (
            StartupSafetyDecision.BLOCK
        )


def test_machine_msi_policy_preserves_protected_classes_and_scope() -> None:
    policy = MachineMsiExecutionPolicy()
    product = _machine_product()
    ordinary = policy.assess(product, _analysis(SoftwareSafetyClass.USER_APPLICATION))
    developer = policy.assess(product, _analysis(SoftwareSafetyClass.DEVELOPER_TOOL))
    security = policy.assess(product, _analysis(SoftwareSafetyClass.SECURITY_SOFTWARE))
    shared = policy.assess(product, _analysis(SoftwareSafetyClass.SHARED_RUNTIME))
    current_user = product.model_copy(
        update={
            "install_context": MsiInstallContext.USER_UNMANAGED,
            "scope": SoftwareScope.CURRENT_USER,
        }
    )

    assert ordinary.decision is MsiExecutionDecision.ALLOW
    assert ordinary.risk_level is RiskLevel.R2
    assert developer.decision is MsiExecutionDecision.ALLOW
    assert developer.risk_level is RiskLevel.R2_HIGH_IMPACT
    assert security.decision is MsiExecutionDecision.BLOCK
    assert shared.decision is MsiExecutionDecision.BLOCK
    assert (
        policy.assess(
            current_user,
            _analysis(SoftwareSafetyClass.USER_APPLICATION),
        ).decision
        is MsiExecutionDecision.BLOCK
    )
