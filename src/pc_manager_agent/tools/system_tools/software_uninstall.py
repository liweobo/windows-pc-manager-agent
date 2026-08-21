"""The sole irreversible write tool allowed by Stage 4D2A."""

from __future__ import annotations

from pydantic import BaseModel

from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.software_uninstall_execution import (
    MsiUninstallRequest,
    MsiUninstallResult,
)
from pc_manager_agent.platform_support.msi_uninstall import MsiUninstallPlatform
from pc_manager_agent.tools.manifest import CancellationToken, ToolManifest


class MsiUninstallTool:
    """Invoke one validated MSI product; no raw executable or argument field exists."""

    def __init__(self, platform: MsiUninstallPlatform) -> None:
        self._platform = platform
        self._manifest = ToolManifest(
            name="software.uninstall.msi",
            description="Uninstall one validated current-user MSI product",
            input_model=MsiUninstallRequest,
            output_model=MsiUninstallResult,
            risk_level=RiskLevel.R2,
            required_permissions=("current-user-msi-maintenance",),
            read_only=False,
            idempotent=False,
            supports_cancellation=True,
            rollback_level=RollbackLevel.NONE,
            preconditions=(
                "fresh software identity and ProductCode registration are unchanged",
                "execution policy and process/service preflight allow removal",
                "plan and immediate confirmations are consumed exactly once",
                "durable transaction and audit start records exist",
            ),
            postconditions=(
                "installer result is recorded separately from fresh inventory verification",
                "no process kill, service stop, elevation, reboot, or residual deletion is invoked",
            ),
            timeout_seconds=3_600.0,
            max_batch_size=1,
            audit_fields=(
                "identity digest",
                "ProductCode digest",
                "installer category and exit code",
                "verification state",
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
        """Dispatch only the typed ProductCode through the fixed platform adapter."""
        if not isinstance(request, MsiUninstallRequest):
            raise TypeError("MsiUninstallTool received an unexpected input model")
        installer = self._platform.uninstall(request.product, cancellation)
        return MsiUninstallResult(
            transaction_id=request.transaction_id,
            operation_id=request.operation_id,
            identity_digest=request.product.identity_digest,
            product_code_digest=request.product.product_code_digest,
            installer=installer,
        )
