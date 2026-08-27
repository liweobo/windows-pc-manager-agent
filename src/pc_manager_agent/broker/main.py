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
    from pc_manager_agent.persistence.privileged_actions import PrivilegedActionRepository
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
    from pc_manager_agent.privileged.broker_identity import BrokerTrustPolicy
    from pc_manager_agent.privileged.broker_session import (
        AuthenticatedPipeStream,
        ElevatedBrokerServerSession,
    )
    from pc_manager_agent.privileged.elevated_broker import ElevatedPrivilegedBroker
    from pc_manager_agent.privileged.serialization import PrivilegedRequestSerializer
    from pc_manager_agent.privileged.service_handler import WindowsServicePrivilegedHandler
    from pc_manager_agent.safety.service_policy import ServiceSafetyPolicy

    try:
        trust_mode = BrokerTrustMode(trust_mode_value)
    except ValueError:
        return _EXIT_TRUST
    database_path = user_data_path("WindowsPCManagerAgent", ensure_exists=False) / "state.db"
    broker_path = Path(sys.executable).resolve(strict=True)
    try:
        binary = WindowsBrokerBinaryInspector().inspect(broker_path)
        identity = capture_current_process_identity()
        if not identity.elevated or identity.integrity_level not in {"HIGH", "SYSTEM"}:
            return _EXIT_NOT_ELEVATED
        trust = BrokerTrustPolicy(trust_mode, expected_sha256=binary.sha256)
        trust.require_binary(binary)
    except Exception:
        return _EXIT_TRUST
    audit_repository = AuditRepository(database_path)
    privileged_repository = PrivilegedActionRepository(database_path)
    pipe_server = None
    stream = None
    try:
        audit_repository.initialize()
        privileged_repository.initialize(reconcile_active=False)
        audit = ElevatedBrokerAuditLogger(audit_repository, app_version=__version__)
        platform = WindowsServiceControlPlatform()
        handler = WindowsServicePrivilegedHandler(
            platform,
            ServiceSafetyPolicy(
                current_username=current_windows_username(),
                agent_root=broker_path.parent,
            ),
            ServiceDependencyAnalyzer(),
        )
        broker = ElevatedPrivilegedBroker(
            PrivilegedRequestSerializer(),
            privileged_repository,
            handler,
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
