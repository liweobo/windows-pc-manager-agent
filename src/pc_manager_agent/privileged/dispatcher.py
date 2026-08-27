"""Strict action-to-handler dispatch for the one-shot elevated Broker."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol

from pc_manager_agent.domain.elevated_broker import BrokerActionEvidence
from pc_manager_agent.domain.privileged_actions import (
    BrokerDecision,
    PrivilegedActionRequest,
    PrivilegedActionType,
)
from pc_manager_agent.domain.risk import RollbackLevel
from pc_manager_agent.privileged.manifests import (
    PrivilegedActionManifest,
    PrivilegedManifestRegistry,
)
from pc_manager_agent.privileged.revalidation import PrivilegedRevalidationError
from pc_manager_agent.tools.manifest import CancellationToken


@dataclass(frozen=True, slots=True)
class FreshPrivilegedEvidence:
    """Common immutable evidence plus handler-owned validated state."""

    action_type: PrivilegedActionType
    target_state_hash: str
    safety_digest: str
    validated: object


@dataclass(frozen=True, slots=True)
class PrivilegedHandlerOutcome:
    """Action-neutral execution/verification result returned to the Broker core."""

    execution_started: bool
    execution_completed: bool
    verified: bool
    uncertain: bool
    pre_state_hash: str
    post_state_hash: str | None
    result_code: str
    message: str
    rollback_level: RollbackLevel
    action_evidence: BrokerActionEvidence | None = None


class PrivilegedActionHandler(Protocol):
    """Narrow typed implementation for one or more explicitly registered actions."""

    @property
    def action_types(self) -> frozenset[PrivilegedActionType]:
        """Return the finite actions implemented by this handler."""
        ...

    def require(self, request: PrivilegedActionRequest) -> FreshPrivilegedEvidence:
        """Rebuild all target, safety, backup, and precondition evidence."""
        ...

    def execute_and_verify(
        self,
        request: PrivilegedActionRequest,
        fresh: FreshPrivilegedEvidence,
        cancellation: CancellationToken,
        on_dispatched: Callable[[], None] | None = None,
    ) -> PrivilegedHandlerOutcome:
        """Dispatch one narrow adapter and return fresh action-specific verification."""
        ...


class PrivilegedActionDispatcher:
    """Default-deny handler table with manifest, Schema, policy, and Preview binding."""

    def __init__(
        self,
        manifests: PrivilegedManifestRegistry,
        handlers: Iterable[PrivilegedActionHandler],
    ) -> None:
        values: dict[PrivilegedActionType, PrivilegedActionHandler] = {}
        for handler in handlers:
            for action_type in handler.action_types:
                manifest = manifests.require(action_type)
                if manifest.handler_key == "":  # pragma: no cover - model construction guard
                    raise ValueError("Privileged handler manifest key is empty")
                if action_type in values:
                    raise ValueError(f"Duplicate privileged handler: {action_type.value}")
                values[action_type] = handler
        self._manifests = manifests
        self._handlers = values

    @property
    def actions(self) -> frozenset[PrivilegedActionType]:
        """Expose only actions having both a manifest and a concrete handler."""
        return frozenset(self._handlers)

    def require(
        self,
        request: PrivilegedActionRequest,
        *,
        preview_target_state_hash: str,
        preview_safety_digest: str,
    ) -> FreshPrivilegedEvidence:
        """Validate routing metadata and require exact fresh Preview evidence."""
        manifest, handler = self._require_route(request)
        fresh = handler.require(request)
        if fresh.action_type is not request.action_type:
            raise PrivilegedRevalidationError(
                BrokerDecision.ACTION_NOT_ALLOWLISTED,
                "Privileged handler returned another action type",
            )
        if (
            fresh.target_state_hash != preview_target_state_hash
            or fresh.safety_digest != preview_safety_digest
        ):
            raise PrivilegedRevalidationError(
                BrokerDecision.TARGET_CHANGED,
                "Fresh target or safety evidence differs from the confirmed Preview",
            )
        if manifest.canonical_digest() != request.manifest_digest:
            raise PrivilegedRevalidationError(
                BrokerDecision.MANIFEST_CHANGED,
                "Privileged action manifest changed",
            )
        return fresh

    def execute_and_verify(
        self,
        request: PrivilegedActionRequest,
        fresh: FreshPrivilegedEvidence,
        cancellation: CancellationToken,
        on_dispatched: Callable[[], None] | None = None,
    ) -> PrivilegedHandlerOutcome:
        """Dispatch to the exact registered handler without argument transformation."""
        _, handler = self._require_route(request)
        return handler.execute_and_verify(request, fresh, cancellation, on_dispatched)

    def manifest(self, action_type: PrivilegedActionType) -> PrivilegedActionManifest:
        """Return one immutable manifest for audit and Main-side verification."""
        if action_type not in self._handlers:
            raise LookupError(f"Privileged action has no handler: {action_type.value}")
        return self._manifests.require(action_type)

    def _require_route(
        self,
        request: PrivilegedActionRequest,
    ) -> tuple[PrivilegedActionManifest, PrivilegedActionHandler]:
        try:
            manifest = self._manifests.require(request.action_type)
            handler = self._handlers[request.action_type]
        except (KeyError, LookupError) as exc:
            raise PrivilegedRevalidationError(
                BrokerDecision.ACTION_NOT_ALLOWLISTED,
                "Privileged action has no concrete registered handler",
            ) from exc
        if request.action_schema_version != manifest.action_schema_version:
            raise PrivilegedRevalidationError(
                BrokerDecision.ACTION_SCHEMA_UNSUPPORTED,
                "Privileged action Schema version is unsupported",
            )
        if request.safety_policy_version != manifest.safety_policy_version:
            raise PrivilegedRevalidationError(
                BrokerDecision.POLICY_VERSION_MISMATCH,
                "Privileged safety policy version changed",
            )
        if request.manifest_digest != manifest.canonical_digest():
            raise PrivilegedRevalidationError(
                BrokerDecision.MANIFEST_CHANGED,
                "Privileged action manifest digest changed",
            )
        return manifest, handler
