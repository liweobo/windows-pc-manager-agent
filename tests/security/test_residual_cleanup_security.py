"""Fail-closed Stage 4D4 boundaries against stale, mixed, and direct writes."""

from __future__ import annotations

import inspect
import json
import os
import shutil
from pathlib import Path

import pytest
from tests.fixtures.residual_cleanup import (
    build_residual_cleanup_environment,
    create_residual_report,
)
from tests.fixtures.software_residuals import residual_context

from pc_manager_agent.domain.residual_cleanup import (
    CleanupEligibilityDecision,
    ResidualCleanupRequest,
    ResidualCleanupTransactionState,
)
from pc_manager_agent.domain.software_residuals import (
    ResidualClassification,
    ResidualSource,
    UninstallMechanism,
)
from pc_manager_agent.domain.trash import RecycleBinResult, RecycleVerificationStatus
from pc_manager_agent.orchestration import residual_cleanup as cleanup_orchestration
from pc_manager_agent.safety import residual_cleanup_revalidation
from pc_manager_agent.safety.residual_cleanup_preview import ResidualCleanupPreviewError
from pc_manager_agent.tools.file_tools import recycle_executor
from pc_manager_agent.tools.registry import ToolInputError, WriteAuthorizationError
from pc_manager_agent.tools.system_tools import residual_cleanup as cleanup_tools


def test_mixed_batch_is_blocked_as_a_whole_and_never_recycles(tmp_path: Path) -> None:
    root = tmp_path / "SyntheticProduct"
    root.mkdir()
    executable = root / "app.bin"
    database = root / "user.db"
    executable.write_bytes(b"program")
    database.write_bytes(b"user-data")
    environment = build_residual_cleanup_environment(tmp_path / "state.db")
    context = residual_context(root)
    try:
        report = create_residual_report(environment, context)
        by_path = {candidate.path: candidate for candidate in report.candidates}
        assessment = environment.service.assess(
            ResidualCleanupRequest(
                source_report_id=report.report_id,
                selected_residual_ids=(
                    by_path[executable].candidate_id,
                    by_path[database].candidate_id,
                ),
            )
        )
        assert assessment.selected_count == 2
        assert any(
            item.eligibility is CleanupEligibilityDecision.ELIGIBLE for item in assessment.items
        )
        assert any(
            item.eligibility is CleanupEligibilityDecision.BLOCKED for item in assessment.items
        )
        with pytest.raises(ResidualCleanupPreviewError, match="blocked"):
            environment.service.prepare(assessment)
        assert environment.recycle.calls == []
        assert executable.exists()
        assert database.exists()
    finally:
        environment.close()


def test_material_change_after_plan_confirmation_invalidates_runtime_preview(
    tmp_path: Path,
) -> None:
    root = tmp_path / "SyntheticProduct"
    root.mkdir()
    payload = root / "app.bin"
    payload.write_bytes(b"version-one")
    environment = build_residual_cleanup_environment(tmp_path / "state.db")
    context = residual_context(root)
    try:
        report = create_residual_report(environment, context)
        candidate = next(item for item in report.candidates if item.path == payload)
        assessment = environment.service.assess(
            ResidualCleanupRequest(
                source_report_id=report.report_id,
                selected_residual_ids=(candidate.candidate_id,),
            )
        )
        prepared = environment.service.prepare(assessment)
        environment.service.resolve_plan_confirmation(prepared, True)
        payload.write_bytes(b"version-two-and-different-size")
        with pytest.raises(ResidualCleanupPreviewError, match="changed"):
            environment.service.request_runtime_confirmation(prepared)
        assert environment.cleanup_repository.state(prepared.plan.transaction_id) is (
            ResidualCleanupTransactionState.BLOCKED
        )
        assert environment.recycle.calls == []
        assert payload.read_bytes().startswith(b"version-two")
    finally:
        environment.close()


