"""Independent standard-user readback for every real Stage 4X3 action."""

from __future__ import annotations

from pc_manager_agent.domain.elevated_broker import (
    ElevatedBrokerResultEnvelope,
    ElevatedVerificationStatus,
    MachineMsiResultEvidence,
    MachineStartupResultEvidence,
    ServiceStartupResultEvidence,
)
from pc_manager_agent.domain.privileged_actions import (
    MachineMsiUninstallPayload,
    PrivilegedActionEnvelope,
    ServiceStartPayload,
    ServiceStartupTypeChangePayload,
    ServiceStartupTypeRestorePayload,
    ServiceStopPayload,
    StartupMachineDisablePayload,
    StartupMachineRestorePayload,
)
from pc_manager_agent.domain.service_actions import ServiceState
from pc_manager_agent.domain.startup_actions import StartupIdentity
from pc_manager_agent.orchestration.software_target_resolver import SoftwareTargetResolver
from pc_manager_agent.platform_support.msi_uninstall import MsiProductInventoryPlatform
from pc_manager_agent.platform_support.service_control import ServiceControlPlatform
from pc_manager_agent.platform_support.startup import StartupManagementPlatform
from pc_manager_agent.tools.manifest import CancellationToken


class Stage4X3PostconditionVerifier:
    """Require Broker verification and fresh local state; fail closed on read errors."""

    def __init__(
        self,
        service_control: ServiceControlPlatform,
        startup: StartupManagementPlatform,
        software: SoftwareTargetResolver,
        msi_inventory: MsiProductInventoryPlatform,
        *,
        max_items: int = 5_000,
    ) -> None:
        self._service = service_control
        self._startup = startup
        self._software = software
        self._msi = msi_inventory
        self._max_items = max_items

    def verify(
        self,
        envelope: PrivilegedActionEnvelope,
        result: ElevatedBrokerResultEnvelope,
    ) -> bool:
        """Dispatch one read-only verifier without trusting display text or exit codes."""
        if (
            result.result.verification_status is not ElevatedVerificationStatus.VERIFIED
            or result.result.action_type is not envelope.request.action_type
            or result.result.target_identity_hash != envelope.request.target_identity_hash
        ):
            return False
        try:
            payload = envelope.request.payload
            if isinstance(payload, (ServiceStartPayload, ServiceStopPayload)):
                return self._verify_service_control(envelope, payload)
            if isinstance(
                payload,
                (ServiceStartupTypeChangePayload, ServiceStartupTypeRestorePayload),
            ):
                return self._verify_service_startup(payload, result)
            if isinstance(payload, (StartupMachineDisablePayload, StartupMachineRestorePayload)):
                return self._verify_machine_startup(payload, result)
            if isinstance(payload, MachineMsiUninstallPayload):
                return self._verify_machine_msi(payload, result)
        except (OSError, RuntimeError, ValueError):
            return False
        return False

    def _verify_service_control(
        self,
        envelope: PrivilegedActionEnvelope,
        payload: ServiceStartPayload | ServiceStopPayload,
    ) -> bool:
        current = self._service.inspect(payload.service_identity.service_name)
        expected = (
            ServiceState.RUNNING
            if isinstance(payload, ServiceStartPayload)
            else ServiceState.STOPPED
        )
        return bool(
            current is not None
            and current.identity.canonical_digest() == envelope.request.target_identity_hash
            and current.state is expected
        )

    def _verify_service_startup(
        self,
        payload: ServiceStartupTypeChangePayload | ServiceStartupTypeRestorePayload,
        result: ElevatedBrokerResultEnvelope,
    ) -> bool:
        evidence = result.result.action_evidence
        if not isinstance(evidence, ServiceStartupResultEvidence) or not evidence.runtime_unchanged:
            return False
        current = self._service.inspect(payload.service_identity.service_name)
        target = (
            payload.expected_current_configuration.model_copy(
                update={"startup_type": payload.requested_startup_type}
            )
            if isinstance(payload, ServiceStartupTypeChangePayload)
            else payload.target_original_configuration
        )
        return bool(
            current is not None
            and current.identity.canonical_digest() == payload.service_identity.canonical_digest()
            and current.startup_configuration == target
            and current.state is payload.expected_runtime_state
            and evidence.after_configuration == target
            and evidence.after_runtime_state is payload.expected_runtime_state
        )

    def _verify_machine_startup(
        self,
        payload: StartupMachineDisablePayload | StartupMachineRestorePayload,
        result: ElevatedBrokerResultEnvelope,
    ) -> bool:
        evidence = result.result.action_evidence
        if not isinstance(evidence, MachineStartupResultEvidence):
            return False
        identity = StartupIdentity(source=payload.source, registry=payload.registry_identity)
        present = self._startup.inspect(identity) is not None
        expected_present = isinstance(payload, StartupMachineRestorePayload)
        return present is expected_present and evidence.value_present_after is expected_present

    def _verify_machine_msi(
        self,
        payload: MachineMsiUninstallPayload,
        result: ElevatedBrokerResultEnvelope,
    ) -> bool:
        evidence = result.result.action_evidence
        if (
            not isinstance(evidence, MachineMsiResultEvidence)
            or evidence.monitoring_detached
            or evidence.product_registration_present_after is not False
            or evidence.software_identity_present_after is not False
        ):
            return False
        cancellation = CancellationToken()
        software, snapshot = self._software.inspect(
            payload.software_identity_digest,
            self._max_items,
            cancellation,
        )
        registrations = self._msi.registrations(payload.product_code)
        return bool(
            software is None
            and not snapshot.inventory.truncated
            and not snapshot.inventory.warnings
            and not any(item.installed for item in registrations)
        )
