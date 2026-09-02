"""Default-deny Agent role, delegation, and registered-tool proposal policy."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel

from pc_manager_agent.domain.agents import (
    AgentCapabilityManifest,
    AgentRole,
    AgentRuntimeIdentity,
    AgentToolProposal,
)
from pc_manager_agent.domain.context import DataClassification
from pc_manager_agent.tools.registry import ToolRegistry


class AgentCapabilityError(RuntimeError):
    """Raised when an Agent crosses its immutable capability boundary."""


_FILE_READ_TOOLS = (
    "file.scan",
    "file.analyze.large",
    "file.analyze.inactive",
    "file.analyze.duplicates",
)
_SYSTEM_READ_TOOLS = (
    "system.info",
    "system.cpu",
    "system.memory",
    "system.disks",
    "system.processes",
    "system.startup",
    "system.services",
    "system.software",
)
_SOFTWARE_READ_TOOLS = (
    "software.inventory",
    "software.resolve",
    "software.inspect",
    "software.uninstall_capability",
    "software.uninstall_preview",
    "software.residuals.analyze",
    "software.residuals.report",
    "software.residuals.inspect",
)
_OFFICE_READ_TOOLS = ("office.document.read",)
_BROWSER_READ_TOOLS = (
    "browser.session.open",
    "browser.page.navigate",
    "browser.page.observe",
)
_OPTIMIZATION_READ_TOOLS = (
    "optimization.snapshot",
    "optimization.storage.analyze",
    "optimization.cleanup_candidates.analyze",
    "optimization.performance.analyze",
    "optimization.recommendations",
    "optimization.recycle_bin.inspect",
    "optimization.recommendation.inspect",
    "optimization.recommendation.prepare_action",
    "optimization.session.create",
    "optimization.session.refresh",
)


def default_agent_manifests() -> tuple[AgentCapabilityManifest, ...]:
    """Return the complete finite V1 role matrix; omission means deny."""
    ordinary = (DataClassification.PUBLIC, DataClassification.USER_DATA)
    local = (*ordinary, DataClassification.LOCAL_SYSTEM_METADATA)
    domain_roles = (
        AgentRole.FILE,
        AgentRole.SYSTEM,
        AgentRole.SOFTWARE,
        AgentRole.OFFICE,
        AgentRole.BROWSER,
        AgentRole.OPTIMIZATION,
    )
    return (
        AgentCapabilityManifest(
            role=AgentRole.ORCHESTRATOR,
            version="stage5d-v1",
            allowed_input_types=("UserRequest", "DomainReceiptReference"),
            allowed_output_types=("TaskGraph", "TaskOutcome"),
            readable_data=local,
            delegatable_roles=(AgentRole.PLANNER, *domain_roles, AgentRole.VERIFIER),
            memory_scopes=("GLOBAL_PREFERENCE",),
        ),
        AgentCapabilityManifest(
            role=AgentRole.PLANNER,
            version="stage5d-v1",
            model_backed=True,
            allowed_input_types=("AgentPlanningRequest",),
            allowed_output_types=("AgentGraphDraft",),
            readable_data=ordinary,
            memory_scopes=("GLOBAL_PREFERENCE",),
            may_propose_actions=True,
        ),
        AgentCapabilityManifest(
            role=AgentRole.SAFETY_REVIEWER,
            version="stage5d-v1",
            model_backed=True,
            allowed_input_types=("TaskGraph", "TaskGoalBoundary"),
            allowed_output_types=("AgentResult",),
            readable_data=local,
        ),
        AgentCapabilityManifest(
            role=AgentRole.FILE,
            version="stage5d-v1",
            model_backed=True,
            allowed_input_types=("ContextPackage",),
            allowed_output_types=("AgentResult", "DomainPreparationProposal"),
            readable_data=local,
            proposed_tools=_FILE_READ_TOOLS,
            memory_scopes=("GLOBAL_PREFERENCE", "FILE"),
            may_propose_actions=True,
            may_propose_memory=True,
        ),
        AgentCapabilityManifest(
            role=AgentRole.SYSTEM,
            version="stage5d-v1",
            model_backed=True,
            allowed_input_types=("ContextPackage",),
            allowed_output_types=("AgentResult", "DomainPreparationProposal"),
            readable_data=local,
            proposed_tools=_SYSTEM_READ_TOOLS,
            memory_scopes=("GLOBAL_PREFERENCE", "SYSTEM", "UI"),
            may_propose_actions=True,
            may_propose_memory=True,
        ),
        AgentCapabilityManifest(
            role=AgentRole.SOFTWARE,
            version="stage5d-v1",
            model_backed=True,
            allowed_input_types=("ContextPackage",),
            allowed_output_types=("AgentResult", "DomainPreparationProposal"),
            readable_data=local,
            proposed_tools=_SOFTWARE_READ_TOOLS,
            memory_scopes=("GLOBAL_PREFERENCE", "SOFTWARE"),
            may_propose_actions=True,
            may_propose_memory=True,
        ),
        AgentCapabilityManifest(
            role=AgentRole.OFFICE,
            version="stage5d-v1",
            model_backed=True,
            allowed_input_types=("ContextPackage",),
            allowed_output_types=("AgentResult", "DomainPreparationProposal"),
            readable_data=(*local, DataClassification.DOCUMENT_CONTENT),
            proposed_tools=_OFFICE_READ_TOOLS,
            memory_scopes=("GLOBAL_PREFERENCE", "OFFICE"),
            may_propose_actions=True,
            may_propose_memory=True,
        ),
        AgentCapabilityManifest(
            role=AgentRole.BROWSER,
            version="stage5d-v1",
            model_backed=True,
            allowed_input_types=("ContextPackage",),
            allowed_output_types=("AgentResult", "DomainPreparationProposal"),
            readable_data=(*ordinary, DataClassification.WEB_CONTENT),
            proposed_tools=_BROWSER_READ_TOOLS,
            memory_scopes=("GLOBAL_PREFERENCE", "BROWSER"),
            may_propose_actions=True,
            may_propose_memory=True,
        ),
        AgentCapabilityManifest(
            role=AgentRole.OPTIMIZATION,
            version="stage5d-v1",
            model_backed=True,
            allowed_input_types=("ContextPackage",),
            allowed_output_types=("AgentResult", "DomainPreparationProposal"),
            readable_data=local,
            proposed_tools=_OPTIMIZATION_READ_TOOLS,
            memory_scopes=("GLOBAL_PREFERENCE", "SYSTEM"),
            may_propose_actions=True,
            may_propose_memory=True,
        ),
        AgentCapabilityManifest(
            role=AgentRole.VERIFIER,
            version="stage5d-v1",
            allowed_input_types=("DomainEvidenceReference",),
            allowed_output_types=("TaskOutcome",),
            readable_data=local,
        ),
        AgentCapabilityManifest(
            role=AgentRole.MEMORY_MANAGER,
            version="stage5d-v1",
            allowed_input_types=("MemoryCandidate", "MemoryQuery"),
            allowed_output_types=("MemoryEntry", "MemoryContext"),
            readable_data=ordinary,
            memory_scopes=(
                "GLOBAL_PREFERENCE",
                "FILE",
                "OFFICE",
                "VOICE",
                "BROWSER",
                "SYSTEM",
                "SOFTWARE",
                "UI",
            ),
            may_write_memory=True,
        ),
        AgentCapabilityManifest(
            role=AgentRole.AUDIT_MANAGER,
            version="stage5d-v1",
            allowed_input_types=("AuditMetadata",),
            allowed_output_types=("AuditReceipt",),
            readable_data=(DataClassification.PUBLIC, DataClassification.LOCAL_SYSTEM_METADATA),
        ),
    )


class AgentCapabilityRegistry:
    """Sealed role registry that rejects replacement and unknown roles."""

    def __init__(self) -> None:
        self._manifests: dict[AgentRole, AgentCapabilityManifest] = {}
        self._sealed = False

    def register(self, manifest: AgentCapabilityManifest) -> None:
        """Register one role before sealing, without replacement."""
        if self._sealed:
            raise AgentCapabilityError("Agent capability registry is sealed")
        if manifest.role in self._manifests:
            raise AgentCapabilityError(f"Duplicate Agent role: {manifest.role.value}")
        self._manifests[manifest.role] = manifest

    def seal(self) -> None:
        """Require the complete V1 role set and prevent later mutation."""
        missing = set(AgentRole) - set(self._manifests)
        if missing:
            names = ", ".join(sorted(role.value for role in missing))
            raise AgentCapabilityError(f"Missing Agent manifests: {names}")
        self._sealed = True

    def manifest(self, role: AgentRole) -> AgentCapabilityManifest:
        """Return one known role only after the matrix has been sealed."""
        if not self._sealed:
            raise AgentCapabilityError("Agent capability registry is not sealed")
        try:
            return self._manifests[role]
        except KeyError as exc:
            raise AgentCapabilityError(f"Unknown Agent role: {role.value}") from exc

    @property
    def roles(self) -> tuple[AgentRole, ...]:
        """Return roles in deterministic order."""
        return tuple(sorted(self._manifests, key=lambda role: role.value))


def build_agent_capability_registry() -> AgentCapabilityRegistry:
    """Build and seal the complete default-deny V1 matrix."""
    registry = AgentCapabilityRegistry()
    for manifest in default_agent_manifests():
        registry.register(manifest)
    registry.seal()
    return registry


class AgentToolAccessPolicy:
    """Validate an R0 proposal without exposing the registry's execute method."""

    def __init__(self, capabilities: AgentCapabilityRegistry) -> None:
        self._capabilities = capabilities

    def validate(
        self,
        identity: AgentRuntimeIdentity,
        proposal: AgentToolProposal,
        registry: ToolRegistry,
    ) -> BaseModel:
        """Check identity, role allow-list, registered manifest, and input schema."""
        manifest = self._capabilities.manifest(identity.role)
        if identity.manifest_digest != manifest.canonical_digest():
            raise AgentCapabilityError("Agent manifest identity mismatch")
        if proposal.tool_name not in manifest.proposed_tools:
            raise AgentCapabilityError("Agent is not allowed to propose this tool")
        tool_manifest = registry.manifest(proposal.tool_name)
        if not tool_manifest.read_only:
            raise AgentCapabilityError("Agents may only propose registered read-only tools")
        return registry.validate_input(proposal.tool_name, proposal.arguments)


