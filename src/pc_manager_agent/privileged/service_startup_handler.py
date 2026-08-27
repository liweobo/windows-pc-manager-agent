"""Broker-side Automatic/Manual service startup change and restore handler."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pc_manager_agent.domain.elevated_broker import ServiceStartupResultEvidence
from pc_manager_agent.domain.privileged_actions import (
    BrokerDecision,
    PrivilegedActionRequest,
    PrivilegedActionType,
    ServiceStartupTypeChangePayload,
    ServiceStartupTypeRestorePayload,
    canonical_model_digest,
)
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.service_actions import ServiceObservation, ServiceStartupConfiguration
from pc_manager_agent.domain.service_startup_actions import (
    ServiceStartupActionRequest,
    ServiceStartupActionType,
)
from pc_manager_agent.persistence.service_startup_actions import (
    ServiceStartupActionRepository,
    ServiceStartupBackupVault,
    ServiceStartupStoreError,
)
from pc_manager_agent.platform_support.service_control import ServiceControlPlatform
from pc_manager_agent.platform_support.service_startup import ServiceStartupPlatform
from pc_manager_agent.privileged.dispatcher import (
    FreshPrivilegedEvidence,
    PrivilegedHandlerOutcome,
)
from pc_manager_agent.privileged.revalidation import PrivilegedRevalidationError
from pc_manager_agent.safety.service_startup_policy import (
    ServiceStartupSafetyPolicy,
    build_service_startup_impact,
)
from pc_manager_agent.tools.manifest import CancellationToken


@dataclass(frozen=True, slots=True)
class ValidatedServiceStartupRequest:
    """Fresh observation and exact source/target pair validated inside the Broker."""

    observation: ServiceObservation
    action: ServiceStartupActionType
    target: ServiceStartupConfiguration


class WindowsServiceStartupPrivilegedHandler:
    """Change only one startup-type field and never alter current service state."""

    def __init__(
        self,
        control: ServiceControlPlatform,
        startup: ServiceStartupPlatform,
        policy: ServiceStartupSafetyPolicy,
        vault: ServiceStartupBackupVault,
        history: ServiceStartupActionRepository,
    ) -> None:
        self._control = control
        self._startup = startup
        self._policy = policy
        self._vault = vault
        self._history = history

    @property
    def action_types(self) -> frozenset[PrivilegedActionType]:
        """Return change and independent conflict-checked restore actions."""
        return frozenset(
            {
                PrivilegedActionType.SERVICE_STARTUP_TYPE_CHANGE,
                PrivilegedActionType.SERVICE_STARTUP_TYPE_RESTORE,
            }
        )

    def require(self, request: PrivilegedActionRequest) -> FreshPrivilegedEvidence:
        """Reload identity, policy, impact, configuration, runtime, backup, and history."""
        payload = request.payload
        if isinstance(payload, ServiceStartupTypeChangePayload):
            action = _change_action(payload)
            target = ServiceStartupConfiguration(
                startup_type=payload.requested_startup_type,
                delayed_auto_start=False,
            )
            expected = payload.expected_current_configuration
            self._require_change_backup(payload)
        elif isinstance(payload, ServiceStartupTypeRestorePayload):
            action = ServiceStartupActionType.RESTORE
            target = payload.target_original_configuration
            expected = payload.expected_current_configuration
            self._require_restore_history(payload)
        else:
            raise PrivilegedRevalidationError(
                BrokerDecision.ACTION_NOT_ALLOWLISTED,
                "Service startup handler received another payload type",
            )
        if request.risk_level is not RiskLevel.R3:
            raise PrivilegedRevalidationError(
                BrokerDecision.RISK_CHANGED,
                "Elevated service startup actions must remain R3",
            )
        observation = self._control.inspect(payload.service_identity.service_name)
        if observation is None:
            raise PrivilegedRevalidationError(
                BrokerDecision.TARGET_CHANGED,
                "Exact service no longer exists",
            )
        if (
            observation.identity.canonical_digest() != payload.service_identity.canonical_digest()
            or observation.identity.canonical_digest() != request.target_identity_hash
        ):
            raise PrivilegedRevalidationError(
                BrokerDecision.TARGET_CHANGED,
                "Stable service identity changed",
            )
        if observation.startup_configuration != expected:
            decision = (
                BrokerDecision.RESTORE_CONFLICT
                if isinstance(payload, ServiceStartupTypeRestorePayload)
                else BrokerDecision.TARGET_CHANGED
            )
            raise PrivilegedRevalidationError(
                decision,
                "Current service startup configuration changed",
            )
        if observation.state is not payload.expected_runtime_state or observation.state.is_pending:
            raise PrivilegedRevalidationError(
                BrokerDecision.PRECONDITION_FAILED,
                "Service runtime state changed",
            )
        impact = build_service_startup_impact(observation)
        if impact.canonical_digest() != payload.impact_digest:
            raise PrivilegedRevalidationError(
                BrokerDecision.PRECONDITION_FAILED,
                "Service dependency impact changed",
            )
        safety = self._policy.assess(observation, action, target)
        safety_digest = canonical_model_digest(safety.model_dump(mode="json"))
        if not safety.allowed or safety_digest != payload.safety_digest:
            raise PrivilegedRevalidationError(
                BrokerDecision.SAFETY_BLOCKED,
                "Fresh service startup safety policy blocked or changed",
            )
        validated = ValidatedServiceStartupRequest(observation, action, target)
        return FreshPrivilegedEvidence(
            action_type=request.action_type,
            target_state_hash=observation.state_digest(),
            safety_digest=safety_digest,
            validated=validated,
        )

    def execute_and_verify(
        self,
        request: PrivilegedActionRequest,
        fresh: FreshPrivilegedEvidence,
        cancellation: CancellationToken,
        on_dispatched: Callable[[], None] | None = None,
    ) -> PrivilegedHandlerOutcome:
        """Use the narrow SCM adapter and independently read back config and runtime."""
        payload = request.payload
        validated = fresh.validated
        if not isinstance(
            payload,
            (ServiceStartupTypeChangePayload, ServiceStartupTypeRestorePayload),
        ) or not isinstance(validated, ValidatedServiceStartupRequest):
            raise PrivilegedRevalidationError(
                BrokerDecision.PRECONDITION_FAILED,
                "Service startup validated evidence type changed",
            )
        action_request = ServiceStartupActionRequest(
            action=validated.action,
            identity=payload.service_identity,
            expected_source_configuration=payload.expected_current_configuration,
            target_configuration=validated.target,
            expected_runtime_state=payload.expected_runtime_state,
            expected_impact_digest=payload.impact_digest,
            backup_id=payload.backup_id,
            backup_digest=payload.backup_digest,
        )
        if validated.action is ServiceStartupActionType.SET_AUTOMATIC:
            mutation = self._startup.set_automatic(action_request, cancellation, on_dispatched)
        elif validated.action is ServiceStartupActionType.SET_MANUAL:
            mutation = self._startup.set_manual(action_request, cancellation, on_dispatched)
        else:
            mutation = self._startup.restore(action_request, cancellation, on_dispatched)
        current = self._control.inspect(payload.service_identity.service_name)
        verified = bool(
            mutation.change_dispatched
            and mutation.verified
            and mutation.runtime_unchanged
            and current is not None
            and current.identity.canonical_digest() == request.target_identity_hash
            and current.startup_configuration == validated.target
            and current.state is payload.expected_runtime_state
        )
        if verified:
            if isinstance(payload, ServiceStartupTypeChangePayload):
                self._history.record_privileged_change(
                    transaction_id=payload.source_transaction_id,
                    backup_id=payload.backup_id,
                    backup_digest=payload.backup_digest,
                    stable_identity=payload.service_identity,
                    display_name=validated.observation.display_name,
                    original_configuration=payload.expected_current_configuration,
                    written_configuration=validated.target,
                    original_runtime_state=payload.expected_runtime_state,
                )
            else:
                self._history.mark_restored(payload.backup_id)
        post_hash = current.state_digest() if current is not None else None
        return PrivilegedHandlerOutcome(
            execution_started=mutation.change_dispatched,
            execution_completed=mutation.change_dispatched,
            verified=verified,
            uncertain=False,
            pre_state_hash=fresh.target_state_hash,
            post_state_hash=post_hash if verified else None,
            result_code=(
                "SERVICE_STARTUP_CONFIGURATION_VERIFIED"
                if verified
                else "SERVICE_STARTUP_VERIFICATION_FAILED"
            ),
            message=mutation.message,
            rollback_level=RollbackLevel.FULL,
            action_evidence=(
                ServiceStartupResultEvidence(
                    before_configuration=mutation.before_configuration,
                    after_configuration=mutation.after_configuration,
                    before_runtime_state=mutation.before_runtime_state,
                    after_runtime_state=mutation.after_runtime_state,
                    runtime_unchanged=mutation.runtime_unchanged,
                )
                if mutation.change_dispatched
                else None
            ),
        )

    def _require_change_backup(self, payload: ServiceStartupTypeChangePayload) -> None:
        try:
            backup = self._vault.load(payload.backup_id, expected_digest=payload.backup_digest)
        except ServiceStartupStoreError as exc:
            raise PrivilegedRevalidationError(
                BrokerDecision.BACKUP_INVALID,
                "Service startup backup is unavailable or corrupt",
            ) from exc
        if (
            backup.stable_identity.canonical_digest() != payload.service_identity.canonical_digest()
            or backup.original_configuration != payload.expected_current_configuration
            or backup.original_runtime_state is not payload.expected_runtime_state
        ):
            raise PrivilegedRevalidationError(
                BrokerDecision.BACKUP_BINDING_INVALID,
                "Service startup backup does not belong to the exact request",
            )

    def _require_restore_history(self, payload: ServiceStartupTypeRestorePayload) -> None:
        try:
            backup = self._vault.load(payload.backup_id, expected_digest=payload.backup_digest)
            record = self._history.get_change(payload.backup_id)
        except ServiceStartupStoreError as exc:
            raise PrivilegedRevalidationError(
                BrokerDecision.BACKUP_INVALID,
                "Service startup restore history or backup is unavailable",
            ) from exc
        if (
            record.original_transaction_id != payload.original_change_transaction_id
            or record.stable_identity.canonical_digest()
            != payload.service_identity.canonical_digest()
            or record.written_configuration != payload.expected_current_configuration
            or record.original_configuration != payload.target_original_configuration
            or backup.original_configuration != payload.target_original_configuration
            or backup.stable_identity.canonical_digest()
            != payload.service_identity.canonical_digest()
        ):
            raise PrivilegedRevalidationError(
                BrokerDecision.BACKUP_BINDING_INVALID,
                "Restore source does not match the Agent-owned service change",
            )


def _change_action(payload: ServiceStartupTypeChangePayload) -> ServiceStartupActionType:
    return (
        ServiceStartupActionType.SET_AUTOMATIC
        if payload.requested_startup_type.value == "AUTOMATIC"
        else ServiceStartupActionType.SET_MANUAL
    )
