"""Office aggregate-only audit: values, paths, Diff and provider payloads are excluded."""

from pydantic import JsonValue

from pc_manager_agent import __version__
from pc_manager_agent.audit.models import AuditEvent
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.domain.office_documents import office_digest
from pc_manager_agent.domain.office_transactions import OfficeTransaction
from pc_manager_agent.domain.risk import RiskLevel


class OfficeAudit:
    """Allow only finite metadata fields; never serialize a DocumentEditPlan wholesale."""

    def __init__(self, repository: AuditRepository, git_commit: str | None = None) -> None:
        self._repository = repository
        self._git_commit = git_commit

    def record(
        self,
        event: str,
        reference: str,
        *,
        risk: RiskLevel = RiskLevel.R0,
        count: int = 0,
        size: int = 0,
        code: str | None = None,
        provider: str | None = None,
        request_id: str | None = None,
        transaction: OfficeTransaction | None = None,
        duration_ms: int | None = None,
        tool_name: str | None = None,
    ) -> None:
        """Write mandatory identifiers/counts and an optional stable error code only."""
        if code is not None and (len(code) > 100 or not code.replace("_", "").isalnum()):
            code = "OFFICE_OPERATION_FAILED"
        parameters: dict[str, JsonValue] = {"reference": reference, "count": count, "bytes": size}
        if transaction is not None:
            parameters.update(
                {
                    "transaction_id": str(transaction.transaction_id),
                    "mode": transaction.mode.value,
                    "format": transaction.format.value if transaction.format else None,
                    "operation_types": [kind.value for kind in transaction.operation_kinds],
                    "operation_count": len(transaction.operation_kinds),
                    "input_identity_digests": list(transaction.input_identity_digests),
                    "output_identity_digest": office_digest(transaction.result)
                    if transaction.result
                    else None,
                    "preview_id": str(transaction.preview_id),
                    "preview_digest": transaction.preview_digest,
                    "confirmation_ids": [
                        str(identifier) for identifier in transaction.confirmation_ids
                    ],
                    "backup_id": str(transaction.backup_id) if transaction.backup_id else None,
                    "recovery_of": str(transaction.recovery_of)
                    if transaction.recovery_of
                    else None,
                    "state": transaction.state.value,
                }
            )
        self._repository.record(
            AuditEvent(
                event_type=f"office.{event}",
                app_version=__version__,
                git_commit=self._git_commit,
                risk_level=risk,
                model_provider=provider,
                model_request_id=request_id,
                plan_id=str(transaction.plan_id) if transaction else None,
                tool_name=tool_name,
                duration_ms=duration_ms,
                parameters=parameters,
                error={"code": code} if code else None,
            )
        )
