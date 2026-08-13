"""Complete Stage 4B workflow using fakes so tests never mutate real startup state."""

from __future__ import annotations

import base64
from pathlib import Path
from uuid import UUID

import pytest

from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.audit.startup_actions import StartupActionAuditLogger
from pc_manager_agent.confirmation.startup_actions import StartupActionConfirmationService
from pc_manager_agent.domain.startup_actions import (
    RegistryStartupIdentity,
    StartupBackupPayload,
    StartupEntryStatus,
    StartupIdentity,
    StartupManagementMode,
    StartupObservation,
    StartupSource,
)
from pc_manager_agent.domain.startup_errors import StartupActionError
from pc_manager_agent.orchestration.startup_actions import StartupActionService
from pc_manager_agent.orchestration.startup_target_resolver import StartupTargetResolver
from pc_manager_agent.persistence.startup_actions import (
    StartupActionRepository,
    StartupBackupVault,
    StartupExecutionGuard,
)
from pc_manager_agent.safety.startup_policy import StartupSafetyPolicy
from pc_manager_agent.safety.startup_preview import StartupPreviewEngine
from pc_manager_agent.safety.startup_validator import StartupActionSafetyValidator
from pc_manager_agent.tools.registry import ToolRegistry
from pc_manager_agent.tools.system_tools.startup_actions import (
    DisableStartupTool,
    RestoreStartupTool,
)


class Protector:
    """Reversible test protector that proves vault encryption is injectable."""

    def protect(self, plaintext: bytes) -> bytes:
        return b"v1:" + plaintext[::-1]

    def unprotect(self, ciphertext: bytes) -> bytes:
        if not ciphertext.startswith(b"v1:"):
            raise ValueError("corrupt")
        return ciphertext[3:][::-1]


class FakeStartupPlatform:
    """Single-entry state machine implementing the narrow platform contract."""

    def __init__(self, value: StartupObservation) -> None:
        self.value = value
        self.active = True
        self.disabled_exact = False

    def list_entries(self, max_items: int = 5_000) -> tuple[StartupObservation, ...]:
        return (self.value,) if self.active and max_items else ()

    def inspect(self, identity: StartupIdentity) -> StartupObservation | None:
        if identity.canonical_digest() != self.value.identity.canonical_digest():
            raise ValueError("identity mismatch")
        return self.value if self.active else None

    def capture_backup(
        self,
        identity: StartupIdentity,
        backup_id: UUID,
    ) -> StartupBackupPayload:
        assert backup_id
        assert self.inspect(identity) is not None
        return StartupBackupPayload(
            original_identity=identity,
            source=StartupSource.HKCU_RUN,
            registry_value_data_b64=base64.b64encode(b"exact-registry-data").decode(),
            registry_value_type=1,
        )

    def disable(self, payload: StartupBackupPayload) -> None:
        assert payload.original_identity == self.value.identity
        if not self.active:
            raise FileNotFoundError
        self.active = False
        self.disabled_exact = True

    def restore(self, payload: StartupBackupPayload) -> None:
        assert payload.original_identity == self.value.identity
        if self.active or not self.disabled_exact:
            raise FileExistsError
        self.active = True
        self.disabled_exact = False

    def disabled_material_matches(self, payload: StartupBackupPayload) -> bool:
        return (
            payload.original_identity == self.value.identity
            and not self.active
            and self.disabled_exact
        )


def _observation(tmp_path: Path) -> StartupObservation:
    executable = tmp_path / "example.exe"
    executable.write_bytes(b"MZ")
    digest = "b" * 64
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
        command_summary="example.exe (arguments withheld)",
        scope="CURRENT_USER",
        status=StartupEntryStatus.ENABLED,
        status_evidence="No StartupApproved override",
        management_mode=StartupManagementMode.DISABLE_SUPPORTED,
    )


