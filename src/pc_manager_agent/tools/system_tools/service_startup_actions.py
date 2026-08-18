"""Three exact service startup tools; no generic service configuration tool exists."""

from pydantic import BaseModel

from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.service_startup_actions import (
    ServiceStartupActionRequest,
    ServiceStartupActionType,
    ServiceStartupBackupPayload,
    ServiceStartupMutationResult,
)
from pc_manager_agent.persistence.service_startup_actions import ServiceStartupBackupVault
from pc_manager_agent.platform_support.service_startup import ServiceStartupPlatform
from pc_manager_agent.rollback.service_startup_commands import (
    ServiceStartupConfigurationCommand,
)
from pc_manager_agent.tools.manifest import CancellationToken, ToolManifest


class SetServiceAutomaticTool:
    """Set one exact backed-up service to non-delayed Automatic."""

    def __init__(
        self,
        platform: ServiceStartupPlatform,
        vault: ServiceStartupBackupVault,
    ) -> None:
        self._platform = platform
        self._vault = vault
        self._manifest = _manifest(
            "system.service.startup.set_automatic",
            "Set one exact approved service to non-delayed Automatic",
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return the fixed R2 Automatic transition manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Verify backup binding and invoke only the exact Automatic command."""
        typed = ServiceStartupActionRequest.model_validate(request)
        if typed.action is not ServiceStartupActionType.SET_AUTOMATIC:
            raise ValueError("set_automatic tool accepts only SET_AUTOMATIC")
        _require_backup(self._vault, typed)
        return ServiceStartupConfigurationCommand(self._platform, typed, cancellation).execute()


class SetServiceManualTool:
    """Set one exact backed-up service to Manual."""

    def __init__(
        self,
        platform: ServiceStartupPlatform,
        vault: ServiceStartupBackupVault,
    ) -> None:
        self._platform = platform
        self._vault = vault
        self._manifest = _manifest(
            "system.service.startup.set_manual",
            "Set one exact approved service to Manual",
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return the fixed R2 Manual transition manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Verify backup binding and invoke only the exact Manual command."""
        typed = ServiceStartupActionRequest.model_validate(request)
        if typed.action is not ServiceStartupActionType.SET_MANUAL:
            raise ValueError("set_manual tool accepts only SET_MANUAL")
        _require_backup(self._vault, typed)
        return ServiceStartupConfigurationCommand(self._platform, typed, cancellation).execute()


class RestoreServiceStartupTool:
    """Restore one exact service from an Agent-owned conflict-checked change record."""

    def __init__(
        self,
        platform: ServiceStartupPlatform,
        vault: ServiceStartupBackupVault,
    ) -> None:
        self._platform = platform
        self._vault = vault
        self._manifest = _manifest(
            "system.service.startup.restore",
            "Restore one exact Automatic/Manual service startup configuration",
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return the fixed R2 conflict-checked restore manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Verify current-operation backup binding and invoke only restore."""
        typed = ServiceStartupActionRequest.model_validate(request)
        if typed.action is not ServiceStartupActionType.RESTORE:
            raise ValueError("service startup restore tool accepts only RESTORE")
        _require_backup(self._vault, typed)
        return ServiceStartupConfigurationCommand(self._platform, typed, cancellation).execute()


def _require_backup(
    vault: ServiceStartupBackupVault,
    request: ServiceStartupActionRequest,
) -> ServiceStartupBackupPayload:
    payload = vault.load(request.backup_id, expected_digest=request.backup_digest)
    if payload.stable_identity.canonical_digest() != request.identity.canonical_digest():
        raise ValueError("Service startup backup identity does not match tool arguments")
    if payload.original_configuration != request.expected_source_configuration:
        raise ValueError("Service startup backup configuration does not match source")
    if payload.original_runtime_state is not request.expected_runtime_state:
        raise ValueError("Service startup backup runtime state does not match source")
    return payload


def _manifest(name: str, description: str) -> ToolManifest:
    return ToolManifest(
        name=name,
        description=description,
        input_model=ServiceStartupActionRequest,
        output_model=ServiceStartupMutationResult,
        risk_level=RiskLevel.R2,
        required_permissions=(
            "ordinary-user",
            "existing-service-DACL-SERVICE_CHANGE_CONFIG",
        ),
        read_only=False,
        idempotent=False,
        supports_cancellation=True,
        rollback_level=RollbackLevel.FULL,
        preconditions=(
            "safe exact current-user third-party own-process service",
            "verified current-user DPAPI startup configuration backup",
            "two consumed same-transition confirmations",
            "execution-time stable identity, source configuration, impact, and state match",
        ),
        postconditions=(
            "startup type read back as requested",
            "runtime state remains unchanged",
        ),
        timeout_seconds=30.0,
        max_batch_size=1,
        audit_fields=(
            "transaction_id",
            "action",
            "identity_digest",
            "backup_digest",
            "verified",
            "runtime_unchanged",
        ),
        supported_platforms=("windows",),
        requires_confirmation=True,
        requires_runtime_confirmation=True,
        supports_preview=True,
        irreversible=False,
    )
