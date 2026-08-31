"""Exact-reference R0 read tool; the owning service supplies confirmed scope."""

from collections.abc import Callable
from uuid import UUID

from pydantic import BaseModel

from pc_manager_agent.domain.office_documents import DocumentReference, StructuredDocument
from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.tools.manifest import CancellationToken, ToolManifest


class OfficeReadRequest(FrozenModel):
    """Opaque plan and grant IDs; no user/model path or executable parameters."""

    plan_id: UUID
    grant_id: UUID


class OfficeReadResult(FrozenModel):
    """Authorized local parsed content; never automatically forwarded to a provider."""

    reference: DocumentReference
    document: StructuredDocument


class OfficeReadTool:
    """Read one confirmed exact input through the dedicated document service."""

    def __init__(
        self, reader: Callable[[OfficeReadRequest, CancellationToken], OfficeReadResult]
    ) -> None:
        self._reader = reader
        self._manifest = ToolManifest(
            name="office.document.read",
            description="Read one explicitly confirmed local document",
            input_model=OfficeReadRequest,
            output_model=OfficeReadResult,
            risk_level=RiskLevel.R0,
            required_permissions=("current_user_selected_file",),
            read_only=True,
            idempotent=True,
            supports_cancellation=True,
            rollback_level=RollbackLevel.NONE,
            preconditions=("exact confirmed R0 plan", "current file grant", "safe stable handle"),
            postconditions=("bounded structured content", "content identity verified"),
            timeout_seconds=60,
            max_batch_size=1,
            audit_fields=("plan_id", "grant_id", "format"),
            supported_platforms=("windows",),
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return the fixed R0 manifest, isolated from system-management registries."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> OfficeReadResult:
        """Require the service's active confirmed read scope even for direct registry calls."""
        if not isinstance(request, OfficeReadRequest):
            raise TypeError("OfficeReadRequest required")
        return self._reader(request, cancellation)
