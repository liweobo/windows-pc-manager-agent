"""The sole MSIX uninstall write tool allowed by Stage 4D2C2."""

from __future__ import annotations

from pydantic import BaseModel

from pc_manager_agent.domain.msix_uninstall import (
    MsixUninstallRequest,
    MsixUninstallResult,
    NormalizedMsixPackage,
)
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.orchestration.msix_uninstall import (
    MsixResidualAnalyzer,
    MsixUninstallVerifier,
)
from pc_manager_agent.platform_support.msix_packages import MsixPackagePlatform
from pc_manager_agent.tools.manifest import CancellationToken, ToolManifest


class MsixUninstallTool:
    """Remove one validated current-user package using one fixed WinRT option."""

    def __init__(self, platform: MsixPackagePlatform, max_inventory_items: int = 5_000) -> None:
        self._platform = platform
        self._max_items = max_inventory_items
        self._verifier = MsixUninstallVerifier()
        self._residual = MsixResidualAnalyzer()
        self._manifest = ToolManifest(
            name="software.uninstall.msix",
            description="Remove one validated ordinary current-user MSIX package",
            input_model=MsixUninstallRequest,
            output_model=MsixUninstallResult,
            risk_level=RiskLevel.R2,
            required_permissions=("current-user-package-maintenance",),
            read_only=False,
            idempotent=False,
            supports_cancellation=True,
            rollback_level=RollbackLevel.NONE,
            preconditions=(
                "exact package, scope, type, dependency, policy, and preflight evidence match",
                "plan and immediate confirmations are consumed exactly once",
                "no other software uninstall transaction is active",
            ),
            postconditions=(
                "fresh current-user package inventory determines final status",
                "roamable data preservation is requested and no extra data deletion occurs",
            ),
            timeout_seconds=3_600.0,
            max_batch_size=1,
            audit_fields=(
                "package identity digest",
                "dependency, policy, and data-impact digests",
                "deployment category and fresh-inventory verification",
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
        """Dispatch a typed action, refresh inventory, and report rather than delete residuals."""
        if not isinstance(request, MsixUninstallRequest):
            raise TypeError("MsixUninstallTool received an unexpected input model")
        deployment = self._platform.remove_current_user(request.action, cancellation)
        fresh = self._platform.inventory_current_user(self._max_items, CancellationToken())
        original = _package_from_action(request)
        verification = self._verifier.verify(original, fresh, deployment.category)
        return MsixUninstallResult(
            transaction_id=request.transaction_id,
            deployment=deployment,
            verification=verification,
            residual=self._residual.inspect(None),
        )


def _package_from_action(request: MsixUninstallRequest) -> NormalizedMsixPackage:
    """Build the minimal normalized projection needed by the verifier."""
    return NormalizedMsixPackage(
        identity=request.action.identity,
        display_name=request.action.identity.family.name,
    )
