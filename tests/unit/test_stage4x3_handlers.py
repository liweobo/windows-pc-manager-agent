"""Direct Stage 4X3 handler tests over synthetic local evidence only."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pc_manager_agent.domain.privileged_actions import (
    MachineMsiUninstallPayload,
    PrivilegedActionRequest,
    PrivilegedActionType,
    ServiceStartupTypeChangePayload,
    StartupMachineDisablePayload,
    canonical_model_digest,
)
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.service_actions import ServiceStartupType
from pc_manager_agent.domain.service_startup_actions import (
    ServiceStartupActionType,
    ServiceStartupBackupPayload,
)
from pc_manager_agent.domain.software_uninstall_analysis import RegistryHive
from pc_manager_agent.domain.software_uninstall_execution import (
    MsiInstallContext,
    MsiInstallerExecutionResult,
    MsiInstallerResultCategory,
    MsiPreflightState,
    MsiProductRegistration,
    MsiUninstallVerification,
    MsiVerificationState,
    ValidatedMsiProduct,
)
from pc_manager_agent.domain.startup_actions import (
    RegistryStartupIdentity,
    StartupActionType,
    StartupBackupPayload,
    StartupEntryStatus,
    StartupIdentity,
    StartupManagementMode,
    StartupMutationResult,
    StartupObservation,
    StartupSource,
)
from pc_manager_agent.domain.system_diagnostics import SoftwareScope
from pc_manager_agent.orchestration.software_capability import UninstallCapabilityResolver
from pc_manager_agent.orchestration.software_execution_preflight import (
    SoftwareExecutionPreflight,
)
from pc_manager_agent.orchestration.software_inventory import SoftwareInventoryService
from pc_manager_agent.orchestration.software_msi_validation import MsiProductValidator
from pc_manager_agent.orchestration.software_target_resolver import SoftwareTargetResolver
from pc_manager_agent.persistence.privileged_actions import PrivilegedActionRepository
from pc_manager_agent.persistence.service_startup_actions import (
    ServiceStartupActionRepository,
    ServiceStartupBackupVault,
)
from pc_manager_agent.persistence.software_uninstall_execution import MsiUninstallRepository
from pc_manager_agent.persistence.startup_actions import (
    StartupActionRepository,
    StartupBackupVault,
)
from pc_manager_agent.privileged.machine_msi_handler import (
    WindowsMachineMsiPrivilegedHandler,
    machine_msi_state_digest,
)
from pc_manager_agent.privileged.machine_startup_handler import (
    WindowsMachineStartupPrivilegedHandler,
)
from pc_manager_agent.privileged.service_startup_handler import (
    WindowsServiceStartupPrivilegedHandler,
)
from pc_manager_agent.safety.machine_msi_policy import MachineMsiExecutionPolicy
from pc_manager_agent.safety.machine_startup_policy import MachineStartupSafetyPolicy
from pc_manager_agent.safety.service_policy import ServiceSafetyPolicy
from pc_manager_agent.safety.service_startup_policy import (
    ServiceStartupSafetyPolicy,
    build_service_startup_impact,
)
from pc_manager_agent.safety.software_uninstall_policy import SoftwareUninstallSafetyPolicy
from pc_manager_agent.tools.manifest import CancellationToken
from tests.fixtures.msi_uninstall import FakeMsiInventory, ReadySystemPlatform
from tests.fixtures.software_analysis import FakeSoftwareInventoryPlatform, msi_entry
from tests.stage4c1_support import FakeServicePlatform, service_observation
from tests.stage4c2_support import FakeProtector, FakeServiceStartupPlatform, configuration


def _request(
    action_type: PrivilegedActionType,
    payload: object,
    target_identity_hash: str,
) -> PrivilegedActionRequest:
    return PrivilegedActionRequest.model_construct(
        action_type=action_type,
        payload=payload,
        risk_level=RiskLevel.R3,
        target_identity_hash=target_identity_hash,
        plan_id=uuid4(),
    )


def test_service_startup_handler_revalidates_executes_and_records(tmp_path: Path) -> None:
    binary = tmp_path / "service.exe"
    binary.write_bytes(b"MZ")
    observation = service_observation(binary, start_type=2)
    target = configuration(ServiceStartupType.MANUAL)
    control = FakeServicePlatform(observation)
    platform = FakeServiceStartupPlatform(control)
    vault = ServiceStartupBackupVault(tmp_path / "state.db", FakeProtector())
    history = ServiceStartupActionRepository(tmp_path / "state.db")
    vault.initialize()
    history.initialize()
    try:
        backup = vault.store(
            ServiceStartupBackupPayload(
                stable_identity=observation.identity,
                display_name=observation.display_name,
                original_configuration=observation.startup_configuration,
                original_runtime_state=observation.state,
            )
        )
        policy = ServiceStartupSafetyPolicy(
            ServiceSafetyPolicy(
                current_username=r"DESKTOP\alice",
                agent_root=tmp_path / "agent",
                windows_directory=tmp_path / "Windows",
            )
        )
        impact = build_service_startup_impact(observation)
        assessment = policy.assess(observation, ServiceStartupActionType.SET_MANUAL, target)
        payload = ServiceStartupTypeChangePayload(
            source_transaction_id=uuid4(),
            service_identity=observation.identity,
            expected_current_configuration=observation.startup_configuration,
            requested_startup_type=ServiceStartupType.MANUAL,
            expected_runtime_state=observation.state,
            impact_digest=impact.canonical_digest(),
            safety_digest=canonical_model_digest(assessment.model_dump(mode="json")),
            backup_id=backup.backup_id,
            backup_digest=backup.payload_digest,
        )
        request = _request(
            PrivilegedActionType.SERVICE_STARTUP_TYPE_CHANGE,
            payload,
            observation.identity.canonical_digest(),
        )
        handler = WindowsServiceStartupPrivilegedHandler(control, platform, policy, vault, history)
        fresh = handler.require(request)
        dispatched: list[bool] = []
        result = handler.execute_and_verify(
            request,
            fresh,
            CancellationToken(),
            lambda: dispatched.append(True),
        )
        assert dispatched == [True]
        assert result.verified
        assert control.observation.startup_configuration == target
        assert history.get_change(backup.backup_id).written_configuration == target
    finally:
        history.close()
        vault.close()


class _MachineStartupPlatform:
    def __init__(self, observation: StartupObservation) -> None:
        self.observation: StartupObservation | None = observation

    def inspect(self, identity: StartupIdentity) -> StartupObservation | None:
        if self.observation is None:
            return None
        return (
            self.observation
            if self.observation.identity.canonical_digest() == identity.canonical_digest()
            else None
        )

    def disable_machine_run(
        self,
        payload: StartupBackupPayload,
        cancellation: CancellationToken,
        on_dispatched=None,  # type: ignore[no-untyped-def]
    ) -> StartupMutationResult:
        del cancellation
        before = self.observation
        assert before is not None
        if on_dispatched is not None:
            on_dispatched()
        self.observation = None
        now = datetime.now(UTC)
        return StartupMutationResult(
            action=StartupActionType.DISABLE,
            identity_digest=payload.original_identity.canonical_digest(),
            before_state_digest=before.current_state_digest(),
            after_status=StartupEntryStatus.AGENT_DISABLED,
            verified=True,
            message="synthetic exact value removed",
            started_at=now,
            completed_at=now,
        )


def test_machine_startup_handler_requires_backup_and_records_disable(tmp_path: Path) -> None:
    executable = tmp_path / "startup.exe"
    executable.write_bytes(b"MZ")
    digest = "a" * 64
    identity = StartupIdentity(
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
    )
    observation = StartupObservation(
        identity=identity,
        display_name="Example App",
        publisher="Example Publisher",
        executable_path=executable,
        command_summary="startup.exe (0 arguments)",
        scope="ALL_USERS",
        status=StartupEntryStatus.ENABLED,
        status_evidence="Synthetic exact HKLM Run value",
        management_mode=StartupManagementMode.READ_ONLY,
    )
    vault = StartupBackupVault(tmp_path / "state.db", FakeProtector())
    history = StartupActionRepository(tmp_path / "state.db")
    vault.initialize()
    history.initialize()
    try:
        reference = vault.store(
            StartupBackupPayload(
                original_identity=identity,
                source=StartupSource.HKLM_RUN,
                registry_value_data_b64=base64.b64encode(b"example.exe").decode("ascii"),
                registry_value_type=1,
            )
        )
        policy = MachineStartupSafetyPolicy(
            agent_root=tmp_path / "agent",
            windows_directory=tmp_path / "Windows",
        )
        safety = policy.assess(observation, StartupActionType.DISABLE)
        payload = StartupMachineDisablePayload(
            source_transaction_id=uuid4(),
            registry_identity=identity.registry,
            startup_identity_digest=identity.canonical_digest(),
            expected_state_digest=observation.current_state_digest(),
            safety_digest=canonical_model_digest(safety.model_dump(mode="json")),
            backup_id=reference.backup_id,
            backup_digest=reference.payload_digest,
        )
        request = _request(
            PrivilegedActionType.STARTUP_MACHINE_DISABLE,
            payload,
            identity.canonical_digest(),
        )
        platform = _MachineStartupPlatform(observation)
        handler = WindowsMachineStartupPrivilegedHandler(
            platform,  # type: ignore[arg-type]
            policy,
            vault,
            history,
        )
        fresh = handler.require(request)
        result = handler.execute_and_verify(request, fresh, CancellationToken())
        assert result.verified
        assert history.get_disabled(reference.backup_id).identity == identity
    finally:
        history.close()
        vault.close()


class _MachineMsiAdapter:
    def uninstall(
        self,
        product: ValidatedMsiProduct,
        cancellation: CancellationToken,
        on_dispatched=None,  # type: ignore[no-untyped-def]
    ) -> MsiInstallerExecutionResult:
        del product, cancellation
        if on_dispatched is not None:
            on_dispatched()
        return MsiInstallerExecutionResult(
            category=MsiInstallerResultCategory.SUCCESS,
            exit_code=0,
            launched=True,
        )


class _MachineMsiVerifier:
    def verify(self, *args, **kwargs) -> MsiUninstallVerification:  # type: ignore[no-untyped-def]
        del args, kwargs
        return MsiUninstallVerification(
            state=MsiVerificationState.VERIFIED_REMOVED,
            original_identity_present=False,
            original_product_code_present=False,
            inventory_refreshed=True,
            evidence=("synthetic inventories prove absence",),
        )


def test_machine_msi_handler_revalidates_fixed_identity_and_verifies(tmp_path: Path) -> None:
    raw = msi_entry(install_location=tmp_path / "app").model_copy(
        update={
            "raw_source_id": "HKEY_LOCAL_MACHINE|x64|synthetic-msi",
            "scope": SoftwareScope.LOCAL_MACHINE,
            "registry_hive": RegistryHive.LOCAL_MACHINE,
        }
    )
    inventory_platform = FakeSoftwareInventoryPlatform((raw,))
    resolver = SoftwareTargetResolver(SoftwareInventoryService(inventory_platform))
    snapshot = resolver.refresh(5_000, CancellationToken())
    software = snapshot.inventory.entries[0]
    product_code = raw.product_code
    assert product_code is not None
    registration = MsiProductRegistration(
        product_code=product_code,
        context=MsiInstallContext.MACHINE,
        product_name=raw.display_name,
        version=raw.display_version,
        publisher=raw.publisher,
        install_location=raw.install_location,
        installed=True,
    )
    msi_inventory = FakeMsiInventory(registration)
    capability = UninstallCapabilityResolver()
    validator = MsiProductValidator(msi_inventory)
    product = validator.validate_machine(
        software,
        capability.resolve(software, raw),
    )
    analysis_policy = SoftwareUninstallSafetyPolicy(
        agent_root=tmp_path / "agent",
        windows_directory=tmp_path / "Windows",
    )
    execution_policy = MachineMsiExecutionPolicy()
    assessment = execution_policy.assess(product, analysis_policy.assess(software))
    preflight_service = SoftwareExecutionPreflight(ReadySystemPlatform())
    preflight = preflight_service.inspect(software, product, CancellationToken()).model_copy(
        update={"privilege_expected": "required"}
    )
    assert preflight.state is MsiPreflightState.READY
    assessment_digest = canonical_model_digest(assessment.model_dump(mode="json"))
    payload = MachineMsiUninstallPayload(
        source_transaction_id=uuid4(),
        product_code=product.product_code,
        product_code_digest=product.product_code_digest,
        software_identity_digest=product.identity_digest,
        metadata_digest=product.metadata_digest,
        capability_digest=product.capability_digest,
        registration_digest=product.registration_digest,
        execution_assessment_digest=assessment_digest,
        preflight_digest=preflight.canonical_digest(),
        display_name=product.display_name,
        display_version=product.display_version,
        publisher=product.publisher,
        install_context=product.install_context,
        scope=product.scope,
        architecture=product.architecture,
        source_anchor_digest=product.source_anchor_digest,
    )
    request = _request(
        PrivilegedActionType.MSI_UNINSTALL_MACHINE,
        payload,
        product.identity_digest,
    )
    activity = MsiUninstallRepository(tmp_path / "state.db")
    privileged = PrivilegedActionRepository(tmp_path / "state.db")
    activity.initialize()
    privileged.initialize()
    try:
        handler = WindowsMachineMsiPrivilegedHandler(
            resolver,
            capability,
            validator,
            analysis_policy,
            execution_policy,
            preflight_service,
            _MachineMsiAdapter(),  # type: ignore[arg-type]
            _MachineMsiVerifier(),  # type: ignore[arg-type]
            activity,
            privileged,
        )
        fresh = handler.require(request)
        assert fresh.target_state_hash == machine_msi_state_digest(
            product,
            assessment_digest,
            preflight.canonical_digest(),
        )
        result = handler.execute_and_verify(request, fresh, CancellationToken())
        assert result.verified
        assert result.execution_completed
    finally:
        privileged.close()
        activity.close()
