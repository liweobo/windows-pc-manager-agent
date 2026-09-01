"""Optional provider-neutral summarization for explicitly consented web content."""

from typing import Protocol

from pc_manager_agent.domain.browser import BrowserModelSummary, BrowserModelSummaryRequest


class BrowserContentProvider(Protocol):
    """Summarize minimized untrusted page data; never propose or authorize actions."""

    @property
    def destination(self) -> str:
        """Return the exact provider, endpoint, and model disclosure label."""

    async def summarize(self, request: BrowserModelSummaryRequest) -> BrowserModelSummary:
        """Return an advisory summary after the caller independently obtained consent."""
