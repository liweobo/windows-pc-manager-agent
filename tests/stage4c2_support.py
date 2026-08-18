"""Shared deterministic Stage 4C2 fixtures; no real Windows service is changed."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from pc_manager_agent.domain.service_actions import (
    ServiceStartupConfiguration,
    ServiceStartupType,
)
from pc_manager_agent.domain.service_startup_actions import (
    ServiceStartupActionRequest,
    ServiceStartupActionType,
    ServiceStartupMutationResult,
    ServiceStartupPermissionEvidence,
)
from pc_manager_agent.safety.service_startup_policy import build_service_startup_impact
from pc_manager_agent.tools.manifest import CancellationToken
from tests.stage4c1_support import FakeServicePlatform


class FakeProtector:
    """Reversible test-only protector with an explicit non-production marker."""

    def protect(self, plaintext: bytes) -> bytes:
        """Prefix plaintext so vault tests exercise serialization and digest verification."""
        return b"TEST-ONLY\0" + plaintext[::-1]

    def unprotect(self, ciphertext: bytes) -> bytes:
        """Reverse only correctly marked test payloads."""
        prefix = b"TEST-ONLY\0"
        if not ciphertext.startswith(prefix):
            raise ValueError("invalid test ciphertext")
        return ciphertext[len(prefix) :][::-1]


class FakeServiceStartupPlatform:
    """In-memory Automatic/Manual adapter that never touches SCM."""

    def __init__(
        self,
        control: FakeServicePlatform,
        *,
        permissions: ServiceStartupPermissionEvidence | None = None,
        fail_after_dispatch: bool = False,
        change_runtime_state: bool = False,
    ) -> None:
        self.control = control
        self.permissions = permissions or ServiceStartupPermissionEvidence(
            can_query_configuration=True,
            can_change_configuration=True,
            process_elevated=False,
        )
        self.fail_after_dispatch = fail_after_dispatch
        self.change_runtime_state = change_runtime_state
        self.calls: list[ServiceStartupActionType] = []

    def evaluate_permissions(self, service_name: str) -> ServiceStartupPermissionEvidence:
        """Return configured least-privilege evidence without mutation."""
        del service_name
        return self.permissions

    def set_automatic(
        self,
        request: ServiceStartupActionRequest,
        cancellation: CancellationToken,
        on_dispatched: Callable[[], None] | None = None,
    ) -> ServiceStartupMutationResult:
        """Apply a fake Automatic transition after exact precondition checks."""
        if request.action is not ServiceStartupActionType.SET_AUTOMATIC:
            raise ValueError("wrong fake action")
        return self._change(request, cancellation, on_dispatched)

    def set_manual(
        self,
        request: ServiceStartupActionRequest,
        cancellation: CancellationToken,
        on_dispatched: Callable[[], None] | None = None,
    ) -> ServiceStartupMutationResult:
        """Apply a fake Manual transition after exact precondition checks."""
        if request.action is not ServiceStartupActionType.SET_MANUAL:
            raise ValueError("wrong fake action")
        return self._change(request, cancellation, on_dispatched)

    def restore(
        self,
        request: ServiceStartupActionRequest,
        cancellation: CancellationToken,
        on_dispatched: Callable[[], None] | None = None,
    ) -> ServiceStartupMutationResult:
        """Apply a fake independently confirmed reverse transition."""
        if request.action is not ServiceStartupActionType.RESTORE:
            raise ValueError("wrong fake restore action")
        return self._change(request, cancellation, on_dispatched)

    def _change(
        self,
        request: ServiceStartupActionRequest,
        cancellation: CancellationToken,
        on_dispatched: Callable[[], None] | None,
    ) -> ServiceStartupMutationResult:
        started = datetime.now(UTC)
        before = self.control.observation
        if before.identity.canonical_digest() != request.identity.canonical_digest():
            raise RuntimeError("identity changed")
        if before.startup_configuration != request.expected_source_configuration:
            raise RuntimeError("configuration changed")
        if before.state is not request.expected_runtime_state:
            raise RuntimeError("runtime changed")
        if build_service_startup_impact(before).canonical_digest() != (
            request.expected_impact_digest
        ):
            raise RuntimeError("impact changed")
        if cancellation.is_cancelled:
            return ServiceStartupMutationResult(
                action=request.action,
                identity_digest=request.identity.canonical_digest(),
                before_configuration=before.startup_configuration,
                after_configuration=before.startup_configuration,
                before_runtime_state=before.state,
                after_runtime_state=before.state,
                change_dispatched=False,
                verified=False,
                runtime_unchanged=True,
                message="cancelled before fake dispatch",
                started_at=started,
                completed_at=datetime.now(UTC),
            )
        self.calls.append(request.action)
        if on_dispatched is not None:
            on_dispatched()
        if self.fail_after_dispatch:
            raise RuntimeError("fake configuration write failed after dispatch")
        after_state = before.state
        if self.change_runtime_state:
            from pc_manager_agent.domain.service_actions import ServiceState

            after_state = (
                ServiceState.STOPPED
                if before.state is not ServiceState.STOPPED
                else ServiceState.RUNNING
            )
        self.control.observation = before.model_copy(
            update={
                "startup_configuration": request.target_configuration,
                "state": after_state,
            }
        )
        runtime_unchanged = after_state is before.state
        return ServiceStartupMutationResult(
            action=request.action,
            identity_digest=request.identity.canonical_digest(),
            before_configuration=before.startup_configuration,
            after_configuration=request.target_configuration,
            before_runtime_state=before.state,
            after_runtime_state=after_state,
            change_dispatched=True,
            verified=runtime_unchanged,
            runtime_unchanged=runtime_unchanged,
            message=(
                "fake write verified" if runtime_unchanged else "runtime changed unexpectedly"
            ),
            started_at=started,
            completed_at=datetime.now(UTC),
        )


def configuration(startup_type: ServiceStartupType) -> ServiceStartupConfiguration:
    """Build one supported non-delayed test configuration."""
    return ServiceStartupConfiguration(
        startup_type=startup_type,
        delayed_auto_start=False,
    )
