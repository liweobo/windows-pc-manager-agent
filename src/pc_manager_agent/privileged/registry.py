"""Privileged action allow-list kept separate from the ordinary Tool Registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel

from pc_manager_agent.domain.privileged_actions import (
    PrivilegedActionRequest,
    PrivilegedActionType,
    PrivilegeRequirement,
    ServiceStartPayload,
    ServiceStopPayload,
)
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.privileged.revalidation import (
    FakePrivilegedService,
    ServicePrivilegedRevalidator,
)


class PrivilegedActionHandler(Protocol):
    """Action-specific Mock revalidation, execution, and verification contract."""

    def require(self, request: PrivilegedActionRequest) -> FakePrivilegedService:
        """Perform fresh deterministic precondition checks."""
        ...

    def execute(self, request: PrivilegedActionRequest) -> bool:
        """Apply one finite operation only to fake system state."""
        ...

    def verify(self, request: PrivilegedActionRequest) -> FakePrivilegedService | None:
        """Return fresh state only when the exact postcondition holds."""
        ...


@dataclass(frozen=True, slots=True)
class PrivilegedActionManifest:
    """Immutable security metadata for one Mock-executable protocol action."""

    action_type: PrivilegedActionType
    payload_model: type[BaseModel]
    risk_floor: RiskLevel
    required_privilege: PrivilegeRequirement
    handler: PrivilegedActionHandler
    audit_policy: str


class PrivilegedActionRegistryError(RuntimeError):
    """Raised for duplicate or non-allowlisted privileged actions."""


class PrivilegedActionRegistry:
    """Finite internal allow-list; it is never exposed as an LLM tool."""

    def __init__(self) -> None:
        self._manifests: dict[PrivilegedActionType, PrivilegedActionManifest] = {}

    def register(self, manifest: PrivilegedActionManifest) -> None:
        """Register one explicit action and reject collisions or weakened metadata."""
        if manifest.action_type in self._manifests:
            raise PrivilegedActionRegistryError(
                f"Privileged action already registered: {manifest.action_type.value}"
            )
        if manifest.risk_floor is not RiskLevel.R3:
            raise PrivilegedActionRegistryError("Stage 4X1 Mock actions retain R3 semantics")
        if manifest.required_privilege is not PrivilegeRequirement.ELEVATED_ADMIN_REQUIRED:
            raise PrivilegedActionRegistryError(
                "Stage 4X1 only represents explicit Administrator-required actions"
            )
        self._manifests[manifest.action_type] = manifest

    def require(self, action_type: PrivilegedActionType) -> PrivilegedActionManifest:
        """Return one registered manifest or fail without a generic fallback."""
        try:
            return self._manifests[action_type]
        except KeyError as exc:
            raise PrivilegedActionRegistryError(
                f"Privileged action is not allowlisted: {action_type.value}"
            ) from exc

    @property
    def action_types(self) -> tuple[PrivilegedActionType, ...]:
        """Return the deterministic Mock-executable action set."""
        return tuple(sorted(self._manifests, key=lambda item: item.value))


def build_stage4x1_registry(
    handler: ServicePrivilegedRevalidator,
) -> PrivilegedActionRegistry:
    """Build the only Stage 4X1 allow-list: synthetic service Start and Stop."""
    registry = PrivilegedActionRegistry()
    registry.register(
        PrivilegedActionManifest(
            action_type=PrivilegedActionType.SERVICE_START,
            payload_model=ServiceStartPayload,
            risk_floor=RiskLevel.R3,
            required_privilege=PrivilegeRequirement.ELEVATED_ADMIN_REQUIRED,
            handler=handler,
            audit_policy="DIGESTS_ONLY",
        )
    )
    registry.register(
        PrivilegedActionManifest(
            action_type=PrivilegedActionType.SERVICE_STOP,
            payload_model=ServiceStopPayload,
            risk_floor=RiskLevel.R3,
            required_privilege=PrivilegeRequirement.ELEVATED_ADMIN_REQUIRED,
            handler=handler,
            audit_policy="DIGESTS_ONLY",
        )
    )
    return registry
