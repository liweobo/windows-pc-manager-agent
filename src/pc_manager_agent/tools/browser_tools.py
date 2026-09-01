"""Exact Stage 5C browser tool registry; no generic click, selector, script, or HTTP API."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from pc_manager_agent.browser.adapter import BrowserAdapter
from pc_manager_agent.domain.browser import (
    BrowserActionRequest,
    BrowserActionResult,
    BrowserObservation,
    BrowserSessionDescriptor,
)
from pc_manager_agent.domain.browser_downloads import BrowserWorkerDownload
from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.tools.execution import WriteExecutionGuard
from pc_manager_agent.tools.manifest import CancellationToken, ToolManifest
from pc_manager_agent.tools.registry import ToolRegistry


class BrowserSessionOpenInput(FrozenModel):
    """Input for one empty ephemeral Chromium context."""

    headless: bool = False


class BrowserNavigateInput(FrozenModel):
    """Input for one Main-validated explicit URL."""

    url: str = Field(min_length=1, max_length=4_096)
    allow_insecure_http: bool = False


class BrowserObserveInput(FrozenModel):
    """Identity check for one active session observation."""

    session_id: str = Field(pattern=r"^[a-f0-9-]{36}$")


class BrowserActionInput(FrozenModel):
    """Input carrying only the closed semantic action schema."""

    action: BrowserActionRequest


class BrowserDownloadInput(FrozenModel):
    """Input for one confirmed document and Agent-owned temporary directory."""

    action: BrowserActionRequest
    temporary_directory: Path


def _r0_manifest(
    *,
    name: str,
    description: str,
    input_model: type[BaseModel],
    output_model: type[BaseModel],
) -> ToolManifest:
    return ToolManifest(
        name=name,
        description=description,
        input_model=input_model,
        output_model=output_model,
        risk_level=RiskLevel.R0,
        required_permissions=("standard-user-browser-worker", "public-network-read"),
        read_only=True,
        idempotent=False,
        supports_cancellation=True,
        rollback_level=RollbackLevel.NONE,
        preconditions=("exact browser plan is confirmed", "URL policy passed freshly"),
        postconditions=("no local or remote mutation was authorized",),
        timeout_seconds=60.0,
        max_batch_size=1,
        audit_fields=("origin", "action kind", "reason code", "counts"),
        supported_platforms=("windows",),
        requires_confirmation=True,
    )


class BrowserSessionOpenTool:
    """Open one non-persistent browser context."""

    def __init__(self, adapter: BrowserAdapter) -> None:
        self._adapter = adapter
        self._manifest = _r0_manifest(
            name="browser.session.open",
            description="Open one isolated Chromium session without a persistent profile",
            input_model=BrowserSessionOpenInput,
            output_model=BrowserSessionDescriptor,
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return the fixed R0 session manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Start only if cancellation was not already requested."""
        if not isinstance(request, BrowserSessionOpenInput):
            raise TypeError("BrowserSessionOpenTool received an unexpected input model")
        if cancellation.cancellation_requested():
            raise RuntimeError("BROWSER_ACTION_CANCELLED")
        return self._adapter.start(headless=request.headless)


class BrowserNavigateTool:
    """Navigate to one prevalidated explicit URL."""

    def __init__(self, adapter: BrowserAdapter) -> None:
        self._adapter = adapter
        self._manifest = _r0_manifest(
            name="browser.page.navigate",
            description="Navigate to one exact public HTTPS URL",
            input_model=BrowserNavigateInput,
            output_model=BrowserObservation,
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return the fixed R0 navigation manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Delegate one URL with no shell or raw network fallback."""
        if not isinstance(request, BrowserNavigateInput):
            raise TypeError("BrowserNavigateTool received an unexpected input model")
        if cancellation.cancellation_requested():
            raise RuntimeError("BROWSER_ACTION_CANCELLED")
        return self._adapter.navigate(request.url, allow_http=request.allow_insecure_http)


class BrowserObserveTool:
    """Collect a bounded visible/accessibility page observation."""

    def __init__(self, adapter: BrowserAdapter) -> None:
        self._adapter = adapter
        self._manifest = _r0_manifest(
            name="browser.page.observe",
            description="Read bounded visible text and semantic roles from the active page",
            input_model=BrowserObserveInput,
            output_model=BrowserObservation,
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return the fixed R0 observation manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Observe without accepting a selector or script."""
        if not isinstance(request, BrowserObserveInput):
            raise TypeError("BrowserObserveTool received an unexpected input model")
        if cancellation.cancellation_requested():
            raise RuntimeError("BROWSER_ACTION_CANCELLED")
        observation = self._adapter.observe()
        if str(observation.session_id) != request.session_id:
            raise RuntimeError("BROWSER_SESSION_MISMATCH")
        return observation


class BrowserElementActionTool:
    """Perform one closed semantic action after worker-side fresh resolution."""

    def __init__(self, adapter: BrowserAdapter) -> None:
        self._adapter = adapter
        self._manifest = _r0_manifest(
            name="browser.element.activate",
            description="Perform one allowlisted semantic browser action",
            input_model=BrowserActionInput,
            output_model=BrowserActionResult,
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return the fixed semantic action manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Delegate an already validated action with no generic click primitive."""
        if not isinstance(request, BrowserActionInput):
            raise TypeError("BrowserElementActionTool received an unexpected input model")
        if cancellation.cancellation_requested():
            raise RuntimeError("BROWSER_ACTION_CANCELLED")
        return self._adapter.perform(request.action)


class BrowserDocumentDownloadTool:
    """Fetch one confirmed document into Agent-owned temporary storage."""

    def __init__(self, adapter: BrowserAdapter) -> None:
        self._adapter = adapter
        self._manifest = ToolManifest(
            name="browser.document.download",
            description="Download one exact safe document for local validation",
            input_model=BrowserDownloadInput,
            output_model=BrowserWorkerDownload,
            risk_level=RiskLevel.R1,
            required_permissions=("standard-user-browser-worker", "local-staging-write"),
            read_only=False,
            idempotent=False,
            supports_cancellation=True,
            rollback_level=RollbackLevel.FULL,
            preconditions=(
                "exact R1 Preview and plan are confirmed",
                "href and page generation pass fresh revalidation",
            ),
            postconditions=("one temporary artifact exists for type and magic validation",),
            timeout_seconds=120.0,
            max_batch_size=1,
            audit_fields=("origin", "filename", "size", "sha256", "verification"),
            supported_platforms=("windows",),
            requires_confirmation=True,
            supports_preview=True,
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return the fixed R1 one-document manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Download only through the isolated adapter and only one item."""
        if not isinstance(request, BrowserDownloadInput):
            raise TypeError("BrowserDocumentDownloadTool received an unexpected input model")
        if cancellation.cancellation_requested():
            raise RuntimeError("BROWSER_ACTION_CANCELLED")
        return self._adapter.download(request.action, request.temporary_directory)


def build_browser_registry(
    adapter: BrowserAdapter,
    *,
    write_guard: WriteExecutionGuard | None = None,
) -> ToolRegistry:
    """Build the complete five-tool Stage 5C allow-list."""
    registry = ToolRegistry(write_guard=write_guard)
    for tool in (
        BrowserSessionOpenTool(adapter),
        BrowserNavigateTool(adapter),
        BrowserObserveTool(adapter),
        BrowserElementActionTool(adapter),
        BrowserDocumentDownloadTool(adapter),
    ):
        registry.register(tool)
    return registry