def test_final_toctou_change_blocks_consumption_and_new_object_is_not_recycled(
    tmp_path: Path,
) -> None:
    root = tmp_path / "SyntheticProduct"
    root.mkdir()
    payload = root / "app.bin"
    payload.write_bytes(b"version-one")
    environment = build_residual_cleanup_environment(tmp_path / "state.db")
    context = residual_context(root)
    try:
        report = create_residual_report(environment, context)
        candidate = next(item for item in report.candidates if item.path == payload)
        assessment = environment.service.assess(
            ResidualCleanupRequest(
                source_report_id=report.report_id,
                selected_residual_ids=(candidate.candidate_id,),
            )
        )
        prepared = environment.service.prepare(assessment)
        environment.service.resolve_plan_confirmation(prepared, True)
        runtime = environment.service.request_runtime_confirmation(prepared)
        environment.service.resolve_runtime_confirmation(runtime, True)
        payload.write_bytes(b"replacement-content-with-new-identity-evidence")
        with pytest.raises(ResidualCleanupPreviewError):
            environment.service.execute(runtime)
        assert environment.cleanup_repository.state(prepared.plan.transaction_id) is (
            ResidualCleanupTransactionState.BLOCKED
        )
        assert environment.recycle.calls == []
        assert payload.read_bytes().startswith(b"replacement")
    finally:
        environment.close()


def test_recent_child_write_after_uninstall_is_blocked(tmp_path: Path) -> None:
    root = tmp_path / "SyntheticProduct"
    root.mkdir()
    payload = root / "app.bin"
    payload.write_bytes(b"version-one")
    environment = build_residual_cleanup_environment(tmp_path / "state.db")
    context = residual_context(root)
    try:
        report = create_residual_report(environment, context)
        root_candidate = next(item for item in report.candidates if item.path == root)
        payload.write_bytes(b"post-uninstall-activity")
        assessment = environment.service.assess(
            ResidualCleanupRequest(
                source_report_id=report.report_id,
                selected_residual_ids=(root_candidate.candidate_id,),
            )
        )
        item = assessment.items[0]
        assert item.eligibility is CleanupEligibilityDecision.BLOCKED
        assert "modified-after-uninstall" in item.eligibility_reason_codes
        assert environment.recycle.calls == []
    finally:
        environment.close()


def test_shared_location_signal_and_package_user_data_are_always_blocked(
    tmp_path: Path,
) -> None:
    cases = (
        (
            "SharedProduct",
            residual_context(tmp_path / "SharedProduct").model_copy(
                update={
                    "known_paths": (
                        residual_context(tmp_path / "SharedProduct")
                        .known_paths[0]
                        .model_copy(update={"shared_location": True}),
                    )
                }
            ),
            "shared-location",
        ),
        (
            "PackageData",
            residual_context(
                tmp_path / "PackageData",
                mechanism=UninstallMechanism.MSIX,
                source=ResidualSource.MSIX_PACKAGE_DATA,
                classification=ResidualClassification.PACKAGE_USER_DATA,
            ),
            "package_user_data",
        ),
    )
    for name, context, expected_reason in cases:
        root = tmp_path / name
        root.mkdir()
        (root / "state.bin").write_bytes(b"data")
        # Rebuild the completion timestamp after creating the synthetic tree.
        context = context.model_copy(
            update={
                "uninstall_completed_at": residual_context(root).uninstall_completed_at,
            }
        )
        environment = build_residual_cleanup_environment(tmp_path / f"{name}.db")
        try:
            report = create_residual_report(environment, context)
            candidate = next(item for item in report.candidates if item.path == root)
            assessment = environment.service.assess(
                ResidualCleanupRequest(
                    source_report_id=report.report_id,
                    selected_residual_ids=(candidate.candidate_id,),
                )
            )
            item = assessment.items[0]
            assert item.eligibility is CleanupEligibilityDecision.BLOCKED
            assert any(expected_reason in reason for reason in item.eligibility_reason_codes)
            assert environment.recycle.calls == []
        finally:
            environment.close()


