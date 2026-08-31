"""Provider-neutral optional Office proposals; no tool, path, shell or execution access."""

from typing import Protocol

from pc_manager_agent.office.context import OfficeModelRequest, OfficeModelResult


class OfficeModelProvider(Protocol):
    """Optional capability interface independent of Windows management planning."""

    @property
    def destination(self) -> str:
        """Return the exact provider/endpoint/model disclosure label to bind in consent."""
        ...

    async def propose(self, request: OfficeModelRequest) -> OfficeModelResult:
        """Return untrusted structured intent/quotes; never run it or select output paths."""
        ...
