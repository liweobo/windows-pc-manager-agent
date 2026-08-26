from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from pc_manager_agent.confirmation.privileged_actions import (
    PrivilegedActionConfirmation,
    PrivilegedActionConfirmationService,
    PrivilegedConfirmationError,
    PrivilegedConfirmationState,
    PrivilegedConfirmationTier,
)
from pc_manager_agent.domain.privileged_actions import (
    PrivilegeRequirement,
    PrivilegeResolutionStatus,
    ServiceStopPayload,
)
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.orchestration.privileged_actions import PreparedPrivilegedAction
from tests.fixtures.privileged_actions import (
    PrivilegedTestStack,
    build_privileged_test_stack,
    required_resolution,
)


class Clock:
    """Small mutable UTC clock for deterministic expiry tests."""

    def __init__(self) -> None:
        self.value = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value


def _prepare_pending(stack: PrivilegedTestStack) -> PreparedPrivilegedAction:
    current = stack.fake_state.inspect_service(stack.fake_service.identity.service_name)
    assert current is not None
    payload = ServiceStopPayload(
        service_identity=current.identity,
        expected_startup_configuration_digest=(current.startup_configuration.canonical_digest()),
        expected_dependency_digest=current.dependency_digest,
    )
    return stack.service.prepare(
        source_plan_id=uuid4(),
        source_plan_hash=hashlib.sha256(b"source-plan").hexdigest(),
        payload=payload,
        target_identity_hash=current.identity.canonical_digest(),
        object_summary="one exact synthetic service",
        target_state_hash=current.state_digest(),
        safety_digest=current.safety_digest,
        privilege_resolution=current.privilege_resolution,
    )


def test_confirmation_model_rejects_ambiguous_lifetime_and_authority(tmp_path: Path) -> None:
    stack = build_privileged_test_stack(tmp_path / "state.db")
    try:
        pending = _prepare_pending(stack).plan_confirmation
        raw = pending.model_dump(mode="python")
        invalid = (
            {"requested_at": pending.requested_at.replace(tzinfo=None)},
            {"expires_at": pending.expires_at.replace(tzinfo=None)},
            {
                "confirmed_at": datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=8))),
            },
            {"expires_at": pending.requested_at},
            {"parent_confirmation_id": uuid4()},
            {"tier": PrivilegedConfirmationTier.RUNTIME, "parent_confirmation_id": None},
            {"risk_level": RiskLevel.R2},
            {"privilege_requirement": PrivilegeRequirement.STANDARD_USER},
            {"state": PrivilegedConfirmationState.APPROVED, "confirmed_at": None},
        )
        for updates in invalid:
            with pytest.raises(ValidationError):
                PrivilegedActionConfirmation.model_validate({**raw, **updates})
    finally:
        stack.close()


def test_confirmation_service_rejects_invalid_ttl(tmp_path: Path) -> None:
    stack = build_privileged_test_stack(tmp_path / "state.db")
    try:
        for plan_ttl, runtime_ttl in ((0, 60), (300, 0)):
            with pytest.raises(ValueError):
                PrivilegedActionConfirmationService(
                    stack.repository,
                    plan_ttl_seconds=plan_ttl,
                    runtime_ttl_seconds=runtime_ttl,
                )
    finally:
        stack.close()


def test_plan_confirmation_rejection_and_double_resolution_fail_closed(tmp_path: Path) -> None:
    stack = build_privileged_test_stack(tmp_path / "state.db")
    try:
        prepared = _prepare_pending(stack)
        rejected = stack.service.resolve_plan_confirmation(
            prepared.plan_confirmation.confirmation_id,
            False,
            prepared.plan,
            prepared.preview,
        )
        assert rejected.state is PrivilegedConfirmationState.REJECTED
        with pytest.raises(PrivilegedConfirmationError):
            stack.service.resolve_plan_confirmation(
                rejected.confirmation_id,
                True,
                prepared.plan,
                prepared.preview,
            )
    finally:
        stack.close()