def test_selected_cache_does_not_widen_to_parent_or_config_sibling(tmp_path: Path) -> None:
    root = tmp_path / "SyntheticProduct"
    cache = root / "Cache"
    config = root / "Config"
    cache.mkdir(parents=True)
    config.mkdir()
    (cache / "entry.bin").write_bytes(b"cache")
    (config / "settings.ini").write_bytes(b"protected")
    environment = build_residual_cleanup_environment(tmp_path / "state.db")
    context = residual_context(root)
    try:
        report = create_residual_report(environment, context)
        candidate = next(item for item in report.candidates if item.path == cache)
        assessment = environment.service.assess(
            ResidualCleanupRequest(
                source_report_id=report.report_id,
                selected_residual_ids=(candidate.candidate_id,),
            )
        )
        prepared = environment.service.prepare(assessment)
        assert tuple(item.candidate.path for item in prepared.plan.items) == (cache,)
        assert all(item.candidate.path != root for item in prepared.plan.items)
        assert all(item.candidate.path != config for item in prepared.plan.items)
        assert config.exists()
    finally:
        environment.close()


def test_obsolete_shortcut_requires_exact_pre_uninstall_target_evidence(
    tmp_path: Path,
) -> None:
    shortcut = tmp_path / "Synthetic Product.lnk"
    shortcut.write_bytes(b"synthetic-shortcut-metadata")
    removed_target = tmp_path / "removed" / "app.exe"
    base = residual_context(
        shortcut,
        source=ResidualSource.SHORTCUT,
        classification=ResidualClassification.SHORTCUT,
    )
    context = base.model_copy(
        update={
            "known_paths": (
                base.known_paths[0].model_copy(update={"related_target_path": removed_target}),
            )
        }
    )
    environment = build_residual_cleanup_environment(tmp_path / "state.db")
    try:
        report = create_residual_report(environment, context)
        candidate = next(item for item in report.candidates if item.path == shortcut)
        assessment = environment.service.assess(
            ResidualCleanupRequest(
                source_report_id=report.report_id,
                selected_residual_ids=(candidate.candidate_id,),
            )
        )
        assert assessment.all_eligible
        item = assessment.items[0]
        assert item.classification is ResidualClassification.SHORTCUT
        assert "exact-pre-uninstall-shortcut" in item.protection_reasons

        removed_target.parent.mkdir()
        removed_target.write_bytes(b"still-installed")
        changed = environment.service.assess(
            ResidualCleanupRequest(
                source_report_id=report.report_id,
                selected_residual_ids=(candidate.candidate_id,),
            )
        )
        assert changed.items[0].eligibility is CleanupEligibilityDecision.BLOCKED
        assert "shortcut-target-still-present" in changed.items[0].eligibility_reason_codes
        assert environment.recycle.calls == []
    finally:
        environment.close()


def test_large_material_snapshot_promotes_plan_to_high_impact(tmp_path: Path) -> None:
    root = tmp_path / "SyntheticProduct"
    root.mkdir()
    for index in range(101):
        (root / f"item-{index:03d}.bin").touch()
    environment = build_residual_cleanup_environment(tmp_path / "state.db")
    context = residual_context(root)
    try:
        report = create_residual_report(environment, context)
        candidate = next(item for item in report.candidates if item.path == root)
        assessment = environment.service.assess(
            ResidualCleanupRequest(
                source_report_id=report.report_id,
                selected_residual_ids=(candidate.candidate_id,),
            )
        )
        prepared = environment.service.prepare(assessment)
        assert prepared.plan.risk_level.value == "R2_HIGH_IMPACT"
        assert prepared.plan.contained_object_count == 102
        assert environment.recycle.calls == []
    finally:
        environment.close()


@pytest.mark.parametrize("replacement_kind", ("file", "directory", "symlink"))
def test_same_path_replacement_or_reparse_never_inherits_old_report_authority(
    tmp_path: Path,
    replacement_kind: str,
) -> None:
    target = tmp_path / "SyntheticProduct.bin"
    target.write_bytes(b"original")
    environment = build_residual_cleanup_environment(tmp_path / "state.db")
    context = residual_context(target)
    try:
        report = create_residual_report(environment, context)
        candidate = next(item for item in report.candidates if item.path == target)
        original = tmp_path / "quarantine-original.bin"
        target.replace(original)
        if replacement_kind == "file":
            target.write_bytes(b"new-object")
        elif replacement_kind == "directory":
            target.mkdir()
        else:
            redirected = tmp_path / "other-user-data"
            redirected.mkdir()
            try:
                target.symlink_to(redirected, target_is_directory=True)
            except OSError:
                pytest.skip("Synthetic Windows reparse creation is unavailable")
        assessment = environment.service.assess(
            ResidualCleanupRequest(
                source_report_id=report.report_id,
                selected_residual_ids=(candidate.candidate_id,),
            )
        )
        assert assessment.items[0].eligibility is CleanupEligibilityDecision.BLOCKED
        assert "stage4d3-identity-changed" in assessment.items[0].eligibility_reason_codes
        assert environment.recycle.calls == []
        assert original.read_bytes() == b"original"
    finally:
        environment.close()


