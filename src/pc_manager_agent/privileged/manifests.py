"""Immutable allowlist metadata for every real elevated Broker action."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from pc_manager_agent.domain.privileged_actions import (
    MACHINE_MSI_POLICY_VERSION,
    MACHINE_MSI_SCHEMA_VERSION,
    MACHINE_STARTUP_POLICY_VERSION,
    MACHINE_STARTUP_SCHEMA_VERSION,
    SERVICE_CONTROL_POLICY_VERSION,
    SERVICE_CONTROL_SCHEMA_VERSION,
    SERVICE_STARTUP_POLICY_VERSION,
    SERVICE_STARTUP_SCHEMA_VERSION,
    PrivilegedActionType,
    canonical_model_digest,
)
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel


@dataclass(frozen=True, slots=True)
class PrivilegedActionManifest:
    """Static security contract used by Main and Broker before handler dispatch."""

    action_type: PrivilegedActionType
    action_schema_version: int
    safety_policy_version: str
    risk_floor: RiskLevel
    rollback_level: RollbackLevel
    handler_key: str
    maximum_runtime_seconds: int
    required_integrity_level: str = "HIGH"

    def canonical_digest(self) -> str:
        """Bind action routing to every security-relevant manifest field."""
        return canonical_model_digest(
            {
                "action_type": self.action_type.value,
                "action_schema_version": self.action_schema_version,
                "safety_policy_version": self.safety_policy_version,
                "risk_floor": self.risk_floor.value,
                "rollback_level": self.rollback_level.value,
                "handler_key": self.handler_key,
                "maximum_runtime_seconds": self.maximum_runtime_seconds,
                "required_integrity_level": self.required_integrity_level,
            }
        )


class PrivilegedManifestRegistry:
    """Default-deny map from a finite action enum to one immutable manifest."""

    def __init__(self, manifests: Iterable[PrivilegedActionManifest]) -> None:
        values: dict[PrivilegedActionType, PrivilegedActionManifest] = {}
        for manifest in manifests:
            if manifest.action_type in values:
                raise ValueError(f"Duplicate privileged manifest: {manifest.action_type.value}")
            if manifest.risk_floor is not RiskLevel.R3:
                raise ValueError("Real elevated action manifests must retain an R3 floor")
            if manifest.required_integrity_level != "HIGH":
                raise ValueError("Stage 4X3 never registers SYSTEM/TrustedInstaller handlers")
            values[manifest.action_type] = manifest
        self._values = values

    def require(self, action_type: PrivilegedActionType) -> PrivilegedActionManifest:
        """Return an exact allowlisted manifest or fail without a fallback."""
        try:
            return self._values[action_type]
        except KeyError as exc:
            raise LookupError(f"Privileged action is not registered: {action_type.value}") from exc

    @property
    def actions(self) -> frozenset[PrivilegedActionType]:
        """Expose the immutable action allowlist for diagnostics and tests."""
        return frozenset(self._values)


def build_stage4x3_manifest_registry() -> PrivilegedManifestRegistry:
    """Build the only real Stage 4X3 elevated action allowlist."""
    return PrivilegedManifestRegistry(
        (
            _manifest(
                PrivilegedActionType.SERVICE_START,
                SERVICE_CONTROL_SCHEMA_VERSION,
                SERVICE_CONTROL_POLICY_VERSION,
                "service-control",
                RollbackLevel.MANUAL,
                120,
            ),
            _manifest(
                PrivilegedActionType.SERVICE_STOP,
                SERVICE_CONTROL_SCHEMA_VERSION,
                SERVICE_CONTROL_POLICY_VERSION,
                "service-control",
                RollbackLevel.MANUAL,
                120,
            ),
            _manifest(
                PrivilegedActionType.SERVICE_STARTUP_TYPE_CHANGE,
                SERVICE_STARTUP_SCHEMA_VERSION,
                SERVICE_STARTUP_POLICY_VERSION,
                "service-startup",
                RollbackLevel.FULL,
                120,
            ),
            _manifest(
                PrivilegedActionType.SERVICE_STARTUP_TYPE_RESTORE,
                SERVICE_STARTUP_SCHEMA_VERSION,
                SERVICE_STARTUP_POLICY_VERSION,
                "service-startup",
                RollbackLevel.FULL,
                120,
            ),
            _manifest(
                PrivilegedActionType.STARTUP_MACHINE_DISABLE,
                MACHINE_STARTUP_SCHEMA_VERSION,
                MACHINE_STARTUP_POLICY_VERSION,
                "machine-startup",
                RollbackLevel.FULL,
                120,
            ),
            _manifest(
                PrivilegedActionType.STARTUP_MACHINE_RESTORE,
                MACHINE_STARTUP_SCHEMA_VERSION,
                MACHINE_STARTUP_POLICY_VERSION,
                "machine-startup",
                RollbackLevel.FULL,
                120,
            ),
            _manifest(
                PrivilegedActionType.MSI_UNINSTALL_MACHINE,
                MACHINE_MSI_SCHEMA_VERSION,
                MACHINE_MSI_POLICY_VERSION,
                "machine-msi",
                RollbackLevel.NONE,
                7_200,
            ),
        )
    )


def _manifest(
    action_type: PrivilegedActionType,
    schema: int,
    policy: str,
    handler_key: str,
    rollback: RollbackLevel,
    runtime: int,
) -> PrivilegedActionManifest:
    return PrivilegedActionManifest(
        action_type=action_type,
        action_schema_version=schema,
        safety_policy_version=policy,
        risk_floor=RiskLevel.R3,
        rollback_level=rollback,
        handler_key=handler_key,
        maximum_runtime_seconds=runtime,
    )
