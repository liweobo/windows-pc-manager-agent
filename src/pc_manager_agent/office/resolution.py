"""Resolve only existing explicit document references; never search or widen filesystem scope."""

from uuid import UUID

from pc_manager_agent.domain.office_documents import DocumentReference, OfficeError
from pc_manager_agent.tools.office_tools.read import OfficeReadResult


class DocumentTargetResolver:
    """Names discover candidates only; ambiguity requires a user's exact UUID selection."""

    def candidates(
        self, name: str, available: tuple[OfficeReadResult, ...]
    ) -> tuple[DocumentReference, ...]:
        """Return exact-basename matches among already authorized/read session documents."""
        if not name or any(character in name for character in "/\\:*?\x00") or name in {".", ".."}:
            raise OfficeError("DOCUMENT_NAME_QUERY_INVALID")
        return tuple(
            item.reference
            for item in available
            if item.reference.identity.state.path.name.casefold() == name.casefold()
        )

    def resolve(
        self, document_id: UUID, available: tuple[OfficeReadResult, ...]
    ) -> DocumentReference:
        """Resolve one opaque user-selected identity; zero or multiple matches fail closed."""
        matches = tuple(
            item.reference for item in available if item.reference.document_id == document_id
        )
        if len(matches) != 1:
            raise OfficeError("DOCUMENT_TARGET_AMBIGUOUS_OR_MISSING")
        return matches[0]
