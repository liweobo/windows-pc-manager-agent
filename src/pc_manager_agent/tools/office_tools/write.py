"""Reference-only Office writer registration with durable single-use dispatch authority."""

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pydantic import BaseModel, JsonValue

from pc_manager_agent.domain.office_documents import OfficeError
from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.persistence.office_documents import OfficeRepository
from pc_manager_agent.tools.execution import ExecutionAuthorization, arguments_digest
from pc_manager_agent.tools.manifest import CancellationToken, ToolManifest


class OfficeWriteRequest(FrozenModel):
    """Opaque reference to a fully prepared local operation; no path or edit body."""

    reference_id: UUID


class OfficeWriteResult(FrozenModel):
    """The actual durable result reference; success still requires domain verification."""

    result_id: UUID


def capability_digest(authorization: ExecutionAuthorization) -> str:
    """Bind the dispatch to transaction, plan, preview, tool and exact arguments."""
    return arguments_digest(
        authorization.model_dump(
            mode="json",
            exclude={"operation_id", "runtime_confirmation_id"},
        )
    )


class OfficeWriteGuard:
    """Consume one approved internal dispatch, created only after domain user confirmations."""

    def __init__(self, repository: OfficeRepository) -> None:
        self._repository = repository

    def issue(
        self, reference_id: UUID, plan_id: UUID, preview_id: UUID, tool_name: str
    ) -> ExecutionAuthorization:
        """Reserve one exact dispatch after the owning service consumes its user approvals."""
        now = datetime.now(UTC)
        request = OfficeWriteRequest(reference_id=reference_id)
        authorization = ExecutionAuthorization(
            transaction_id=reference_id,
            operation_id=uuid4(),
            plan_id=plan_id,
            preview_id=preview_id,
            tool_name=tool_name,
            arguments_digest=arguments_digest(request.model_dump(mode="json")),
        )
        binding = capability_digest(authorization)
        identifier = self._repository.request(binding, "DISPATCH", now + timedelta(seconds=30))
        self._repository.resolve(identifier, binding, "DISPATCH", True, now)
        return authorization.model_copy(update={"operation_id": identifier})

    def require(
        self,
        authorization: ExecutionAuthorization,
        tool_name: str,
        arguments: Mapping[str, JsonValue],
    ) -> None:
        """Reject arbitrary caller parameters and atomically consume one reserved call."""
        if (
            tool_name != authorization.tool_name
            or arguments_digest(arguments) != authorization.arguments_digest
        ):
            raise OfficeError("OFFICE_DISPATCH_CHANGED")
        self._repository.consume(
            authorization.operation_id,
            capability_digest(authorization),
            "DISPATCH",
            datetime.now(UTC),
        )


class OfficeWriteTool:
    """One fixed operation with exact risk and a deterministic injected implementation."""

    def __init__(
        self, name: str, risk: RiskLevel, handler: Callable[[UUID, CancellationToken], UUID]
    ) -> None:
        if name not in {
            "office.document.backup",
            "office.document.create",
            "office.document.apply_edit",
            "office.document.restore",
            "office.document.undo_created",
        }:
            raise OfficeError("OFFICE_TOOL_NOT_ALLOWLISTED")
        allowed = (
            {RiskLevel.R2, RiskLevel.R2_HIGH_IMPACT}
            if name in {"office.document.apply_edit", "office.document.restore"}
            else {RiskLevel.R1}
        )
        if risk not in allowed:
            raise OfficeError("OFFICE_TOOL_RISK_MISMATCH")
        self._handler = handler
        self._manifest = ToolManifest(
            name=name,
            description="Execute one exact confirmed Office operation",
            input_model=OfficeWriteRequest,
            output_model=OfficeWriteResult,
            risk_level=risk,
            read_only=False,
            idempotent=False,
            supports_cancellation=True,
            required_permissions=("standard_user", "exact_document_scope"),
            rollback_level=RollbackLevel.FULL,
            supports_preview=True,
            requires_runtime_confirmation=risk in {RiskLevel.R2, RiskLevel.R2_HIGH_IMPACT},
            preconditions=("durable single-use dispatch", "fresh identity", "exact output"),
            postconditions=("verified result and recovery record",),
            timeout_seconds=60,
            max_batch_size=1,
            audit_fields=("reference_id",),
            supported_platforms=("windows",),
            allowed_risk_levels=(RiskLevel.R2, RiskLevel.R2_HIGH_IMPACT)
            if risk is RiskLevel.R2_HIGH_IMPACT
            else (),
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return this fixed writer's manifest; no generic command dispatch exists."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> OfficeWriteResult:
        """Invoke one prepared operation after the registry consumes the dispatch capability."""
        if not isinstance(request, OfficeWriteRequest):
            raise TypeError("OfficeWriteRequest required")
        return OfficeWriteResult(result_id=self._handler(request.reference_id, cancellation))