class Harness:
    """Own integration resources so every SQLite engine is deterministically disposed."""

    def __init__(
        self,
        service: StartupActionService,
        platform: FakeStartupPlatform,
        repository: StartupActionRepository,
        vault: StartupBackupVault,
        audit: AuditRepository,
    ) -> None:
        self.service = service
        self.platform = platform
        self.repository = repository
        self.vault = vault
        self.audit = audit

    def close(self) -> None:
        self.vault.close()
        self.repository.close()
        self.audit.close()


def _service(tmp_path: Path) -> Harness:
    value = _observation(tmp_path)
    platform = FakeStartupPlatform(value)
    database = tmp_path / "state.db"
    repository = StartupActionRepository(database)
    repository.initialize()
    vault = StartupBackupVault(database, Protector())
    vault.initialize()
    audit_repository = AuditRepository(database)
    audit_repository.initialize()
    guard = StartupExecutionGuard(repository)
    registry = ToolRegistry(write_guard=guard)
    registry.register(DisableStartupTool(platform, vault, repository))
    registry.register(RestoreStartupTool(platform, vault, repository))
    policy = StartupSafetyPolicy(
        agent_root=tmp_path / "agent",
        windows_directory=tmp_path / "windows",
    )
    service = StartupActionService(
        platform,
        StartupTargetResolver(platform),
        policy,
        StartupPreviewEngine(policy),
        StartupActionSafetyValidator(registry),
        StartupActionConfirmationService(300, 60),
        vault,
        repository,
        registry,
        StartupActionAuditLogger(
            audit_repository,
            app_version="test",
            git_commit=None,
        ),
    )
    return Harness(service, platform, repository, vault, audit_repository)


def _confirm_execute(service: StartupActionService, plan, preview):  # type: ignore[no-untyped-def]
    first = service.request_plan_confirmation(plan, preview)
    service.resolve_plan_confirmation(first.confirmation_id, True, plan, preview)
    current_preview, runtime = service.request_runtime_confirmation(first.confirmation_id, plan)
    service.resolve_runtime_confirmation(
        runtime.confirmation_id,
        True,
        plan,
        current_preview,
    )
    return service.execute(
        first.confirmation_id,
        runtime.confirmation_id,
        plan,
        current_preview,
    )


def test_disable_and_restore_complete_with_exact_backup(tmp_path: Path) -> None:
    harness = _service(tmp_path)
    service, platform = harness.service, harness.platform
    current = platform.value

    disable_plan, disable_preview, disable_review = service.prepare_disable(
        "Disable Example at startup",
        current.identity,
    )
    assert disable_review.approved
    disabled = _confirm_execute(service, disable_plan, disable_preview)
    assert disabled.verified
    assert not platform.active
    assert len(service.list_disabled()) == 1

    restore_plan, restore_preview, restore_review = service.prepare_restore(
        "Restore Example startup entry",
        disable_plan.backup_id,
    )
    assert restore_review.approved
    restored = _confirm_execute(service, restore_plan, restore_preview)
    assert restored.verified
    assert platform.active
    assert service.list_disabled() == ()
    harness.close()


def test_state_change_after_preview_blocks_before_mutation(tmp_path: Path) -> None:
    harness = _service(tmp_path)
    service, platform = harness.service, harness.platform
    plan, preview, review = service.prepare_disable(
        "Disable Example",
        platform.value.identity,
    )
    assert review.approved
    first = service.request_plan_confirmation(plan, preview)
    service.resolve_plan_confirmation(first.confirmation_id, True, plan, preview)
    platform.active = False

    with pytest.raises(StartupActionError):
        service.request_runtime_confirmation(first.confirmation_id, plan)
    assert not platform.disabled_exact
    harness.close()


def test_rejected_confirmation_never_mutates(tmp_path: Path) -> None:
    harness = _service(tmp_path)
    service, platform = harness.service, harness.platform
    plan, preview, _review = service.prepare_disable(
        "Disable Example",
        platform.value.identity,
    )
    first = service.request_plan_confirmation(plan, preview)
    service.resolve_plan_confirmation(first.confirmation_id, False, plan, preview)

    assert platform.active
    harness.close()
