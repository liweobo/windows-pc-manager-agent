"""Narrow registered service tools; no shell or generic service command exists."""

from collections.abc import Callable

from pydantic import BaseModel

from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.service_actions import (
    ServiceStepRequest,
    ServiceStepResult,
    ServiceStepType,
)
from pc_manager_agent.platform_support.service_control import ServiceControlPlatform
from pc_manager_agent.tools.manifest import CancellationToken, ToolManifest


class StartServiceTool:
    """Start one exact separately confirmed service identity."""

    def __init__(
        self,
        platform: ServiceControlPlatform,
        on_dispatched: Callable[[ServiceStepRequest], None] | None = None,
        *,
        risk_level: RiskLevel = RiskLevel.R2,
    ) -> None:
        self._platform = platform
        self._on_dispatched = on_dispatched
        self._manifest = _manifest(
            "system.service.start",
            "Start one exact approved ordinary-user Windows service",
            risk_level,
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return the fixed R2 service-start manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Validate typed input and delegate only the exact SCM start primitive."""
        typed = ServiceStepRequest.model_validate(request)
        if typed.step is not ServiceStepType.START:
            raise ValueError("Service start tool accepts only START")
        if typed.expected_identity_digest != typed.identity.canonical_digest():
            raise ValueError("Service start identity digest is inconsistent")
        return self._platform.start(
            typed.identity,
            typed.expected_startup_configuration_digest,
            typed.expected_state,
            typed.timeout_seconds,
            cancellation,
            lambda: self._notify(typed),
        )

    def _notify(self, request: ServiceStepRequest) -> None:
        if self._on_dispatched is not None:
            self._on_dispatched(request)


class StopServiceTool:
    """Stop one exact separately confirmed service without cascading dependents."""

    def __init__(
        self,
        platform: ServiceControlPlatform,
        on_dispatched: Callable[[ServiceStepRequest], None] | None = None,
        *,
        risk_level: RiskLevel = RiskLevel.R2,
    ) -> None:
        self._platform = platform
        self._on_dispatched = on_dispatched
        self._manifest = _manifest(
            "system.service.stop",
            "Stop one exact approved ordinary-user Windows service without cascade",
            risk_level,
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return the fixed R2 service-stop manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Validate typed input and delegate only the exact SCM stop primitive."""
        typed = ServiceStepRequest.model_validate(request)
        if typed.step is not ServiceStepType.STOP:
            raise ValueError("Service stop tool accepts only STOP")
        if typed.expected_identity_digest != typed.identity.canonical_digest():
            raise ValueError("Service stop identity digest is inconsistent")
        return self._platform.stop(
            typed.identity,
            typed.expected_startup_configuration_digest,
            typed.expected_state,
            typed.timeout_seconds,
            cancellation,
            lambda: self._notify(typed),
        )

    def _notify(self, request: ServiceStepRequest) -> None:
        if self._on_dispatched is not None:
            self._on_dispatched(request)


def _manifest(name: str, description: str, risk: RiskLevel) -> ToolManifest:
    return ToolManifest(
        name=name,
        description=description,
        input_model=ServiceStepRequest,
        output_model=ServiceStepResult,
        risk_level=risk,
        required_permissions=("ordinary-user", "action-specific-service-DACL"),
        read_only=False,
        idempotent=False,
        supports_cancellation=True,
        rollback_level=RollbackLevel.MANUAL,
        preconditions=(
            "deterministic service safety approval",
            "two consumed same-action confirmations",
            "execution-time configuration identity match",
            "dependency policy approval",
        ),
        postconditions=("exact service reaches the requested stable state or truthful failure",),
        timeout_seconds=120.0,
        max_batch_size=1,
        audit_fields=("transaction_id", "action", "identity_digest", "final_state"),
        supported_platforms=("windows",),
        requires_confirmation=True,
        requires_runtime_confirmation=True,
        supports_preview=True,
    )
