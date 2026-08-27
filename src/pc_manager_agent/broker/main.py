"""Independent one-shot elevated Broker executable entry point."""

from __future__ import annotations

import argparse
import ctypes
import os
import sys
from pathlib import Path
from uuid import UUID

from platformdirs import user_data_path

from pc_manager_agent import __version__

_EXIT_SUCCESS = 0
_EXIT_ARGUMENTS = 20
_EXIT_NOT_FROZEN = 21
_EXIT_NOT_ELEVATED = 22
_EXIT_TRUST = 23
_EXIT_IPC = 24
_EXIT_EXECUTION = 25


def build_parser() -> argparse.ArgumentParser:
    """Build the fixed three-field opaque bootstrap command line."""
    parser = argparse.ArgumentParser(allow_abbrev=False, add_help=False)
    parser.add_argument("--broker-instance", type=UUID, required=True)
    parser.add_argument("--rendezvous", required=True)
    parser.add_argument("--protocol-version", type=int, choices=(1,), required=True)
    parser.add_argument("--caller-pid", type=int, required=True)
    parser.add_argument("--agent-instance", type=UUID, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one authenticated request and exit without persistence or retry."""
    try:
        arguments = build_parser().parse_args(argv)
    except SystemExit:
        return _EXIT_ARGUMENTS
    if not getattr(sys, "frozen", False):
        return _EXIT_NOT_FROZEN
    trust_mode_value = (
        os.environ.get(
            "PC_MANAGER_PRIVILEGED_BROKER_TRUST_MODE",
            "production",
        )
        .strip()
        .upper()
    )
    _harden_process_environment(Path(sys.executable).resolve(strict=True).parent)

    # Imports intentionally occur after the fixed bootstrap parse and environment cleanup.
    from pc_manager_agent.audit.elevated_broker import ElevatedBrokerAuditLogger
    from pc_manager_agent.audit.repository import AuditRepository
    from pc_manager_agent.domain.elevated_broker import (
        BrokerTrustMode,
        WindowsProcessIdentity,
    )
    from pc_manager_agent.orchestration.service_dependency_analyzer import (
        ServiceDependencyAnalyzer,
    )
    from pc_manager_agent.orchestration.software_capability import UninstallCapabilityResolver
    from pc_manager_agent.orchestration.software_execution_preflight import (
        SoftwareExecutionPreflight,
    )
    from pc_manager_agent.orchestration.software_inventory import SoftwareInventoryService
    from pc_manager_agent.orchestration.software_msi_validation import MsiProductValidator
    from pc_manager_agent.orchestration.software_target_resolver import SoftwareTargetResolver
    from pc_manager_agent.orchestration.software_uninstall_verifier import MsiUninstallVerifier
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
    from pc_manager_agent.platform_support.windows.data_protection import (
        WindowsCurrentUserDataProtector,
    )
    from pc_manager_agent.platform_support.windows.msi_uninstall import (
        WindowsMsiProductInventory,
        WindowsMsiUninstallPlatform,
    )
    from pc_manager_agent.platform_support.windows.named_pipe import WindowsBrokerPipeServer
    from pc_manager_agent.platform_support.windows.process_identity import (
        WindowsBrokerBinaryInspector,
        capture_current_process_identity,
        capture_impersonated_pipe_client_identity,
    )
    from pc_manager_agent.platform_support.windows.service_control import (
        WindowsServiceControlPlatform,
        current_windows_username,
    )
    from pc_manager_agent.platform_support.windows.service_startup import (
        WindowsServiceStartupPlatform,
    )
    from pc_manager_agent.platform_support.windows.software_inventory import (
        WindowsSoftwareInventoryPlatform,
    )
    from pc_manager_agent.platform_support.windows.startup_management import (
        WindowsStartupManagementPlatform,
    )
    from pc_manager_agent.platform_support.windows.system_diagnostics import (
        WindowsSystemDiagnosticsPlatform,
    )
    from pc_manager_agent.privileged.broker_identity import BrokerTrustPolicy
    from pc_manager_agent.privileged.broker_session import (
        AuthenticatedPipeStream,
        ElevatedBrokerServerSession,
    )
    from pc_manager_agent.privileged.dispatcher import PrivilegedActionDispatcher
    from pc_manager_agent.privileged.elevated_broker import ElevatedPrivilegedBroker
    from pc_manager_agent.privileged.machine_msi_handler import (
        WindowsMachineMsiPrivilegedHandler,
    )
    from pc_manager_agent.privileged.machine_startup_handler import (
        WindowsMachineStartupPrivilegedHandler,
    )
    from pc_manager_agent.privileged.manifests import build_stage4x3_manifest_registry
    from pc_manager_agent.privileged.serialization import PrivilegedRequestSerializer
    from pc_manager_agent.privileged.service_control_dispatch import ServiceControlDispatchHandler
    from pc_manager_agent.privileged.service_handler import WindowsServicePrivilegedHandler
    from pc_manager_agent.privileged.service_startup_handler import (
        WindowsServiceStartupPrivilegedHandler,
    )
    from pc_manager_agent.safety.machine_msi_policy import MachineMsiExecutionPolicy
    from pc_manager_agent.safety.machine_startup_policy import MachineStartupSafetyPolicy
    from pc_manager_agent.safety.service_policy import ServiceSafetyPolicy
    from pc_manager_agent.safety.service_startup_policy import ServiceStartupSafetyPolicy
    from pc_manager_agent.safety.software_uninstall_policy import SoftwareUninstallSafetyPolicy

    try:
        trust_mode = BrokerTrustMode(trust_mode_value)
    except ValueError:
        return _EXIT_TRUST
    database_path = user_data_path("WindowsPCManagerAgent", ensure_exists=False) / "state.db"
    broker_path = Path(sys.executable).resolve(strict=True)
    try:
        binary = WindowsBrokerBinaryInspector().inspect(broker_path)
        identity = capture_current_process_identity()
        if not identity.elevated or identity.integrity_level != "HIGH":
            return _EXIT_NOT_ELEVATED
        trust = BrokerTrustPolicy(trust_mode, expected_sha256=binary.sha256)
        trust.require_binary(binary)
    except Exception:
        return _EXIT_TRUST
    audit_repository = AuditRepository(database_path)
    privileged_repository = PrivilegedActionRepository(database_path)
    service_startup_vault = ServiceStartupBackupVault(
        database_path,
        WindowsCurrentUserDataProtector(),
    )
    service_startup_history = ServiceStartupActionRepository(database_path)
    startup_vault = StartupBackupVault(database_path, WindowsCurrentUserDataProtector())
    startup_history = StartupActionRepository(database_path)
    msi_activity = MsiUninstallRepository(database_path)
    pipe_server = None
    stream = None
    try:
        audit_repository.initialize()
        privileged_repository.initialize(reconcile_active=False)
        service_startup_vault.initialize()
        service_startup_history.initialize(reconcile_active=False)
        startup_vault.initialize()
        startup_history.initialize(reconcile_active=False)
        msi_activity.initialize(reconcile_active=False)
        audit = ElevatedBrokerAuditLogger(audit_repository, app_version=__version__)
        control = WindowsServiceControlPlatform()
        base_service_policy = ServiceSafetyPolicy(
            current_username=current_windows_username(),
            agent_root=broker_path.parent,
        )
        service_control = ServiceControlDispatchHandler(
            WindowsServicePrivilegedHandler(
                control,
                base_service_policy,
                ServiceDependencyAnalyzer(),
            )
        )
        service_startup = WindowsServiceStartupPrivilegedHandler(
            control,
            WindowsServiceStartupPlatform(),
            ServiceStartupSafetyPolicy(base_service_policy),
            service_startup_vault,
            service_startup_history,
        )
        startup_platform = WindowsStartupManagementPlatform(
            database_path.parent / "disabled_startup"
        )
        machine_startup = WindowsMachineStartupPrivilegedHandler(
            startup_platform,
            MachineStartupSafetyPolicy(agent_root=broker_path.parent),
            startup_vault,
            startup_history,
        )
        software_resolver = SoftwareTargetResolver(
            SoftwareInventoryService(WindowsSoftwareInventoryPlatform())
        )
        msi_inventory = WindowsMsiProductInventory()
        machine_msi = WindowsMachineMsiPrivilegedHandler(
            software_resolver,
            UninstallCapabilityResolver(),
            MsiProductValidator(msi_inventory),
            SoftwareUninstallSafetyPolicy(agent_root=broker_path.parent),
            MachineMsiExecutionPolicy(),
            SoftwareExecutionPreflight(WindowsSystemDiagnosticsPlatform()),
            WindowsMsiUninstallPlatform(),
            MsiUninstallVerifier(software_resolver, msi_inventory),
            msi_activity,
            privileged_repository,
        )
        dispatcher = PrivilegedActionDispatcher(
            build_stage4x3_manifest_registry(),
            (service_control, service_startup, machine_startup, machine_msi),
        )
        broker = ElevatedPrivilegedBroker(
            PrivilegedRequestSerializer(),
            privileged_repository,
            dispatcher,
            audit,
            trust,
        )
        pipe_server = WindowsBrokerPipeServer(arguments.rendezvous, identity.user_sid)
        stream = pipe_server.accept(timeout_seconds=30.0)

        def read_caller(connected: AuthenticatedPipeStream) -> WindowsProcessIdentity:
            return capture_impersonated_pipe_client_identity(
                connected.native_handle,
                process_id=connected.peer_process_id,
                session_id=connected.peer_session_id,
            )

        session = ElevatedBrokerServerSession(
            broker,
            identity,
            binary,
            read_caller,
        )
        session.serve(
            stream,
            broker_instance_id=arguments.broker_instance,
            expected_caller_process_id=arguments.caller_pid,
            expected_agent_instance_id=arguments.agent_instance,
        )
        return _EXIT_SUCCESS
    except Exception:
        # Details stay in redacted durable audit when an authenticated request identity exists.
        return _EXIT_EXECUTION if stream is not None else _EXIT_IPC
    finally:
        if stream is not None:
            stream.close()
        if pipe_server is not None:
            pipe_server.close()
        msi_activity.close()
        startup_history.close()
        startup_vault.close()
        service_startup_history.close()
        service_startup_vault.close()
        privileged_repository.close()
        audit_repository.close()


def _harden_process_environment(application_directory: Path) -> None:
    """Remove provider secrets/Python overrides and narrow process DLL lookup early."""
    sensitive_markers = (
        "API_KEY",
        "ACCESS_TOKEN",
        "AUTH_TOKEN",
        "PASSWORD",
        "SECRET",
        "COOKIE",
    )
    for name in tuple(os.environ):
        upper = name.upper()
        if upper.startswith("PYTHON") or any(marker in upper for marker in sensitive_markers):
            os.environ.pop(name, None)
    os.chdir(application_directory)
    try:
        ctypes.windll.kernel32.SetDefaultDllDirectories(0x00000800 | 0x00000400)
        ctypes.windll.kernel32.SetDllDirectoryW("")
    except (AttributeError, OSError):
        # Windows 11 provides both APIs. Failure is handled by binary/install trust policy.
        return


if __name__ == "__main__":
    raise SystemExit(main())
