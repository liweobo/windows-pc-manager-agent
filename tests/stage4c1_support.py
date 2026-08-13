"""Shared Fake SCM fixtures for Stage 4C1 tests; no real service is mutated."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pc_manager_agent.domain.service_actions import (
    ServiceActionType,
    ServiceIdentity,
    ServiceObservation,
    ServicePermissionEvidence,
    ServiceRelation,
    ServiceState,
    ServiceStepResult,
    ServiceStepType,
    canonical_binary_fingerprint,
)
from pc_manager_agent.tools.manifest import CancellationToken


def service_observation(
    binary: Path,
    *,
    state: ServiceState = ServiceState.RUNNING,
    service_name: str = "UserDemoSvc",
    display_name: str = "User Demo Service",
    account: str = r"DESKTOP\alice",
    service_type: int = 0x10,
    start_type: int = 3,
    publisher: str | None = "Example Vendor",
    publisher_verified: bool = True,
    dependencies: tuple[ServiceRelation, ...] = (),
    dependents: tuple[ServiceRelation, ...] = (),
    controls_accepted: int = 1,
) -> ServiceObservation:
    """Build a complete ordinary-user service observation."""
    return ServiceObservation(
        identity=ServiceIdentity(
            service_name=service_name,
            display_name=display_name,
            service_type=service_type,
            binary_path_fingerprint=canonical_binary_fingerprint(str(binary)),
            service_account=account,
            start_type=start_type,
        ),
        state=state,
        controls_accepted=controls_accepted,
        process_id=100 if state is ServiceState.RUNNING else 0,
        binary_path=binary,
        publisher=publisher,
        publisher_verified=publisher_verified,
        description="Test-only fake service",
        dependencies=dependencies,
        dependents=dependents,
    )


class FakeServicePlatform:
    """In-memory platform implementing exact transitions for deterministic tests."""

    def __init__(
        self,
        observation: ServiceObservation,
        *,
        permissions: ServicePermissionEvidence | None = None,
        fail_start: bool = False,
        fail_stop: bool = False,
        cancel_after_stop: bool = False,
        mutate_identity_after_stop: bool = False,
        unverified_start: bool = False,
    ) -> None:
        self.observation = observation
        self.permissions = permissions or ServicePermissionEvidence(
            can_query=True,
            can_start=True,
            can_stop=True,
            can_enumerate_dependents=True,
            process_elevated=False,
        )
        self.fail_start = fail_start
        self.fail_stop = fail_stop
        self.cancel_after_stop = cancel_after_stop
        self.mutate_identity_after_stop = mutate_identity_after_stop
        self.unverified_start = unverified_start
        self.calls: list[ServiceStepType] = []

    def list_services(self, max_items: int = 5_000) -> tuple[ServiceObservation, ...]:
        """Return the one fake service within the requested bound."""
        return (self.observation,)[:max_items]

    def inspect(self, service_name: str) -> ServiceObservation | None:
        """Return only an exact case-insensitive service-name match."""
        if self.observation.identity.service_name.casefold() != service_name.casefold():
            return None
        return self.observation

    def evaluate_permissions(
        self,
        service_name: str,
        action: ServiceActionType,
    ) -> ServicePermissionEvidence:
        """Return configured read-only permission evidence."""
        del service_name, action
        return self.permissions

    def start(
        self,
        identity: ServiceIdentity,
        expected_state: ServiceState,
        timeout_seconds: float,
        cancellation: CancellationToken,
        on_dispatched: Callable[[], None] | None = None,
    ) -> ServiceStepResult:
        """Record one fake start and optionally fail after dispatch."""
        return self._control(
            ServiceStepType.START,
            identity,
            expected_state,
            ServiceState.RUNNING,
            timeout_seconds,
            cancellation,
            on_dispatched,
            self.fail_start,
        )

    def stop(
        self,
        identity: ServiceIdentity,
        expected_state: ServiceState,
        timeout_seconds: float,
        cancellation: CancellationToken,
        on_dispatched: Callable[[], None] | None = None,
    ) -> ServiceStepResult:
        """Record one fake stop and optionally fail after dispatch."""
        return self._control(
            ServiceStepType.STOP,
            identity,
            expected_state,
            ServiceState.STOPPED,
            timeout_seconds,
            cancellation,
            on_dispatched,
            self.fail_stop,
        )

    def _control(
        self,
        step: ServiceStepType,
        identity: ServiceIdentity,
        expected_state: ServiceState,
        target_state: ServiceState,
        timeout_seconds: float,
        cancellation: CancellationToken,
        on_dispatched: Callable[[], None] | None,
        fail: bool,
    ) -> ServiceStepResult:
        del timeout_seconds
        started = datetime.now(UTC)
        if identity.canonical_digest() != self.observation.identity.canonical_digest():
            raise RuntimeError("identity changed")
        if expected_state is not self.observation.state:
            raise RuntimeError("state changed")
        if cancellation.is_cancelled:
            return ServiceStepResult(
                step=step,
                identity_digest=identity.canonical_digest(),
                before_state=expected_state,
                after_state=expected_state,
                control_dispatched=False,
                verified=False,
                message="cancelled before dispatch",
                started_at=started,
                completed_at=datetime.now(UTC),
            )
        if self.observation.state is target_state:
            return ServiceStepResult(
                step=step,
                identity_digest=identity.canonical_digest(),
                before_state=expected_state,
                after_state=target_state,
                control_dispatched=False,
                verified=True,
                message=f"fake service was already {target_state.value}",
                started_at=started,
                completed_at=datetime.now(UTC),
            )
        self.calls.append(step)
        if on_dispatched is not None:
            on_dispatched()
        if fail:
            raise RuntimeError(f"fake {step.value.lower()} failed after dispatch")
        if step is ServiceStepType.START and self.unverified_start:
            self.observation = self.observation.model_copy(
                update={"state": ServiceState.START_PENDING, "process_id": 0}
            )
            return ServiceStepResult(
                step=step,
                identity_digest=identity.canonical_digest(),
                before_state=expected_state,
                after_state=ServiceState.START_PENDING,
                control_dispatched=True,
                verified=False,
                message="fake start timed out in START_PENDING",
                started_at=started,
                completed_at=datetime.now(UTC),
            )
        self.observation = self.observation.model_copy(
            update={
                "state": target_state,
                "process_id": 100 if target_state is ServiceState.RUNNING else 0,
                "controls_accepted": 1 if target_state is ServiceState.RUNNING else 0,
            }
        )
        if step is ServiceStepType.STOP and self.mutate_identity_after_stop:
            self.observation = self.observation.model_copy(
                update={
                    "identity": self.observation.identity.model_copy(
                        update={"start_type": self.observation.identity.start_type + 1}
                    )
                }
            )
        if step is ServiceStepType.STOP and self.cancel_after_stop:
            cancellation.cancel()
        return ServiceStepResult(
            step=step,
            identity_digest=identity.canonical_digest(),
            before_state=expected_state,
            after_state=target_state,
            control_dispatched=True,
            verified=True,
            message=f"fake service reached {target_state.value}",
            started_at=started,
            completed_at=datetime.now(UTC),
        )
