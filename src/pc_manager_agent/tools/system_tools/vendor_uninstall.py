"""The sole interactive Vendor uninstall write tool allowed by Stage 4D2B."""

from __future__ import annotations

from pydantic import BaseModel

from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.vendor_uninstall import VendorUninstallRequest, VendorUninstallResult
from pc_manager_agent.platform_support.vendor_uninstall import VendorUninstallPlatform
from pc_manager_agent.tools.manifest import CancellationToken, ToolManifest


class VendorUninstallTool:
    """Invoke one validated executable identity; raw registry strings are never accepted."""

    def __init__(self, platform: VendorUninstallPlatform) -> None:
        self._platform = platform
        self._manifest = ToolManifest(
            name="software.uninstall.vendor",
            description="Run one trusted current-user interactive Vendor uninstaller",
            input_model=VendorUninstallRequest,
            output_model=VendorUninstallResult,
            risk_level=RiskLevel.R2,
            required_permissions=("current-user-vendor-maintenance",),
            read_only=False,
            idempotent=False,
            supports_cancellation=True,
            rollback_level=RollbackLevel.NONE,
            preconditions=(
                "fresh software, executable, signature, argument, and preflight evidence match",
                "plan and immediate confirmations are consumed exactly once",
                "durable transaction and mandatory pre-start audit exist",
            ),
            postconditions=(
                "process result remains separate from fresh inventory verification",
                "no shell, elevation, process kill, service stop, or residual deletion is invoked",
            ),
            timeout_seconds=3_600.0,
            max_batch_size=1,
            audit_fields=(
                "software and executable identity digests",
                "argument policy digest",
                "process category and exit code",
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
        """Dispatch only a typed, digest-bound action through the shell-free adapter."""
        if not isinstance(request, VendorUninstallRequest):
            raise TypeError("VendorUninstallTool received an unexpected input model")
        process = self._platform.uninstall(request.action, cancellation)
        return VendorUninstallResult(
            transaction_id=request.transaction_id,
            operation_id=request.operation_id,
            identity_digest=request.action.software_identity_hash,
            vendor_identity_digest=request.action.vendor_identity.invariant_digest(),
            process=process,
        )
