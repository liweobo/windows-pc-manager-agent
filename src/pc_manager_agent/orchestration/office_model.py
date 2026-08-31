"""Independent expiring external-data consent; read/edit approval never authorizes disclosure."""

from datetime import timedelta
from uuid import UUID, uuid4

from pydantic import Field

from pc_manager_agent.audit.office_documents import OfficeAudit
from pc_manager_agent.confirmation.office_documents import DocumentConfirmations
from pc_manager_agent.domain.office_documents import OfficeError, office_digest
from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.office.context import (
    DocumentContextBuilder,
    OfficeModelProposal,
    OfficeModelRequest,
    OfficeModelResult,
    validate_proposal,
)
from pc_manager_agent.orchestration.office_documents import OfficeDocumentService
from pc_manager_agent.providers.llm.office import OfficeModelProvider


class OfficeDisclosure(FrozenModel):
    """Exact local display of the proposed payload and external destination."""

    disclosure_id: UUID = Field(default_factory=uuid4)
    destination: str
    request: OfficeModelRequest


class OfficeModelService:
    """Offer models only minimal user-selected content and no write capability."""

    def __init__(
        self,
        reads: OfficeDocumentService,
        provider: OfficeModelProvider,
        confirmations: DocumentConfirmations,
        audit: OfficeAudit,
    ) -> None:
        self._reads, self._provider = reads, provider
        self._confirmations, self._audit = confirmations, audit
        self._pending: dict[UUID, tuple[OfficeDisclosure, UUID]] = {}

    def prepare(
        self, document_ids: tuple[UUID, ...], chunk_ids: tuple[str, ...], user_goal: str
    ) -> OfficeDisclosure:
        """Show precisely the selected spans, never automatically send the whole document."""
        if len(self._pending) >= 3:
            raise OfficeError("OFFICE_DISCLOSURE_SESSION_LIMIT")
        request = DocumentContextBuilder(self._reads.limits).select(
            tuple(self._reads.result(item) for item in document_ids), chunk_ids, user_goal
        )
        disclosure = OfficeDisclosure(destination=self._provider.destination, request=request)
        identifier = self._confirmations.request(
            office_digest(disclosure),
            "OFFICE_UPLOAD",
            self._confirmations.now() + timedelta(seconds=60),
        )
        self._pending[disclosure.disclosure_id] = (disclosure, identifier)
        return disclosure

    async def confirm_and_propose(
        self, disclosure_id: UUID, approved: bool
    ) -> OfficeModelProposal | None:
        """Consume one exact decision and call one provider; never dispatch the returned intent."""
        try:
            disclosure, identifier = self._pending.pop(disclosure_id)
        except KeyError as exc:
            raise OfficeError("OFFICE_DISCLOSURE_MISSING") from exc
        if self._provider.destination != disclosure.destination:
            raise OfficeError("OFFICE_PROVIDER_CHANGED")
        # Rebuild the context from the current session, so altered cached data or
        # revoked grants cannot reuse a prior disclosure confirmation.
        for document_id in {chunk.document_id for chunk in disclosure.request.chunks}:
            self._reads.revalidate(document_id)
        current = DocumentContextBuilder(self._reads.limits).select(
            tuple(
                self._reads.result(item)
                for item in dict.fromkeys(chunk.document_id for chunk in disclosure.request.chunks)
            ),
            tuple(chunk.chunk_id for chunk in disclosure.request.chunks),
            disclosure.request.user_goal,
        )
        if current != disclosure.request:
            raise OfficeError("OFFICE_DISCLOSURE_CHANGED")
        binding = office_digest(disclosure)
        self._confirmations.resolve(identifier, binding, "OFFICE_UPLOAD", approved)
        if not approved:
            return None
        self._confirmations.consume(identifier, binding, "OFFICE_UPLOAD")
        self._audit.record(
            "model.disclosure_starting", str(disclosure_id), count=len(current.chunks)
        )
        try:
            result = await self._provider.propose(current)
            result = OfficeModelResult.model_validate_json(result.model_dump_json())
            proposal = result.proposal
            validate_proposal(current, proposal)
        except Exception as exc:
            self._audit.record(
                "model.failed", str(disclosure_id), code="OFFICE_MODEL_REQUEST_FAILED"
            )
            raise OfficeError("OFFICE_MODEL_REQUEST_FAILED") from exc
        self._audit.record(
            "model.proposal_received",
            str(disclosure_id),
            count=len(proposal.quotes),
            provider=result.provider,
            request_id=result.request_id,
        )
        return proposal