def test_synthetic_reparse_signal_blocks_before_tree_traversal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "SyntheticProduct"
    root.mkdir()
    secret = root / "must-not-be-traversed.db"
    secret.write_bytes(b"protected")
    environment = build_residual_cleanup_environment(tmp_path / "state.db")
    context = residual_context(root)
    try:
        report = create_residual_report(environment, context)
        candidate = next(item for item in report.candidates if item.path == root)
        import pc_manager_agent.safety.residual_scope_policy as scope_module

        real_is_reparse = scope_module.is_reparse_point
        monkeypatch.setattr(
            scope_module,
            "is_reparse_point",
            lambda path: path == root or real_is_reparse(path),
        )
        assessment = environment.service.assess(
            ResidualCleanupRequest(
                source_report_id=report.report_id,
                selected_residual_ids=(candidate.candidate_id,),
            )
        )
        assert assessment.items[0].eligibility is CleanupEligibilityDecision.BLOCKED
        assert environment.recycle.calls == []
        assert secret.read_bytes() == b"protected"
    finally:
        environment.close()


def test_write_tool_rejects_raw_paths_and_old_report_authority(tmp_path: Path) -> None:
    root = tmp_path / "SyntheticProduct"
    root.mkdir()
    (root / "app.bin").write_bytes(b"program")
    environment = build_residual_cleanup_environment(tmp_path / "state.db")
    context = residual_context(root)
    try:
        report = create_residual_report(environment, context)
        candidate = next(item for item in report.candidates if item.path == root)
        with pytest.raises(ToolInputError):
            environment.registry.execute(
                "software.residuals.trash",
                {
                    "source_report_id": str(report.report_id),
                    "candidate_id": str(candidate.candidate_id),
                    "path": str(root),
                },
            )
        assessment = environment.service.assess(
            ResidualCleanupRequest(
                source_report_id=report.report_id,
                selected_residual_ids=(candidate.candidate_id,),
            )
        )
        prepared = environment.service.prepare(assessment)
        request = environment.cleanup_repository.request_for_item(
            prepared.plan,
            prepared.preview.preview_id,
            prepared.plan.items[0],
        )
        with pytest.raises(WriteAuthorizationError):
            environment.registry.execute(
                "software.residuals.trash",
                request.model_dump(mode="json"),
            )
        assert environment.recycle.calls == []
        assert root.exists()
    finally:
        environment.close()


def test_cleanup_audit_never_stores_literal_local_paths(tmp_path: Path) -> None:
    root = tmp_path / "SyntheticProduct"
    root.mkdir()
    (root / "app.bin").write_bytes(b"program")
    environment = build_residual_cleanup_environment(tmp_path / "state.db")
    context = residual_context(root)
    try:
        report = create_residual_report(environment, context)
        candidate = next(item for item in report.candidates if item.path == root)
        assessment = environment.service.assess(
            ResidualCleanupRequest(
                source_report_id=report.report_id,
                selected_residual_ids=(candidate.candidate_id,),
            )
        )
        prepared = environment.service.prepare(assessment)
        environment.service.resolve_plan_confirmation(prepared, True)
        runtime = environment.service.request_runtime_confirmation(prepared)
        environment.service.resolve_runtime_confirmation(runtime, True)
        environment.service.execute(runtime)
        cleanup_rows = tuple(
            row
            for row in environment.residuals.audit.list_recent(100)
            if row.event_type.startswith("software.residuals.cleanup")
        )
        serialized = json.dumps(
            [
                {
                    "parameters": row.parameters,
                    "before": row.before_state,
                    "result": row.result,
                    "rollback": row.rollback,
                }
                for row in cleanup_rows
            ],
            ensure_ascii=False,
        )
        assert cleanup_rows
        assert str(root) not in serialized
        assert "path_digest" in serialized
    finally:
        environment.close()