class AgentDelegationPolicy:
    """Compute capability subsets without trusting a child's requested role text."""

    def __init__(self, capabilities: AgentCapabilityRegistry) -> None:
        self._capabilities = capabilities

    def narrow(
        self,
        parent: AgentRuntimeIdentity,
        target_role: AgentRole,
        requested: tuple[str, ...],
    ) -> tuple[str, ...]:
        """Return an exact subset or reject any escalation attempt."""
        parent_manifest = self._capabilities.manifest(parent.role)
        if parent.manifest_digest != parent_manifest.canonical_digest():
            raise AgentCapabilityError("Parent Agent identity is stale or forged")
        if target_role not in parent_manifest.delegatable_roles:
            raise AgentCapabilityError("Parent Agent cannot delegate to the target role")
        target = self._capabilities.manifest(target_role)
        allowed = set(target.proposed_tools)
        if not set(requested).issubset(allowed):
            raise AgentCapabilityError("CAPABILITY_ESCALATION_REJECTED")
        return tuple(sorted(requested))


def manifest_mapping(
    registry: AgentCapabilityRegistry,
) -> Mapping[AgentRole, AgentCapabilityManifest]:
    """Expose a read-only snapshot suitable for tests and UI summaries."""
    return {role: registry.manifest(role) for role in registry.roles}
