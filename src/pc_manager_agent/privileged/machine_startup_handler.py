"""Broker-side exact HKLM Run disable and conflict-checked restore handler."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pc_manager_agent.domain.elevated_broker import MachineStartupResultEvidence
from pc_manager_agent.domain.privileged_actions import (
    BrokerDecision,
    PrivilegedActionRequest,
    PrivilegedActionType,
    StartupMachineDisablePayload,
    StartupMachineRestorePayload,
    canonical_model_digest,
)
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.startup_actions import (
    StartupActionType,
    StartupBackupPayload,
    StartupIdentity,
    StartupObservation,
    StartupSafetyDecision,
    StartupSource,
)
from pc_manager_agent.domain.startup_errors import StartupConflictError
from pc_manager_agent.persistence.startup_actions import (
    StartupActionRepository,
    StartupBackupVault,
    StartupStoreError,
)
from pc_manager_agent.platform_support.windows.startup_management import (
    WindowsStartupManagementPlatform,
    machine_absent_state_digest,
)
from pc_manager_agent.privileged.dispatcher import (
    FreshPrivilegedEvidence,
    PrivilegedHandlerOutcome,
)
from pc_manager_agent.privileged.revalidation import PrivilegedRevalidationError
from pc_manager_agent.safety.machine_startup_policy import MachineStartupSafetyPolicy
from pc_manager_agent.tools.manifest import CancellationToken


@dataclass(frozen=True, slots=True)
class ValidatedMachineStartupRequest:
    """Decrypted backup plus immutable original evidence after Broker revalidation."""

    backup: StartupBackupPayload
    original_observation: StartupObservation
    action: StartupActionType


class WindowsMachineStartupPrivilegedHandler:
    """Mutate one HKLM Run value without exposing a generic registry writer."""

    def __init__(
        self,
        platform: WindowsStartupManagementPlatform,
        policy: MachineStartupSafetyPolicy,
        vault: StartupBackupVault,
        history: StartupActionRepository,
    ) -> None:
        self._platform = platform
        self._policy = policy
        self._vault = vault
        self._history = history

    @property
    def action_types(self) -> frozenset[PrivilegedActionType]:
        """Return the only machine startup actions supported in Stage 4X3."""
        return frozenset(
            {
                PrivilegedActionType.STARTUP_MACHINE_DISABLE,
                PrivilegedActionType.STARTUP_MACHINE_RESTORE,
            }
        )

    def require(self, request: PrivilegedActionRequest) -> FreshPrivilegedEvidence:
        """Reload backup, history, live value, identity, safety, and exact registry view."""
        payload = request.payload
        if not isinstance(payload, (StartupMachineDisablePayload, StartupMachineRestorePayload)):
            raise PrivilegedRevalidationError(
                BrokerDecision.ACTION_NOT_ALLOWLISTED,
                "Machine startup handler received another payload type",
            )
        if request.risk_level is not RiskLevel.R3:
            raise PrivilegedRevalidationError(
                BrokerDecision.RISK_CHANGED,
                "Elevated machine startup actions must remain R3",
            )
        identity = StartupIdentity(
            source=StartupSource.HKLM_RUN,
            registry=payload.registry_identity,
        )
        if (
            identity.canonical_digest() != payload.startup_identity_digest
            or identity.canonical_digest() != request.target_identity_hash
        ):
            raise PrivilegedRevalidationError(
                BrokerDecision.TARGET_CHANGED,
                "Machine startup identity binding changed",
            )
        try:
            backup = self._vault.load(payload.backup_id, expected_digest=payload.backup_digest)
        except StartupStoreError as exc:
            raise PrivilegedRevalidationError(
                BrokerDecision.BACKUP_INVALID,
                "Machine startup backup is unavailable or corrupt",
            ) from exc
        if (
            backup.source is not StartupSource.HKLM_RUN
            or backup.original_identity.canonical_digest() != identity.canonical_digest()
        ):
            raise PrivilegedRevalidationError(
                BrokerDecision.BACKUP_BINDING_INVALID,
                "Machine startup backup does not belong to the exact value",
            )
        if isinstance(payload, StartupMachineDisablePayload):
            observation = self._platform.inspect(identity)
            if observation is None:
                raise PrivilegedRevalidationError(
                    BrokerDecision.TARGET_CHANGED,
                    "Machine startup value disappeared before disable",
                )
            state_hash = observation.current_state_digest()
            action = StartupActionType.DISABLE
            original = observation
        else:
            try:
                record = self._history.get_disabled(payload.backup_id)
            except StartupStoreError as exc:
                raise PrivilegedRevalidationError(
                    BrokerDecision.BACKUP_BINDING_INVALID,
                    "Machine startup restore history is unavailable",
                ) from exc
            if (
                record.original_transaction_id != payload.original_disable_transaction_id
                or record.identity.canonical_digest() != identity.canonical_digest()
                or record.backup_digest != payload.backup_digest
            ):
                raise PrivilegedRevalidationError(
                    BrokerDecision.BACKUP_BINDING_INVALID,
                    "Machine startup restore history changed",
                )
            if self._platform.inspect(identity) is not None:
                raise PrivilegedRevalidationError(
                    BrokerDecision.RESTORE_CONFLICT,
                    "Machine startup restore target is occupied",
                )
            state_hash = machine_absent_state_digest(identity)
            action = StartupActionType.RESTORE
            original = record.original_observation
        if state_hash != payload.expected_state_digest:
            raise PrivilegedRevalidationError(
                BrokerDecision.REGISTRY_VIEW_CHANGED,
                "Machine startup state or registry view changed",
            )
        safety = self._policy.assess(original, action)
        safety_digest = canonical_model_digest(safety.model_dump(mode="json"))
        if (
            safety.decision is not StartupSafetyDecision.ALLOW
            or safety_digest != payload.safety_digest
        ):
            raise PrivilegedRevalidationError(
                BrokerDecision.SAFETY_BLOCKED,
                "Fresh machine startup safety policy blocked or changed",
            )
        return FreshPrivilegedEvidence(
            action_type=request.action_type,
            target_state_hash=state_hash,
            safety_digest=safety_digest,
            validated=ValidatedMachineStartupRequest(backup, original, action),
        )

    def execute_and_verify(
        self,
        request: PrivilegedActionRequest,
        fresh: FreshPrivilegedEvidence,
        cancellation: CancellationToken,
        on_dispatched: Callable[[], None] | None = None,
    ) -> PrivilegedHandlerOutcome:
        """Run one transacted value mutation, verify, and persist restore ownership."""
        payload = request.payload
        validated = fresh.validated
        if not isinstance(
            payload,
            (StartupMachineDisablePayload, StartupMachineRestorePayload),
        ) or not isinstance(validated, ValidatedMachineStartupRequest):
            raise PrivilegedRevalidationError(
                BrokerDecision.PRECONDITION_FAILED,
                "Machine startup validated evidence type changed",
            )
        dispatched = False

        def mark_dispatched() -> None:
            nonlocal dispatched
            dispatched = True
            if on_dispatched is not None:
                on_dispatched()

        try:
            if validated.action is StartupActionType.DISABLE:
                mutation = self._platform.disable_machine_run(
                    validated.backup,
                    cancellation,
                    mark_dispatched,
                )
                if mutation.verified:
                    self._history.record_machine_disabled(
                        request.plan_id,
                        payload.backup_id,
                        payload.backup_digest,
                        validated.original_observation,
                    )
                present_after = (
                    self._platform.inspect(validated.backup.original_identity) is not None
                )
                expected_present = False
            else:
                mutation = self._platform.restore_machine_run(
                    validated.backup,
                    cancellation,
                    mark_dispatched,
                )
                if mutation.verified:
                    self._history.mark_restored(payload.backup_id)
                present_after = (
                    self._platform.inspect(validated.backup.original_identity) is not None
                )
                expected_present = True
        except StartupConflictError as exc:
            raise PrivilegedRevalidationError(
                BrokerDecision.RESTORE_CONFLICT,
                "Machine startup value changed before the registry transaction",
            ) from exc
        except StartupStoreError as exc:
            raise PrivilegedRevalidationError(
                BrokerDecision.PERSISTENCE_UNAVAILABLE,
                "Machine startup recovery history could not be persisted",
            ) from exc
        verified = mutation.verified and present_after is expected_present
        identity = validated.backup.original_identity
        current = self._platform.inspect(identity) if present_after else None
        post_hash = (
            current.current_state_digest()
            if current is not None
            else machine_absent_state_digest(identity)
        )
        registry = validated.backup.original_identity.registry
        if registry is None:  # pragma: no cover - strict backup model makes this unreachable
            raise PrivilegedRevalidationError(
                BrokerDecision.PRECONDITION_FAILED,
                "Machine startup registry identity disappeared",
            )
        return PrivilegedHandlerOutcome(
            execution_started=dispatched,
            execution_completed=dispatched,
            verified=verified,
            uncertain=dispatched and not mutation.verified,
            pre_state_hash=fresh.target_state_hash,
            post_state_hash=post_hash if verified else None,
            result_code=(
                "MACHINE_STARTUP_VALUE_VERIFIED"
                if verified
                else "MACHINE_STARTUP_VERIFICATION_FAILED"
            ),
            message=mutation.message,
            rollback_level=RollbackLevel.FULL,
            action_evidence=MachineStartupResultEvidence(
                registry_view=registry.registry_view,
                value_present_before=validated.action is StartupActionType.DISABLE,
                value_present_after=present_after,
            ),
        )
