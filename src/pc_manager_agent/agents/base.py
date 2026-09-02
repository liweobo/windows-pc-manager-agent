"""Runtime-owned Agent identity construction and proposal-only protocol."""

from __future__ import annotations

from typing import Protocol

from pc_manager_agent.domain.agents import (
    AgentCapabilityManifest,
    AgentResult,
    AgentRole,
    AgentRuntimeIdentity,
)
from pc_manager_agent.domain.context import ContextPackage
from pc_manager_agent.safety.agent_capabilities import AgentCapabilityRegistry


class ProposalAgent(Protocol):
    """An Agent may return structured proposals but cannot execute them."""

    @property
    def identity(self) -> AgentRuntimeIdentity:
        """Return the runtime-assigned, short-lived identity."""
        ...

    async def run(self, context: ContextPackage) -> AgentResult:
        """Return an untrusted structured result without authorization."""
        ...


class AgentIdentityFactory:
    """Create identities from sealed manifests rather than model-supplied roles."""

    def __init__(self, capabilities: AgentCapabilityRegistry) -> None:
        self._capabilities = capabilities

    def create(self, role: AgentRole) -> AgentRuntimeIdentity:
        """Bind one short-lived instance to the exact current manifest."""
        manifest = self._capabilities.manifest(role)
        return AgentRuntimeIdentity(
            role=role,
            manifest_digest=manifest.canonical_digest(),
            prompt_version=f"{role.value.casefold()}-{manifest.version}",
        )

    def validate(self, identity: AgentRuntimeIdentity) -> AgentCapabilityManifest:
        """Return the bound manifest or reject a stale or forged runtime identity."""
        manifest = self._capabilities.manifest(identity.role)
        if identity.manifest_digest != manifest.canonical_digest():
            raise ValueError("Agent runtime identity does not match its manifest")
        if identity.prompt_version != f"{identity.role.value.casefold()}-{manifest.version}":
            raise ValueError("Agent runtime prompt identity does not match its manifest")
        return manifest
