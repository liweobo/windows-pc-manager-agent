"""Narrow registered startup tools; no generic registry or filesystem primitive exists."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel

from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.startup_actions import (
    StartupActionRequest,
    StartupActionType,
    StartupBackupPayload,
    StartupEntryStatus,
    StartupMutationResult,
)
from pc_manager_agent.persistence.startup_actions import (
    StartupActionRepository,
    StartupBackupVault,
)
from pc_manager_agent.platform_support.startup import StartupManagementPlatform
from pc_manager_agent.rollback.startup_commands import (
    DisableStartupCommand,
    RestoreStartupCommand,
)
from pc_manager_agent.tools.manifest import CancellationToken, ToolManifest


class DisableStartupTool:
    """Disable exactly one backed-up current-user startup entry."""

    def __init__(
        self,
        platform: StartupManagementPlatform,
        vault: StartupBackupVault,
        repository: StartupActionRepository,
    ) -> None:
        self._platform = platform
        self._vault = vault
        self._repository = repository
        self._manifest = _manifest("startup.disable", "Disable one exact backed-up entry")

    @property
    def manifest(self) -> ToolManifest:
        """Return the fixed R2 reversible disable manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Revalidate backup binding, execute, verify, and reverse a failed verification."""
        typed = StartupActionRequest.model_validate(request)
        if typed.action is not StartupActionType.DISABLE:
            raise ValueError("startup.disable accepts only DISABLE")
        if cancellation.is_cancelled:
            raise RuntimeError("Startup disable was cancelled before mutation")
        payload = self._load_payload(typed)
        command = DisableStartupCommand(self._platform, payload)
        started = datetime.now(UTC)
        command.execute()
        verified = command.verify()
        rolled_back = False
        rollback_verified = False
        if not verified:
            rolled_back = True
            rollback_verified = command.rollback()
        return StartupMutationResult(
            action=typed.action,
            identity_digest=typed.identity.canonical_digest(),
            before_state_digest=typed.expected_state_digest,
            after_status=(
                StartupEntryStatus.AGENT_DISABLED if verified else StartupEntryStatus.ENABLED
            ),
            verified=verified,
            rollback_performed=rolled_back,
            rollback_verified=rollback_verified,
            message=(
                "Startup configuration is absent from its active source; "
                "future launch is not guaranteed"
                if verified
                else "Disable verification failed and exact backup restoration was attempted"
            ),
            started_at=started,
            completed_at=datetime.now(UTC),
        )

    def _load_payload(self, request: StartupActionRequest) -> StartupBackupPayload:
        payload = self._vault.load(request.backup_id, expected_digest=request.backup_digest)
        if payload.original_identity.canonical_digest() != request.identity.canonical_digest():
            raise ValueError("Startup backup identity does not match tool arguments")
        return payload


class RestoreStartupTool:
    """Restore exactly one Agent-disabled startup entry from verified backup."""

    def __init__(
        self,
        platform: StartupManagementPlatform,
        vault: StartupBackupVault,
        repository: StartupActionRepository,
    ) -> None:
        self._platform = platform
        self._vault = vault
        self._repository = repository
        self._manifest = _manifest("startup.restore", "Restore one exact Agent backup")

    @property
    def manifest(self) -> ToolManifest:
        """Return the fixed R2 reversible restore manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Require a disabled index, restore, verify, and reverse failed verification."""
        typed = StartupActionRequest.model_validate(request)
        if typed.action is not StartupActionType.RESTORE:
            raise ValueError("startup.restore accepts only RESTORE")
        if cancellation.is_cancelled:
            raise RuntimeError("Startup restore was cancelled before mutation")
        disabled = self._repository.get_disabled(typed.backup_id)
        if disabled.identity.canonical_digest() != typed.identity.canonical_digest():
            raise ValueError("Disabled startup identity changed")
        payload = self._vault.load(typed.backup_id, expected_digest=typed.backup_digest)
        command = RestoreStartupCommand(self._platform, payload)
        started = datetime.now(UTC)
        command.execute()
        verified = command.verify()
        rolled_back = False
        rollback_verified = False
        if not verified:
            rolled_back = True
            rollback_verified = command.rollback()
        return StartupMutationResult(
            action=typed.action,
            identity_digest=typed.identity.canonical_digest(),
            before_state_digest=typed.expected_state_digest,
            after_status=(
                StartupEntryStatus.ENABLED if verified else StartupEntryStatus.AGENT_DISABLED
            ),
            verified=verified,
            rollback_performed=rolled_back,
            rollback_verified=rollback_verified,
            message=(
                "Startup configuration was restored; future launch is not guaranteed"
                if verified
                else "Restore verification failed and the Agent-disabled state was re-established"
            ),
            started_at=started,
            completed_at=datetime.now(UTC),
        )


def _manifest(name: str, description: str) -> ToolManifest:
    return ToolManifest(
        name=name,
        description=description,
        input_model=StartupActionRequest,
        output_model=StartupMutationResult,
        risk_level=RiskLevel.R2,
        required_permissions=("ordinary-user", "current-user-startup-only"),
        read_only=False,
        idempotent=False,
        supports_cancellation=True,
        rollback_level=RollbackLevel.FULL,
        preconditions=(
            "verified current-user DPAPI backup",
            "two consumed same-action confirmations",
            "execution-time identity and StartupApproved match",
        ),
        postconditions=(
            "configuration state re-read and verified without claiming future launch behaviour",
        ),
        timeout_seconds=30.0,
        max_batch_size=1,
        audit_fields=(
            "transaction_id",
            "action",
            "identity_digest",
            "backup_digest",
            "verified",
        ),
        supported_platforms=("windows",),
        requires_confirmation=True,
        requires_runtime_confirmation=True,
        supports_preview=True,
        irreversible=False,
    )
