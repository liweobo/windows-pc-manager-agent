"""The sole winget uninstall write tool allowed by Stage 4D2C1."""

from __future__ import annotations

from pydantic import BaseModel

from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.winget_uninstall import (
    WingetUninstallRequest,
    WingetUninstallResult,
)
from pc_manager_agent.platform_support.winget_uninstall import WingetUninstallPlatform
from pc_manager_agent.tools.manifest import CancellationToken, ToolManifest


class WingetUninstallTool:
    """Invoke one validated Package ID with fixed flags and a trusted executable identity."""

    def __init__(self, platform: WingetUninstallPlatform) -> None:
        self._platform = platform
        self._manifest = ToolManifest(
            name="software.uninstall.winget",
            description="Remove one high-confidence current-user package through trusted winget",
            input_model=WingetUninstallRequest,
            output_model=WingetUninstallResult,
            risk_level=RiskLevel.R2,
            required_permissions=("current-user-package-maintenance",),
            read_only=False,
            idempotent=False,
            supports_cancellation=True,
            rollback_level=RollbackLevel.NONE,
            preconditions=(
                "fresh package, software, mapping, executable, and preflight evidence match",
                "plan and immediate confirmations are consumed exactly once",
                "durable transaction and mandatory pre-start audit exist",
            ),
            postconditions=(
                "package-manager exit remains separate from dual inventory verification",
                "no shell, elevation, process control, service control, restart, "
                "or deletion occurs",
            ),
            timeout_seconds=3_600.0,
            max_batch_size=1,
            audit_fields=(
                "package and software identity digests",
                "executable and fixed-argument policy digests",
                "process category and dual verification state",
            ),
            supported_platforms=("windows",),
            requires_confirmation=True,
            requires_runtime_confirmation=True,
            supports_preview=True,
            irreversible=True,
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return the fixed irreversible R2 manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Dispatch only the typed action; no command string or optional flag is accepted."""
        if not isinstance(request, WingetUninstallRequest):
            raise TypeError("WingetUninstallTool received an unexpected input model")
        process = self._platform.uninstall(request.action, cancellation)
        return WingetUninstallResult(
            transaction_id=request.transaction_id,
            operation_id=request.operation_id,
            package_identity_digest=request.action.package_identity.canonical_digest(),
            software_identity_digest=request.action.software_identity_digest,
            process=process,
        )
