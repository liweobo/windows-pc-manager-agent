"""Stage 4B domain, safety, confirmation, backup, and command tests."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest

from pc_manager_agent.confirmation.startup_actions import (
    StartupActionConfirmationService,
    StartupConfirmationError,
)
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.startup_actions import (
    RegistryStartupIdentity,
    StartupActionPlan,
    StartupActionPreview,
    StartupActionType,
    StartupBackupPayload,
    StartupBackupReference,
    StartupEntryStatus,
    StartupIdentity,
    StartupManagementMode,
    StartupObservation,
    StartupSafetyDecision,
    StartupSource,
)
from pc_manager_agent.persistence.startup_actions import StartupBackupVault, StartupStoreError
from pc_manager_agent.platform_support.startup import StartupManagementPlatform
from pc_manager_agent.rollback.startup_commands import (
    DisableStartupCommand,
    RestoreStartupCommand,
)
from pc_manager_agent.safety.startup_policy import StartupSafetyPolicy
from pc_manager_agent.safety.startup_preview import StartupPreviewEngine
from pc_manager_agent.safety.startup_validator import StartupActionSafetyValidator
from pc_manager_agent.tools.registry import ToolRegistry


class ReversibleProtector:
    """Test-only reversible protector; production uses Windows current-user DPAPI."""

    def protect(self, plaintext: bytes) -> bytes:
        return b"protected:" + plaintext[::-1]

    def unprotect(self, ciphertext: bytes) -> bytes:
        if not ciphertext.startswith(b"protected:"):
            raise ValueError("invalid ciphertext")
        return ciphertext.removeprefix(b"protected:")[::-1]


def observation(executable: Path) -> StartupObservation:
    digest = "a" * 64
    identity = StartupIdentity(
        source=StartupSource.HKCU_RUN,
        registry=RegistryStartupIdentity(
            hive="HKCU",
            key_path=r"Software\Microsoft\Windows\CurrentVersion\Run",
            value_name="Example",
            value_type=1,
            value_data_digest=digest,
            command_fingerprint=digest,
        ),
    )
    return StartupObservation(
        identity=identity,
        display_name="Example",
        publisher="Example Corporation",
        executable_path=executable,
        command_summary="example.exe (0 arguments)",
        scope="CURRENT_USER",
        status=StartupEntryStatus.ENABLED,
        status_evidence="No override",
        management_mode=StartupManagementMode.DISABLE_SUPPORTED,
    )


def payload(value: StartupObservation) -> StartupBackupPayload:
    return StartupBackupPayload(
        original_identity=value.identity,
        source=StartupSource.HKCU_RUN,
        registry_value_data_b64=base64.b64encode(b"exact-command-bytes").decode(),
        registry_value_type=1,
    )


def plan_preview(
    tmp_path: Path,
) -> tuple[
    StartupActionPlan,
    StartupActionPreview,
    StartupBackupReference,
    StartupObservation,
]:
    """Create a complete allowed plan/Preview fixture for boundary mutations."""
    executable = tmp_path / "example.exe"
    executable.write_bytes(b"MZ")
    value = observation(executable)
    exact = payload(value)
    plan = StartupActionPlan(
        user_goal="Disable Example",
        summary="Disable one startup entry",
        action=StartupActionType.DISABLE,
        target_identity=value.identity,
        target_name=value.display_name,
        expected_state_digest=value.current_state_digest(),
        backup_id=uuid4(),
        backup_digest=exact.canonical_digest(),
    )
    backup = StartupBackupReference(
        backup_id=plan.backup_id,
        identity_digest=value.identity.canonical_digest(),
        payload_digest=plan.backup_digest,
        source=value.identity.source,
        verified=True,
    )
    preview = StartupPreviewEngine(
        StartupSafetyPolicy(agent_root=tmp_path / "agent", windows_directory=tmp_path / "win")
    ).build(plan, value, backup)
    return plan, preview, backup, value


def test_policy_allows_only_known_current_user_third_party(tmp_path: Path) -> None:
    executable = tmp_path / "example.exe"
    executable.write_bytes(b"MZ")
    value = observation(executable)
    policy = StartupSafetyPolicy(agent_root=tmp_path / "agent", windows_directory=tmp_path / "win")

    allowed = policy.assess(value, StartupActionType.DISABLE)
    unknown = policy.assess(
        value.model_copy(update={"publisher": None}),
        StartupActionType.DISABLE,
    )
    machine = policy.assess(
        value.model_copy(update={"scope": "ALL_USERS"}),
        StartupActionType.DISABLE,
    )
    microsoft = policy.assess(
        value.model_copy(update={"publisher": "Microsoft Corporation"}),
        StartupActionType.DISABLE,
    )
    security = policy.assess(
        value.model_copy(
            update={"display_name": "Endpoint Security", "publisher": "Security Corp"}
        ),
        StartupActionType.DISABLE,
    )
    driver = policy.assess(
        value.model_copy(update={"display_name": "Example Driver"}),
        StartupActionType.DISABLE,
    )
    agent = policy.assess(
        value.model_copy(update={"display_name": "WindowsPCManagerAgent"}),
        StartupActionType.DISABLE,
    )

    assert allowed.decision.value == "ALLOW"
    assert unknown.decision.value == "BLOCK"
    assert machine.decision.value == "BLOCK"
    assert microsoft.decision.value == "BLOCK"
    assert security.decision.value == "BLOCK"
    assert driver.decision.value == "BLOCK"
    assert agent.decision.value == "BLOCK"


def test_identity_digest_changes_for_command_path_or_publisher(tmp_path: Path) -> None:
    executable = tmp_path / "example.exe"
    executable.write_bytes(b"MZ")
    value = observation(executable)
    changed_command = value.identity.model_copy(
        update={
            "registry": value.identity.registry.model_copy(
                update={"value_data_digest": "c" * 64, "command_fingerprint": "c" * 64}
            )
            if value.identity.registry
            else None
        }
    )
    other_executable = tmp_path / "other.exe"
    other_executable.write_bytes(b"MZ")

    assert changed_command.canonical_digest() != value.identity.canonical_digest()
    assert (
        value.model_copy(update={"executable_path": other_executable}).current_state_digest()
        != value.current_state_digest()
    )
    assert (
        value.model_copy(update={"publisher": "Changed Publisher"}).current_state_digest()
        != value.current_state_digest()
    )


def test_backup_vault_encrypts_and_verifies_exact_payload(tmp_path: Path) -> None:
    executable = tmp_path / "example.exe"
    executable.write_bytes(b"MZ")
    vault = StartupBackupVault(tmp_path / "state.db", ReversibleProtector())
    vault.initialize()
    exact = payload(observation(executable))

    reference = vault.store(exact)
    restored = vault.load(reference.backup_id, expected_digest=reference.payload_digest)

    assert reference.verified
    assert restored == exact
    assert b"exact-command-bytes" not in (tmp_path / "state.db").read_bytes()
    with pytest.raises(StartupStoreError):
        vault.load(reference.backup_id, expected_digest="0" * 64)
    vault.close()


def test_confirmation_binds_action_identity_state_and_backup(tmp_path: Path) -> None:
    executable = tmp_path / "example.exe"
    executable.write_bytes(b"MZ")
    value = observation(executable)
    exact = payload(value)
    backup_id = uuid4()
    backup_digest = exact.canonical_digest()
    plan = StartupActionPlan(
        user_goal="Disable Example",
        summary="Disable one startup entry",
        action=StartupActionType.DISABLE,
        target_identity=value.identity,
        target_name=value.display_name,
        expected_state_digest=value.current_state_digest(),
        backup_id=backup_id,
        backup_digest=backup_digest,
    )
    backup = StartupBackupReference(
        backup_id=backup_id,
        identity_digest=value.identity.canonical_digest(),
        payload_digest=backup_digest,
        source=value.identity.source,
        verified=True,
    )
    preview = StartupPreviewEngine(
        StartupSafetyPolicy(agent_root=tmp_path / "agent", windows_directory=tmp_path / "win")
    ).build(plan, value, backup)
    current = datetime.now(UTC)
    service = StartupActionConfirmationService(
        300,
        60,
        now=lambda: current,
    )

    plan_request = service.request_plan(plan, preview)
    service.resolve_plan(plan_request.confirmation_id, True, plan, preview)
    runtime = service.request_runtime(plan_request.confirmation_id, plan, preview)
    service.resolve_runtime(runtime.confirmation_id, True, plan, preview)
    consumed = service.consume_runtime(runtime.confirmation_id, plan, preview)

    assert consumed.state.value == "CONSUMED"
    with pytest.raises(StartupConfirmationError):
        service.consume_runtime(runtime.confirmation_id, plan, preview)


def test_confirmation_expires_fail_closed(tmp_path: Path) -> None:
    executable = tmp_path / "example.exe"
    executable.write_bytes(b"MZ")
    value = observation(executable)
    exact = payload(value)
    plan = StartupActionPlan(
        user_goal="Disable Example",
        summary="Disable one startup entry",
        action=StartupActionType.DISABLE,
        target_identity=value.identity,
        target_name=value.display_name,
        expected_state_digest=value.current_state_digest(),
        backup_id=uuid4(),
        backup_digest=exact.canonical_digest(),
    )
    backup = StartupBackupReference(
        backup_id=plan.backup_id,
        identity_digest=value.identity.canonical_digest(),
        payload_digest=plan.backup_digest,
        source=value.identity.source,
        verified=True,
    )
    preview = StartupPreviewEngine(
        StartupSafetyPolicy(agent_root=tmp_path / "agent", windows_directory=tmp_path / "win")
    ).build(plan, value, backup)
    times = iter((datetime.now(UTC), datetime.now(UTC) + timedelta(seconds=31)))
    service = StartupActionConfirmationService(30, 30, now=lambda: next(times))
    request = service.request_plan(plan, preview)

    with pytest.raises(StartupConfirmationError, match="expired"):
        service.resolve_plan(request.confirmation_id, True, plan, preview)


def test_preview_rejects_every_changed_binding(tmp_path: Path) -> None:
    plan, _preview, backup, value = plan_preview(tmp_path)
    engine = StartupPreviewEngine(
        StartupSafetyPolicy(agent_root=tmp_path / "agent", windows_directory=tmp_path / "win")
    )
    changed_registry = value.identity.registry.model_copy(update={"value_name": "Changed"})
    changed_identity = value.identity.model_copy(update={"registry": changed_registry})

    with pytest.raises(ValueError, match="identity"):
        engine.build(plan, value.model_copy(update={"identity": changed_identity}), backup)
    with pytest.raises(ValueError, match="backup"):
        engine.build(plan, value, backup.model_copy(update={"backup_id": uuid4()}))
    with pytest.raises(ValueError, match="state"):
        engine.build(plan, value.model_copy(update={"publisher": "Changed"}), backup)


def test_policy_blocks_path_mode_source_and_enterprise_variants(tmp_path: Path) -> None:
    executable = tmp_path / "example.exe"
    executable.write_bytes(b"MZ")
    value = observation(executable)
    windows_executable = tmp_path / "win" / "system.exe"
    windows_executable.parent.mkdir()
    windows_executable.write_bytes(b"MZ")
    policy = StartupSafetyPolicy(agent_root=tmp_path / "agent", windows_directory=tmp_path / "win")

    missing = policy.assess(
        value.model_copy(update={"executable_path": tmp_path / "missing.exe"}),
        StartupActionType.DISABLE,
    )
    system = policy.assess(
        value.model_copy(update={"executable_path": windows_executable}),
        StartupActionType.DISABLE,
    )
    enterprise = policy.assess(
        value.model_copy(update={"display_name": "Intune Helper"}),
        StartupActionType.DISABLE,
    )
    wrong_mode = policy.assess(
        value.model_copy(update={"management_mode": StartupManagementMode.READ_ONLY}),
        StartupActionType.DISABLE,
    )
    unsupported_identity = value.identity.model_copy(update={"source": StartupSource.HKLM_RUN})
    unsupported = policy.assess(
        value.model_copy(update={"identity": unsupported_identity}),
        StartupActionType.DISABLE,
    )

    assert missing.reason_codes[0].value == "UNKNOWN_EXECUTABLE"
    assert system.reason_codes[0].value == "BLOCKED_SYSTEM_COMPONENT"
    assert enterprise.reason_codes[0].value == "BLOCKED_ENTERPRISE_MANAGED"
    assert wrong_mode.reason_codes[-1].value == "ALREADY_DISABLED"
    assert unsupported.reason_codes[-1].value == "UNSUPPORTED_SOURCE"


def test_validator_reports_all_independent_binding_failures(tmp_path: Path) -> None:
    plan, preview, _backup, _value = plan_preview(tmp_path)
    missing_tool = StartupActionSafetyValidator(ToolRegistry()).review(plan, preview)
    assert not missing_tool.approved
    assert "not registered" in missing_tool.issues[0]

    manifest = SimpleNamespace(
        risk_level=RiskLevel.R0,
        rollback_level=RollbackLevel.NONE,
        max_batch_size=2,
    )
    registry = cast(ToolRegistry, SimpleNamespace(manifest=lambda _name: manifest))
    validator = StartupActionSafetyValidator(registry)
    changed_plan = plan.model_copy(update={"summary": "Changed summary"})
    blocked_assessment = preview.assessment.model_copy(
        update={
            "decision": StartupSafetyDecision.BLOCK,
            "explanation": "Blocked test entry",
        }
    )
    changed_preview = preview.model_copy(
        update={
            "plan_id": uuid4(),
            "transaction_id": uuid4(),
            "plan_digest": "0" * 64,
            "action": StartupActionType.RESTORE,
            "current_state_digest": "1" * 64,
            "backup_id": uuid4(),
            "backup_digest": "2" * 64,
            "backup_verified": False,
            "assessment": blocked_assessment,
        }
    )
    review = validator.review(changed_plan, changed_preview)

    assert not review.approved
    assert len(review.issues) >= 8


def test_confirmation_rejects_invalid_tiers_rejection_and_changed_preview(
    tmp_path: Path,
) -> None:
    plan, preview, _backup, _value = plan_preview(tmp_path)
    with pytest.raises(ValueError, match="positive"):
        StartupActionConfirmationService(0, 60)
    service = StartupActionConfirmationService()

    with pytest.raises(StartupConfirmationError, match="Unknown"):
        service.resolve_plan(uuid4(), True, plan, preview)
    plan_request = service.request_plan(plan, preview)
    with pytest.raises(StartupConfirmationError, match="same-action"):
        service.request_runtime(plan_request.confirmation_id, plan, preview)
    rejected = service.resolve_plan(plan_request.confirmation_id, False, plan, preview)
    assert rejected.state.value == "REJECTED"
    with pytest.raises(StartupConfirmationError, match="already resolved"):
        service.resolve_plan(plan_request.confirmation_id, True, plan, preview)

    approved_request = service.request_plan(plan, preview)
    service.resolve_plan(approved_request.confirmation_id, True, plan, preview)
    changed_preview = preview.model_copy(update={"preview_id": uuid4()})
    runtime = service.request_runtime(approved_request.confirmation_id, plan, changed_preview)
    service.resolve_runtime(runtime.confirmation_id, True, plan, changed_preview)
    with pytest.raises(StartupConfirmationError, match="changed"):
        service.consume_runtime(runtime.confirmation_id, plan, preview)


class CommandPlatform:
    """Minimal mutable platform used to cover explicit command rollbacks."""

    def __init__(self, value: StartupObservation) -> None:
        self.value = value
        self.active = True

    def disable(self, _payload: StartupBackupPayload) -> None:
        self.active = False

    def restore(self, _payload: StartupBackupPayload) -> None:
        self.active = True

    def inspect(self, _identity: StartupIdentity) -> StartupObservation | None:
        return self.value if self.active else None

    def disabled_material_matches(self, _payload: StartupBackupPayload) -> bool:
        return not self.active


def test_startup_commands_execute_verify_and_rollback(tmp_path: Path) -> None:
    executable = tmp_path / "example.exe"
    executable.write_bytes(b"MZ")
    value = observation(executable)
    exact = payload(value)
    platform = CommandPlatform(value)
    disable = DisableStartupCommand(cast(StartupManagementPlatform, platform), exact)

    disable.execute()
    assert disable.verify()
    assert disable.rollback()

    platform.active = False
    restore = RestoreStartupCommand(cast(StartupManagementPlatform, platform), exact)
    restore.execute()
    assert restore.verify()
    assert restore.rollback()
