"""Unit tests for Stage 4C2 models, policy, confirmation, backup, and commands."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from pc_manager_agent.confirmation.service_startup_actions import (
    ServiceStartupActionConfirmationService,
    ServiceStartupConfirmationError,
    ServiceStartupConfirmationState,
    ServiceStartupConfirmationTier,
)
from pc_manager_agent.domain.service_actions import (
    ServiceStartupConfiguration,
    ServiceStartupType,
    ServiceState,
)
from pc_manager_agent.domain.service_startup_actions import (
    ServiceStartupActionPlan,
    ServiceStartupActionRequest,
    ServiceStartupActionType,
    ServiceStartupBackupPayload,
    ServiceStartupBackupReference,
    ServiceStartupPermissionEvidence,
)
from pc_manager_agent.persistence.service_startup_actions import ServiceStartupBackupVault
from pc_manager_agent.rollback.service_startup_commands import (
    ServiceStartupConfigurationCommand,
)
from pc_manager_agent.safety.service_policy import ServiceSafetyPolicy
from pc_manager_agent.safety.service_startup_policy import (
    ServiceStartupSafetyPolicy,
    build_service_startup_impact,
)
from pc_manager_agent.safety.service_startup_preview import ServiceStartupPreviewEngine
from pc_manager_agent.safety.service_startup_validator import ServiceStartupSafetyValidator
from pc_manager_agent.tools.manifest import CancellationToken
from tests.stage4c1_support import FakeServicePlatform, service_observation
from tests.stage4c2_support import FakeProtector, FakeServiceStartupPlatform, configuration


def _plan_and_preview(
    tmp_path: Path,
    request: pytest.FixtureRequest,
):  # type: ignore[no-untyped-def]
    binary = tmp_path / "vendor.exe"
    binary.write_bytes(b"test")
    observation = service_observation(binary, start_type=2)
    target = configuration(ServiceStartupType.MANUAL)
    permissions = ServiceStartupPermissionEvidence(
        can_query_configuration=True,
        can_change_configuration=True,
        process_elevated=False,
    )
    payload = ServiceStartupBackupPayload(
        stable_identity=observation.identity,
        display_name=observation.display_name,
        original_configuration=observation.startup_configuration,
        original_runtime_state=observation.state,
    )
    vault = ServiceStartupBackupVault(tmp_path / "state.db", FakeProtector())
    vault.initialize()
    request.addfinalizer(vault.close)
    backup = vault.store(payload)
    impact = build_service_startup_impact(observation)
    plan = ServiceStartupActionPlan(
        user_goal="set manual",
        summary="set one service manual",
        action=ServiceStartupActionType.SET_MANUAL,
        target_identity=observation.identity,
        display_name=observation.display_name,
        source_configuration=observation.startup_configuration,
        target_configuration=target,
        expected_runtime_state=observation.state,
        expected_state_digest=observation.state_digest(),
        expected_impact_digest=impact.canonical_digest(),
        expected_permission_digest=permissions.canonical_digest(),
        backup_id=backup.backup_id,
        backup_digest=backup.payload_digest,
    )
    base = ServiceSafetyPolicy(
        current_username=r"DESKTOP\alice",
        agent_root=tmp_path / "agent",
        windows_directory=tmp_path / "Windows",
    )
    preview = ServiceStartupPreviewEngine(ServiceStartupSafetyPolicy(base)).build(
        plan, observation, target, permissions, backup
    )
    return plan, preview, vault


def test_stable_identity_digest_excludes_startup_configuration(tmp_path: Path) -> None:
    binary = tmp_path / "vendor.exe"
    binary.write_bytes(b"test")
    before = service_observation(binary, start_type=2)
    after = before.model_copy(
        update={"startup_configuration": configuration(ServiceStartupType.MANUAL)}
    )
    assert before.identity.canonical_digest() == after.identity.canonical_digest()
    assert before.configuration_digest() != after.configuration_digest()


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (ServiceStartupType.DISABLED, ServiceStartupType.MANUAL),
        (ServiceStartupType.AUTOMATIC_DELAYED, ServiceStartupType.MANUAL),
        (ServiceStartupType.MANUAL, ServiceStartupType.DISABLED),
        (ServiceStartupType.BOOT, ServiceStartupType.MANUAL),
    ],
)
def test_plan_rejects_unsupported_transition(
    tmp_path: Path,
    source: ServiceStartupType,
    target: ServiceStartupType,
) -> None:
    binary = tmp_path / "vendor.exe"
    binary.write_bytes(b"test")
    observation = service_observation(binary)
    source_config = ServiceStartupConfiguration(
        startup_type=source,
        delayed_auto_start=source is ServiceStartupType.AUTOMATIC_DELAYED,
    )
    with pytest.raises(ValidationError):
        ServiceStartupActionPlan(
            user_goal="change",
            summary="change",
            action=ServiceStartupActionType.SET_MANUAL,
            target_identity=observation.identity,
            display_name=observation.display_name,
            source_configuration=source_config,
            target_configuration=ServiceStartupConfiguration(
                startup_type=target,
                delayed_auto_start=target is ServiceStartupType.AUTOMATIC_DELAYED,
            ),
            expected_runtime_state=ServiceState.RUNNING,
            expected_state_digest="a" * 64,
            expected_impact_digest="b" * 64,
            expected_permission_digest="c" * 64,
            backup_id=uuid4(),
            backup_digest="d" * 64,
        )


def test_backup_vault_round_trip_is_verified(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    plan, _preview, vault = _plan_and_preview(tmp_path, request)
    payload = vault.load(plan.backup_id, expected_digest=plan.backup_digest)
    assert payload.stable_identity == plan.target_identity
    assert payload.original_configuration == plan.source_configuration


def test_confirmation_is_bound_and_one_time(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    plan, preview, _vault = _plan_and_preview(tmp_path, request)
    service = ServiceStartupActionConfirmationService()
    plan_request = service.request_plan(plan, preview)
    service.resolve_plan(plan_request.confirmation_id, True, plan, preview)
    runtime = service.request_runtime(plan_request.confirmation_id, plan, preview)
    service.resolve_runtime(runtime.confirmation_id, True, plan, preview)
    consumed = service.consume_runtime(runtime.confirmation_id, plan, preview)
    assert consumed.state.value == "CONSUMED"
    with pytest.raises(ServiceStartupConfirmationError):
        service.consume_runtime(runtime.confirmation_id, plan, preview)


def test_permission_denial_makes_preview_non_executable(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    plan, preview, _vault = _plan_and_preview(tmp_path, request)
    denied = preview.model_copy(
        update={
            "permissions": ServiceStartupPermissionEvidence(
                can_query_configuration=True,
                can_change_configuration=False,
                process_elevated=False,
            )
        }
    )
    assert plan.risk_level.value == "R2"
    assert not denied.executable


def test_backup_reference_cannot_claim_unverified_execution(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    plan, preview, _vault = _plan_and_preview(tmp_path, request)
    unverified = ServiceStartupBackupReference(
        backup_id=plan.backup_id,
        identity_digest=plan.target_identity.canonical_digest(),
        payload_digest=plan.backup_digest,
        verified=False,
    )
    assert not preview.model_copy(update={"backup_verified": unverified.verified}).executable


def test_confirmation_rejects_invalid_ttl_and_unapproved_parent(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    with pytest.raises(ValueError):
        ServiceStartupActionConfirmationService(plan_ttl_seconds=0)
    plan, preview, _vault = _plan_and_preview(tmp_path, request)
    service = ServiceStartupActionConfirmationService()
    pending = service.request_plan(plan, preview)
    with pytest.raises(ServiceStartupConfirmationError, match="not approved"):
        service.request_runtime(pending.confirmation_id, plan, preview)


def test_confirmation_rejects_wrong_tiers_and_states(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    plan, preview, _vault = _plan_and_preview(tmp_path, request)
    service = ServiceStartupActionConfirmationService()
    pending_plan = service.request_plan(plan, preview)
    with pytest.raises(ServiceStartupConfirmationError, match="runtime confirmation"):
        service.resolve_runtime(pending_plan.confirmation_id, True, plan, preview)
    approved_plan = service.resolve_plan(pending_plan.confirmation_id, True, plan, preview)
    with pytest.raises(ServiceStartupConfirmationError, match="Only runtime"):
        service.consume_runtime(approved_plan.confirmation_id, plan, preview)
    runtime = service.request_runtime(approved_plan.confirmation_id, plan, preview)
    with pytest.raises(ServiceStartupConfirmationError, match="not approved"):
        service.consume_runtime(runtime.confirmation_id, plan, preview)
    with pytest.raises(ServiceStartupConfirmationError, match="plan confirmation"):
        service.resolve_plan(runtime.confirmation_id, True, plan, preview)
    with pytest.raises(ServiceStartupConfirmationError, match="Unknown runtime"):
        service.consume_runtime(uuid4(), plan, preview)

    invalid_parent = approved_plan.model_copy(
        update={"tier": ServiceStartupConfirmationTier.RUNTIME}
    )
    service._requests[invalid_parent.confirmation_id] = invalid_parent
    with pytest.raises(ServiceStartupConfirmationError, match="parent is invalid"):
        service.request_runtime(invalid_parent.confirmation_id, plan, preview)


def test_confirmation_rejects_expiry_replay_and_binding_drift(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    plan, preview, _vault = _plan_and_preview(tmp_path, request)
    current = [datetime(2026, 1, 1, tzinfo=UTC)]
    service = ServiceStartupActionConfirmationService(
        plan_ttl_seconds=1,
        runtime_ttl_seconds=1,
        now=lambda: current[0],
    )
    expiring = service.request_plan(plan, preview)
    current[0] += timedelta(seconds=2)
    with pytest.raises(ServiceStartupConfirmationError, match="expired"):
        service.resolve_plan(expiring.confirmation_id, True, plan, preview)
    assert service._requests[expiring.confirmation_id].state is (
        ServiceStartupConfirmationState.EXPIRED
    )

    fresh = ServiceStartupActionConfirmationService()
    pending = fresh.request_plan(plan, preview)
    with pytest.raises(ServiceStartupConfirmationError, match="Unknown"):
        fresh.resolve_plan(uuid4(), True, plan, preview)
    with pytest.raises(ServiceStartupConfirmationError, match="Plan or configuration"):
        fresh.resolve_plan(
            pending.confirmation_id,
            True,
            plan.model_copy(update={"summary": "changed summary"}),
            preview,
        )
    changed_preview = preview.model_copy(update={"preview_id": uuid4()})
    with pytest.raises(ServiceStartupConfirmationError, match="Preview binding"):
        fresh.resolve_plan(pending.confirmation_id, True, plan, changed_preview)
    changed_permissions = preview.model_copy(
        update={
            "permissions": preview.permissions.model_copy(
                update={"can_change_configuration": False}
            )
        }
    )
    fresh._requests[pending.confirmation_id] = pending.model_copy(
        update={
            "preview_id": changed_permissions.preview_id,
            "preview_digest": changed_permissions.canonical_digest(),
        }
    )
    with pytest.raises(ServiceStartupConfirmationError, match="evidence changed"):
        fresh.resolve_plan(pending.confirmation_id, True, plan, changed_permissions)
    fresh._requests[pending.confirmation_id] = pending
    rejected = fresh.resolve_plan(pending.confirmation_id, False, plan, preview)
    assert rejected.state is ServiceStartupConfirmationState.REJECTED
    with pytest.raises(ServiceStartupConfirmationError, match="no longer pending"):
        fresh.resolve_plan(pending.confirmation_id, True, plan, preview)
    with pytest.raises(ServiceStartupConfirmationError, match="executable matching"):
        fresh.request_plan(plan, preview.model_copy(update={"backup_verified": False}))


def test_validator_reports_every_changed_binding(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    plan, preview, _vault = _plan_and_preview(tmp_path, request)
    valid_review = ServiceStartupSafetyValidator().review(plan, preview)
    assert valid_review.approved
    assert not valid_review.issues
    changed_identity = preview.observation.identity.model_copy(
        update={"service_account": r"DESKTOP\mallory"}
    )
    changed_observation = preview.observation.model_copy(
        update={
            "identity": changed_identity,
            "startup_configuration": configuration(ServiceStartupType.MANUAL),
        }
    )
    changed_preview = preview.model_copy(
        update={
            "plan_id": uuid4(),
            "transaction_id": uuid4(),
            "plan_digest": "f" * 64,
            "observation": changed_observation,
            "target_configuration": configuration(ServiceStartupType.AUTOMATIC),
            "current_state_digest": "e" * 64,
            "impact": preview.impact.model_copy(update={"dependent_names": ("Other",)}),
            "permissions": preview.permissions.model_copy(
                update={"can_change_configuration": False}
            ),
            "backup_id": uuid4(),
            "backup_digest": "0" * 64,
            "backup_verified": False,
            "safety": preview.safety.model_copy(
                update={"allowed": False, "explanation": "policy blocked"}
            ),
        }
    )
    review = ServiceStartupSafetyValidator().review(plan, changed_preview)
    assert not review.approved
    assert len(review.issues) == 13


def test_configuration_command_builds_undo_and_requires_exact_reverse(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    plan, preview, _vault = _plan_and_preview(tmp_path, request)
    control = FakeServicePlatform(preview.observation)
    platform = FakeServiceStartupPlatform(control)
    tool_request = ServiceStartupActionRequest(
        action=plan.action,
        identity=plan.target_identity,
        expected_source_configuration=plan.source_configuration,
        target_configuration=plan.target_configuration,
        expected_runtime_state=plan.expected_runtime_state,
        expected_impact_digest=plan.expected_impact_digest,
        backup_id=plan.backup_id,
        backup_digest=plan.backup_digest,
    )
    command = ServiceStartupConfigurationCommand(platform, tool_request, CancellationToken())
    assert not command.verify()
    with pytest.raises(RuntimeError):
        command.build_undo_record()
    assert command.execute().verified
    undo = command.build_undo_record()
    assert undo.restore_configuration == plan.source_configuration
    reverse = tool_request.model_copy(
        update={
            "action": ServiceStartupActionType.RESTORE,
            "expected_source_configuration": plan.target_configuration,
            "target_configuration": plan.source_configuration,
            "expected_impact_digest": build_service_startup_impact(
                control.observation
            ).canonical_digest(),
        }
    )
    assert command.rollback(reverse, CancellationToken()).verified
    with pytest.raises(ValueError, match="independently authorized"):
        command.rollback(tool_request, CancellationToken())
    with pytest.raises(ValueError, match="exact reverse"):
        command.rollback(
            reverse.model_copy(update={"target_configuration": plan.target_configuration}),
            CancellationToken(),
        )


def test_configuration_command_dispatches_automatic(tmp_path: Path) -> None:
    binary = tmp_path / "manual-vendor.exe"
    binary.write_bytes(b"test")
    observation = service_observation(binary, start_type=3)
    control = FakeServicePlatform(observation)
    platform = FakeServiceStartupPlatform(control)
    request = ServiceStartupActionRequest(
        action=ServiceStartupActionType.SET_AUTOMATIC,
        identity=observation.identity,
        expected_source_configuration=observation.startup_configuration,
        target_configuration=configuration(ServiceStartupType.AUTOMATIC),
        expected_runtime_state=observation.state,
        expected_impact_digest=build_service_startup_impact(observation).canonical_digest(),
        backup_id=uuid4(),
        backup_digest="a" * 64,
    )
    result = ServiceStartupConfigurationCommand(
        platform,
        request,
        CancellationToken(),
    ).execute()
    assert result.verified
    assert result.after_configuration.startup_type is ServiceStartupType.AUTOMATIC
