"""Synthetic Stage 4X1 objects; no fixture touches Windows privileged APIs."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pc_manager_agent.audit.privileged_actions import PrivilegedActionAuditLogger
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.confirmation.privileged_actions import (
    PrivilegedActionConfirmationService,
)
from pc_manager_agent.domain.privileged_actions import (
    PrivilegedActionEnvelope,
    PrivilegedCallerContext,
    PrivilegeRequirement,
    PrivilegeResolution,
    PrivilegeResolutionStatus,
    ServiceStartPayload,
    ServiceStopPayload,
)
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.service_actions import (
    ServiceStableIdentity,
    ServiceStartupConfiguration,
    ServiceStartupType,
    ServiceState,
)
from pc_manager_agent.orchestration.privileged_actions import PrivilegedActionService
from pc_manager_agent.persistence.privileged_actions import (
    PrivilegedActionRepository,
    PrivilegedRequestReplayStore,
)
from pc_manager_agent.privileged.authentication import EphemeralHmacAuthenticator
from pc_manager_agent.privileged.builder import PrivilegedActionBuilder
from pc_manager_agent.privileged.mock_broker import MockPrivilegedBroker
from pc_manager_agent.privileged.registry import build_stage4x1_registry
from pc_manager_agent.privileged.revalidation import (
    FakePrivilegedService,
    FakePrivilegedSystemState,
    ServicePrivilegedRevalidator,
)
from pc_manager_agent.privileged.serialization import PrivilegedRequestSerializer


@dataclass(slots=True)
class PrivilegedTestStack:
    """Owned repositories and services for one isolated protocol test."""

    audit_repository: AuditRepository
    repository: PrivilegedActionRepository
    serializer: PrivilegedRequestSerializer
    authenticator: EphemeralHmacAuthenticator
    fake_state: FakePrivilegedSystemState
    fake_service: FakePrivilegedService
    caller: PrivilegedCallerContext
    service: PrivilegedActionService
    broker: MockPrivilegedBroker

    def close(self) -> None:
        """Release both SQLite engines."""
        self.repository.close()
        self.audit_repository.close()


def build_privileged_test_stack(
    database_path: Path,
    *,
    request_ttl_seconds: int = 120,
    before_final_revalidation: object | None = None,
    now: Callable[[], datetime] | None = None,
) -> PrivilegedTestStack:
    """Compose one complete Stage 4X1 stack with an injected HMAC fixture key."""
    audit_repository = AuditRepository(database_path)
    audit_repository.initialize()
    repository = PrivilegedActionRepository(database_path)
    repository.initialize()
    serializer = PrivilegedRequestSerializer()
    authenticator = EphemeralHmacAuthenticator(b"stage4x1-test-key-material-32bytes!")
    confirmations = PrivilegedActionConfirmationService(repository, now=now)
    builder = PrivilegedActionBuilder(
        serializer,
        authenticator,
        confirmations,
        request_ttl_seconds=request_ttl_seconds,
        now=now,
    )
    identity = ServiceStableIdentity(
        service_name="ExampleUserService",
        service_type=0x10,
        binary_path_fingerprint="a" * 64,
        service_account="LOCALHOST\\ExampleUser",
    )
    resolution = required_resolution()
    fake_service = FakePrivilegedService(
        identity=identity,
        state=ServiceState.RUNNING,
        startup_configuration=ServiceStartupConfiguration(
            startup_type=ServiceStartupType.MANUAL,
            delayed_auto_start=False,
        ),
        dependency_digest="b" * 64,
        safety_allowed=True,
        safety_digest="c" * 64,
        risk_level=RiskLevel.R3,
        privilege_resolution=resolution,
    )
    fake_state = FakePrivilegedSystemState((fake_service,))
    handler = ServicePrivilegedRevalidator(fake_state)
    registry = build_stage4x1_registry(handler)
    audit = PrivilegedActionAuditLogger(
        audit_repository,
        app_version="test",
        git_commit="0" * 40,
    )
    caller = PrivilegedCallerContext(
        context_id=uuid4(),
        agent_instance_id=uuid4(),
        user_sid_fingerprint="d" * 64,
        session_fingerprint="e" * 64,
    )
    hook = before_final_revalidation if callable(before_final_revalidation) else None
    broker = MockPrivilegedBroker(
        serializer,
        authenticator,
        repository,
        PrivilegedRequestReplayStore(repository),
        registry,
        audit,
        now=now,
        before_final_revalidation=hook,
    )
    service = PrivilegedActionService(
        builder,
        confirmations,
        repository,
        serializer,
        broker,
        audit,
        caller,
    )
    return PrivilegedTestStack(
        audit_repository,
        repository,
        serializer,
        authenticator,
        fake_state,
        fake_service,
        caller,
        service,
        broker,
    )


def required_resolution() -> PrivilegeResolution:
    """Return complete, safety-approved Administrator routing evidence."""
    return PrivilegeResolution(
        status=PrivilegeResolutionStatus.REQUIRED,
        requirement=PrivilegeRequirement.ELEVATED_ADMIN_REQUIRED,
        safety_allowed=True,
        preflight_complete=True,
        access_failure_code=5,
        reason_code="ADMINISTRATOR_REQUIRED",
        explanation="Synthetic action-specific DACL preflight requires Administrator",
    )


def prepare_stop(stack: PrivilegedTestStack) -> PrivilegedActionEnvelope:
    """Approve and register one exact synthetic service-stop request."""
    current = stack.fake_state.inspect_service(stack.fake_service.identity.service_name)
    assert current is not None
    payload = ServiceStopPayload(
        service_identity=current.identity,
        expected_startup_configuration_digest=(current.startup_configuration.canonical_digest()),
        expected_dependency_digest=current.dependency_digest,
    )
    return _prepare(stack, payload)


def prepare_start(stack: PrivilegedTestStack) -> PrivilegedActionEnvelope:
    """Approve and register one exact synthetic service-start request."""
    current = stack.fake_state.inspect_service(stack.fake_service.identity.service_name)
    assert current is not None
    payload = ServiceStartPayload(
        service_identity=current.identity,
        expected_startup_configuration_digest=(current.startup_configuration.canonical_digest()),
        expected_dependency_digest=current.dependency_digest,
    )
    return _prepare(stack, payload)


def _prepare(
    stack: PrivilegedTestStack,
    payload: ServiceStartPayload | ServiceStopPayload,
) -> PrivilegedActionEnvelope:
    current = stack.fake_state.inspect_service(payload.service_identity.service_name)
    assert current is not None
    prepared = stack.service.prepare(
        source_plan_id=uuid4(),
        source_plan_hash=hashlib.sha256(b"source-plan").hexdigest(),
        payload=payload,
        target_identity_hash=current.identity.canonical_digest(),
        object_summary="one exact synthetic service",
        target_state_hash=current.state_digest(),
        safety_digest=current.safety_digest,
        privilege_resolution=current.privilege_resolution,
    )
    stack.service.resolve_plan_confirmation(
        prepared.plan_confirmation.confirmation_id,
        True,
        prepared.plan,
        prepared.preview,
    )
    runtime = stack.service.prepare_runtime_confirmation(
        prepared.plan_confirmation.confirmation_id,
        prepared.plan,
        target_state_hash=current.state_digest(),
        safety_digest=current.safety_digest,
        privilege_resolution=current.privilege_resolution,
    )
    stack.service.resolve_runtime_confirmation(
        runtime.runtime_confirmation.confirmation_id,
        True,
        prepared.plan,
        runtime.preview,
    )
    return stack.service.build_and_register(
        prepared.plan,
        runtime.preview,
        plan_confirmation_id=prepared.plan_confirmation.confirmation_id,
        runtime_confirmation_id=runtime.runtime_confirmation.confirmation_id,
    )


def fixed_time() -> datetime:
    """Return a timezone-aware value useful in model edge-case tests."""
    return datetime(2026, 1, 1, tzinfo=UTC)
