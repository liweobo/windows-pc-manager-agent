"""Deterministic Broker binary and endpoint identity policy for Stage 4X2."""

from __future__ import annotations

from dataclasses import dataclass

from pc_manager_agent.domain.elevated_broker import (
    BrokerBinaryIdentity,
    BrokerTrustMode,
    SignatureStatus,
    WindowsProcessIdentity,
)


class BrokerTrustError(RuntimeError):
    """Raised when binary or operating-system endpoint evidence fails closed."""


@dataclass(frozen=True, slots=True)
class BrokerTrustPolicy:
    """Evaluate production signing or explicit development hash pinning."""

    mode: BrokerTrustMode
    expected_sha256: str
    expected_signer_fingerprint: str | None = None

    def require_binary(self, identity: BrokerBinaryIdentity) -> None:
        """Require an ordinary asInvoker EXE matching the configured trust policy."""
        if identity.reparse_point or identity.sha256 != self.expected_sha256:
            raise BrokerTrustError("Broker file identity or SHA-256 differs from configuration")
        if identity.manifest_execution_level != "asInvoker":
            raise BrokerTrustError("Broker manifest does not retain asInvoker")
        if self.mode is BrokerTrustMode.PRODUCTION and (
            not identity.trusted_location
            or identity.signature_status is not SignatureStatus.VALID
            or self.expected_signer_fingerprint is None
            or identity.signer_fingerprint != self.expected_signer_fingerprint
        ):
            raise BrokerTrustError("Production Broker signing or install trust is unavailable")

    @staticmethod
    def require_pipe_peers(
        *,
        caller: WindowsProcessIdentity,
        expected_caller: WindowsProcessIdentity,
        broker: WindowsProcessIdentity,
        expected_broker_binary: BrokerBinaryIdentity,
    ) -> None:
        """Bind the real OS pipe peers to same-account, same-session expected processes."""
        if caller.canonical_digest() != expected_caller.canonical_digest():
            raise BrokerTrustError("Named-pipe caller process identity changed")
        if caller.elevated:
            raise BrokerTrustError("The main Agent must remain a standard-user process")
        if not broker.elevated or broker.integrity_level not in {"HIGH", "SYSTEM"}:
            raise BrokerTrustError("Broker process is not elevated")
        if caller.user_sid != broker.user_sid:
            raise BrokerTrustError("V1 rejects elevation through a different account")
        if caller.session_id != broker.session_id:
            raise BrokerTrustError("Broker and Agent are in different Windows sessions")
        if broker.image_sha256 != expected_broker_binary.sha256:
            raise BrokerTrustError("Running Broker image differs from pre-UAC identity")
