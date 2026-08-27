"""Fail-closed availability checks for the independent Windows elevated Broker."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pc_manager_agent.domain.elevated_broker import (
    BrokerBinaryIdentity,
    BrokerTrustMode,
    WindowsProcessIdentity,
)
from pc_manager_agent.platform_support.privileged_broker import BrokerBinaryInspector
from pc_manager_agent.privileged.broker_identity import BrokerTrustError, BrokerTrustPolicy


class BrokerAvailabilityStatus(StrEnum):
    """Stable reasons why real UAC execution is ready or unavailable."""

    READY = "READY"
    DISABLED = "DISABLED"
    UNSUPPORTED_PLATFORM = "UNSUPPORTED_PLATFORM"
    MAIN_AGENT_ELEVATED = "MAIN_AGENT_ELEVATED"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    BINARY_UNAVAILABLE = "BINARY_UNAVAILABLE"
    BINARY_UNTRUSTED = "BINARY_UNTRUSTED"


@dataclass(frozen=True, slots=True)
class BrokerAvailability:
    """Read-only Broker readiness conclusion with optional trusted identity evidence."""

    status: BrokerAvailabilityStatus
    message: str
    binary: BrokerBinaryIdentity | None = None

    @property
    def ready(self) -> bool:
        """Return whether a confirmed action may proceed to the UAC launcher."""
        return self.status is BrokerAvailabilityStatus.READY and self.binary is not None


class PrivilegedBrokerAvailabilityService:
    """Check platform, main token, fixed path, hash, manifest, and trust before use."""

    def __init__(
        self,
        inspector: BrokerBinaryInspector,
        *,
        enabled: bool,
        broker_path: Path | None,
        expected_sha256: str | None,
        trust_mode: BrokerTrustMode,
        caller_identity: WindowsProcessIdentity,
    ) -> None:
        self._inspector = inspector
        self._enabled = enabled
        self._path = broker_path
        self._expected_sha256 = expected_sha256
        self._trust_mode = trust_mode
        self._caller = caller_identity

    def inspect(self) -> BrokerAvailability:
        """Return a truthful readiness result without launching or elevating anything."""
        if not self._enabled:
            return BrokerAvailability(
                BrokerAvailabilityStatus.DISABLED, "Windows Broker is disabled"
            )
        if sys.platform != "win32":
            return BrokerAvailability(
                BrokerAvailabilityStatus.UNSUPPORTED_PLATFORM,
                "The real elevated Broker is available only on Windows",
            )
        if self._caller.elevated:
            return BrokerAvailability(
                BrokerAvailabilityStatus.MAIN_AGENT_ELEVATED,
                "The main Agent is already elevated and must stop this route",
            )
        if self._path is None or self._expected_sha256 is None or not self._path.is_absolute():
            return BrokerAvailability(
                BrokerAvailabilityStatus.NOT_CONFIGURED,
                "A fixed absolute Broker path and expected SHA-256 are required",
            )
        try:
            binary = self._inspector.inspect(self._path)
            BrokerTrustPolicy(self._trust_mode, self._expected_sha256).require_binary(binary)
        except (BrokerTrustError, OSError, RuntimeError, ValueError):
            return BrokerAvailability(
                BrokerAvailabilityStatus.BINARY_UNTRUSTED,
                "Broker identity, manifest, signing, location, or SHA-256 trust failed",
            )
        return BrokerAvailability(
            BrokerAvailabilityStatus.READY,
            "The one-shot Broker passed pre-UAC availability checks",
            binary,
        )

    def require_ready(self) -> BrokerBinaryIdentity:
        """Return trusted evidence or fail before any UAC prompt can appear."""
        availability = self.inspect()
        if not availability.ready or availability.binary is None:
            raise BrokerTrustError(availability.message)
        return availability.binary