def test_expired_pending_confirmation_is_persistently_expired(tmp_path: Path) -> None:
    clock = Clock()
    stack = build_privileged_test_stack(tmp_path / "state.db", now=clock)
    try:
        prepared = _prepare_pending(stack)
        clock.value += timedelta(minutes=6)
        with pytest.raises(PrivilegedConfirmationError, match="expired"):
            stack.service.resolve_plan_confirmation(
                prepared.plan_confirmation.confirmation_id,
                True,
                prepared.plan,
                prepared.preview,
            )
        stored = stack.repository.get_confirmation(prepared.plan_confirmation.confirmation_id)
        assert stored.state is PrivilegedConfirmationState.EXPIRED
    finally:
        stack.close()


def test_changed_plan_or_preview_invalidates_plan_confirmation(tmp_path: Path) -> None:
    for variant in ("plan", "preview"):
        stack = build_privileged_test_stack(tmp_path / f"{variant}.db")
        try:
            prepared = _prepare_pending(stack)
            plan = prepared.plan
            preview = prepared.preview
            if variant == "plan":
                plan = plan.model_copy(update={"object_summary_digest": "f" * 64})
            else:
                preview = preview.model_copy(update={"safety_digest": "f" * 64})
            with pytest.raises(PrivilegedConfirmationError):
                stack.service.resolve_plan_confirmation(
                    prepared.plan_confirmation.confirmation_id,
                    True,
                    plan,
                    preview,
                )
        finally:
            stack.close()


def test_runtime_gate_requires_current_approved_parent(tmp_path: Path) -> None:
    stack = build_privileged_test_stack(tmp_path / "state.db")
    try:
        prepared = _prepare_pending(stack)
        current = stack.fake_state.inspect_service(stack.fake_service.identity.service_name)
        assert current is not None
        with pytest.raises(PrivilegedConfirmationError, match="stale"):
            stack.service.prepare_runtime_confirmation(
                prepared.plan_confirmation.confirmation_id,
                prepared.plan,
                target_state_hash=current.state_digest(),
                safety_digest=current.safety_digest,
                privilege_resolution=current.privilege_resolution,
            )
    finally:
        stack.close()


def test_approved_pair_rejects_mismatched_runtime_confirmation(tmp_path: Path) -> None:
    stack = build_privileged_test_stack(tmp_path / "state.db")
    try:
        prepared = _prepare_pending(stack)
        stack.service.resolve_plan_confirmation(
            prepared.plan_confirmation.confirmation_id,
            True,
            prepared.plan,
            prepared.preview,
        )
        current = stack.fake_state.inspect_service(stack.fake_service.identity.service_name)
        assert current is not None
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
        stored = stack.repository.get_confirmation(runtime.runtime_confirmation.confirmation_id)
        stack.repository.update_confirmation(
            stored.model_copy(update={"state": PrivilegedConfirmationState.EXPIRED})
        )
        service = PrivilegedActionConfirmationService(stack.repository)
        with pytest.raises(PrivilegedConfirmationError):
            service.require_approved_pair(
                prepared.plan_confirmation.confirmation_id,
                runtime.runtime_confirmation.confirmation_id,
                prepared.plan,
                runtime.preview,
            )
    finally:
        stack.close()


def test_non_required_routing_never_enters_orchestration(tmp_path: Path) -> None:
    stack = build_privileged_test_stack(tmp_path / "state.db")
    try:
        current = stack.fake_service
        payload = ServiceStopPayload(
            service_identity=current.identity,
            expected_startup_configuration_digest=(
                current.startup_configuration.canonical_digest()
            ),
            expected_dependency_digest=current.dependency_digest,
        )
        resolution = required_resolution().model_copy(
            update={
                "status": PrivilegeResolutionStatus.NOT_REQUIRED,
                "requirement": PrivilegeRequirement.STANDARD_USER,
            }
        )
        with pytest.raises(RuntimeError, match="Administrator-required"):
            stack.service.prepare(
                source_plan_id=uuid4(),
                source_plan_hash="a" * 64,
                payload=payload,
                target_identity_hash=current.identity.canonical_digest(),
                object_summary="service",
                target_state_hash=current.state_digest(),
                safety_digest=current.safety_digest,
                privilege_resolution=resolution,
            )
    finally:
        stack.close()