@pytest.mark.parametrize(
    "forbidden",
    (
        "os.remove(",
        "os.unlink(",
        "shutil.rmtree(",
        ".unlink(",
        ".rmdir(",
        "DeleteFileW",
        "RemoveDirectoryW",
        "winreg.",
        "shell=True",
    ),
)
def test_stage4d4_production_path_has_no_permanent_or_registry_fallback(
    forbidden: str,
) -> None:
    source = "\n".join(
        (
            inspect.getsource(cleanup_orchestration),
            inspect.getsource(residual_cleanup_revalidation),
            inspect.getsource(cleanup_tools),
            inspect.getsource(recycle_executor),
        )
    )
    assert forbidden not in source


def test_recycle_failure_stops_without_permanent_fallback_or_recovery_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "SyntheticProduct"
    root.mkdir()
    (root / "app.bin").write_bytes(b"program")
    environment = build_residual_cleanup_environment(tmp_path / "state.db")
    context = residual_context(root)
    permanent_calls: list[str] = []

    def forbidden(*_args: object, **_kwargs: object) -> None:
        permanent_calls.append("permanent")
        raise AssertionError("Stage 4D4 attempted a permanent delete fallback")

    monkeypatch.setattr(os, "remove", forbidden)
    monkeypatch.setattr(os, "unlink", forbidden)
    monkeypatch.setattr(shutil, "rmtree", forbidden)
    monkeypatch.setattr(Path, "unlink", forbidden)
    monkeypatch.setattr(Path, "rmdir", forbidden)
    monkeypatch.setattr(
        environment.recycle,
        "recycle",
        lambda _path: (_ for _ in ()).throw(RuntimeError("synthetic Shell failure")),
    )
    try:
        report = create_residual_report(environment, context)
        candidate = next(item for item in report.candidates if item.path == root)
        assessment = environment.service.assess(
            ResidualCleanupRequest(
                source_report_id=report.report_id,
                selected_residual_ids=(candidate.candidate_id,),
            )
        )
        prepared = environment.service.prepare(assessment)
        environment.service.resolve_plan_confirmation(prepared, True)
        runtime = environment.service.request_runtime_confirmation(prepared)
        environment.service.resolve_runtime_confirmation(runtime, True)
        result = environment.service.execute(runtime)
        assert result.failed_count == 1
        assert result.completed_count == 0
        assert permanent_calls == []
        assert root.exists()
        assert environment.service.recovery_records(result.transaction_id) == ()
    finally:
        environment.close()


def test_inconsistent_shell_result_never_claims_verified_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "SyntheticProduct"
    root.mkdir()
    (root / "app.bin").write_bytes(b"program")
    environment = build_residual_cleanup_environment(tmp_path / "state.db")
    context = residual_context(root)
    original_recycle = environment.recycle.recycle

    def recycle_with_inconsistent_evidence(path: Path) -> RecycleBinResult:
        outcome = original_recycle(path)
        return outcome.model_copy(
            update={
                "recycled": False,
                "verification_status": RecycleVerificationStatus.UNKNOWN,
            }
        )

    monkeypatch.setattr(environment.recycle, "recycle", recycle_with_inconsistent_evidence)
    try:
        report = create_residual_report(environment, context)
        candidate = next(item for item in report.candidates if item.path == root)
        assessment = environment.service.assess(
            ResidualCleanupRequest(
                source_report_id=report.report_id,
                selected_residual_ids=(candidate.candidate_id,),
            )
        )
        prepared = environment.service.prepare(assessment)
        environment.service.resolve_plan_confirmation(prepared, True)
        runtime = environment.service.request_runtime_confirmation(prepared)
        environment.service.resolve_runtime_confirmation(runtime, True)

        result = environment.service.execute(runtime)

        assert result.failed_count == 1
        assert result.completed_count == 0
        assert environment.service.recovery_records(result.transaction_id) == ()
    finally:
        environment.close()
