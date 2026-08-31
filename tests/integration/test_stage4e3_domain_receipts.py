"""Read actual domain journals produced with synthetic, non-destructive adapters."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update
from tests.fixtures.msi_uninstall import build_msi_environment
from tests.fixtures.vendor_uninstall import build_vendor_environment
from tests.fixtures.winget_uninstall import build_winget_environment
from tests.integration.test_msix_uninstall_workflow import (
    test_full_msix_workflow_is_twice_confirmed_and_freshly_verified as run_msix_workflow,
)
from tests.integration.test_process_action_flow import (
    test_graceful_flow_requires_both_confirmations_and_audits_result as run_process_workflow,
)
from tests.integration.test_residual_cleanup_workflow import (
    test_fresh_double_confirmed_cleanup_moves_to_synthetic_recycle_bin as run_residual_workflow,
)
from tests.integration.test_startup_action_workflow import _confirm_execute, _service
from tests.integration.test_system_cleanup_workflow import (
    test_fresh_double_confirmed_cleanup_moves_only_selected_old_item as run_cleanup_workflow,
)
from tests.integration.test_system_cleanup_workflow import (
    test_recycle_bin_empty_is_independent_twice_confirmed_and_irreversible as run_empty_workflow,
)

from pc_manager_agent.domain.optimization_actions import OptimizationOutcomeType
from pc_manager_agent.domain.optimization_receipts import (
    OptimizationReceiptKind,
    OptimizationTransactionReference,
)
from pc_manager_agent.domain.risk import RollbackLevel
from pc_manager_agent.domain.software_uninstall_analysis import SoftwareTargetQuery
from pc_manager_agent.persistence.optimization_receipts import (
    OptimizationDomainResultReader,
    _tables,
)
from pc_manager_agent.safety.optimization_actions import OptimizationRoutingError


@pytest.mark.parametrize(
    "kind,workflow,filename,attack",
    [
        (OptimizationReceiptKind.PROCESS, run_process_workflow, "actions.db", "identity"),
        (OptimizationReceiptKind.PROCESS, run_process_workflow, "actions.db", "missing"),
        (OptimizationReceiptKind.PROCESS, run_process_workflow, "actions.db", "already_exited"),
        (OptimizationReceiptKind.CLEANUP, run_cleanup_workflow, "state.db", "operation_id"),
        (OptimizationReceiptKind.CLEANUP, run_cleanup_workflow, "state.db", "sequence"),
        (OptimizationReceiptKind.CLEANUP, run_cleanup_workflow, "state.db", "missing"),
        (OptimizationReceiptKind.CLEANUP, run_cleanup_workflow, "state.db", "count"),
        (OptimizationReceiptKind.RESIDUAL, run_residual_workflow, "state.db", "item_ref"),
        (
            OptimizationReceiptKind.RESIDUAL,
            run_residual_workflow,
            "state.db",
            "source_candidate_id",
        ),
        (OptimizationReceiptKind.MSIX, run_msix_workflow, "state.sqlite3", "identity"),
        (OptimizationReceiptKind.MSIX, run_msix_workflow, "state.sqlite3", "missing"),
        (OptimizationReceiptKind.RECYCLE_BIN, run_empty_workflow, "state.db", "identity"),
        (OptimizationReceiptKind.RECYCLE_BIN, run_empty_workflow, "state.db", "missing"),
    ],
)
def test_receipt_rejects_another_objects_success(tmp_path, kind, workflow, filename, attack):
    from pc_manager_agent.persistence.database import create_sqlite_engine
    from pc_manager_agent.persistence.residual_cleanup import ResidualCleanupItemRow
    from pc_manager_agent.persistence.system_cleanup import SystemCleanupItemRow

    workflow(tmp_path)
    engine = create_sqlite_engine(tmp_path / filename)
    reader = OptimizationDomainResultReader(tmp_path / filename)
    try:
        table, _confirmation = _tables(kind)
        with engine.begin() as db:
            row = db.execute(select(table)).mappings().one()
            reference = OptimizationTransactionReference(
                kind=kind, transaction_id=UUID(row["transaction_id"])
            )
            if kind in {OptimizationReceiptKind.CLEANUP, OptimizationReceiptKind.RESIDUAL}:
                if attack == "count":
                    plan = dict(row["plan_payload"])
                    plan["total_items"] += 1
                    db.execute(update(table).values(plan_payload=plan))
                else:
                    items = (
                        SystemCleanupItemRow
                        if kind is OptimizationReceiptKind.CLEANUP
                        else ResidualCleanupItemRow
                    )
                    item = db.execute(select(items.__table__)).mappings().one()
                    payload = dict(item["result_payload"])
                    if attack != "missing":
                        payload[attack] = (
                            payload[attack] + 1 if attack == "sequence" else str(uuid4())
                        )
                    db.execute(
                        update(items).values(
                            result_payload=None if attack == "missing" else payload
                        )
                    )
            else:
                column = (
                    "result_payload" if kind is OptimizationReceiptKind.RECYCLE_BIN else "result"
                )
                payload = dict(row[column])
                if kind is OptimizationReceiptKind.PROCESS:
                    payload["members"] = [dict(item) for item in payload["members"]]
                    for item in payload["members"]:
                        if attack == "already_exited":
                            item["state"] = "ALREADY_EXITED"
                        else:
                            item["identity_digest"] = "e" * 64
                else:
                    payload["transaction_id"] = str(uuid4())
                db.execute(update(table).values({column: None if attack == "missing" else payload}))
        result = reader.read(reference)
        assert result.outcome is (
            OptimizationOutcomeType.NO_LONGER_APPLICABLE
            if attack == "already_exited"
            else OptimizationOutcomeType.APPLIED_UNVERIFIED
        )
    finally:
        reader.close()
        engine.dispose()


def test_startup_result_identity_must_match_reserved_target(tmp_path):
    harness = _service(tmp_path)
    reader = OptimizationDomainResultReader(tmp_path / "state.db")
    try:
        plan, preview, review = harness.service.prepare_disable(
            "review startup", harness.platform.value.identity
        )
        assert review.approved
        _confirm_execute(harness.service, plan, preview)
        table, _confirmation = _tables(OptimizationReceiptKind.STARTUP)
        with harness.repository._sessions.begin() as db:
            row = db.execute(select(table)).mappings().one()
            payload = dict(row["result"])
            payload["identity_digest"] = "e" * 64
            db.execute(update(table).values(result=payload))
        result = reader.read(
            OptimizationTransactionReference(
                kind=OptimizationReceiptKind.STARTUP, transaction_id=plan.transaction_id
            )
        )
        assert result.outcome is OptimizationOutcomeType.APPLIED_UNVERIFIED
    finally:
        reader.close()
        harness.close()


def test_personal_file_receipt_preserves_conditional_full_undo(runtime, tmp_path):
    from pc_manager_agent.domain.file_operations import RenameRule, RenameRuleType

    root = tmp_path / "authorized-receipt"
    root.mkdir()
    source = root / "test.txt"
    source.write_bytes(b"synthetic file")
    record = runtime.authorized_paths.add_authorized(root)
    services = runtime.create_file_operation_services()
    plan = services.compiler.compile_selected_rename(
        "rename selected fixture",
        (source,),
        RenameRule(rule_type=RenameRuleType.SEQUENCE, value="item_", width=2),
        (record.path_id,),
    )
    prepared = services.service.prepare(plan)
    reader = runtime.optimization_result_reader
    reference = OptimizationTransactionReference(
        kind=OptimizationReceiptKind.FILES, transaction_id=prepared.preview.transaction_id
    )
    assert reader.read(reference).outcome is None
    services.service.resolve_confirmation(prepared, True)
    services.service.execute(prepared)
    result = reader.read(reference)
    assert result.outcome is OptimizationOutcomeType.APPLIED_VERIFIED
    assert result.recovery is RollbackLevel.FULL
    with pytest.raises(OptimizationRoutingError, match="MECHANISM"):
        reader.read(reference.model_copy(update={"kind": OptimizationReceiptKind.PERSONAL_TRASH}))


def test_personal_trash_receipt_is_manual_recovery(runtime, monkeypatch, tmp_path):
    from tests.integration.test_trash_flow import (
        test_trash_flow_requires_both_confirmations_and_persists_manual_recovery as run_trash,
    )

    run_trash(runtime, monkeypatch, tmp_path)
    table, _confirmations = _tables(OptimizationReceiptKind.PERSONAL_TRASH)
    reader = runtime.optimization_result_reader
    with reader._engine.connect() as connection:
        identifier = connection.execute(select(table.c.transaction_id)).scalar_one()
    result = reader.read(
        OptimizationTransactionReference(
            kind=OptimizationReceiptKind.PERSONAL_TRASH, transaction_id=UUID(identifier)
        )
    )
    assert result.outcome is OptimizationOutcomeType.APPLIED_VERIFIED
    assert result.recovery is RollbackLevel.MANUAL


@pytest.mark.parametrize(
    "kind,builder,name",
    [
        (OptimizationReceiptKind.MSI, build_msi_environment, "Example App"),
        (OptimizationReceiptKind.VENDOR, build_vendor_environment, "Example App"),
        (OptimizationReceiptKind.WINGET, build_winget_environment, "Example Clean App"),
    ],
)
def test_uninstall_result_is_read_from_inventory_verification_and_consumed_confirmations(
    tmp_path, kind, builder, name
):
    database = tmp_path / "state.db"
    env = builder(database)
    reader = OptimizationDomainResultReader(database)
    try:
        service = env.services.service
        prepared = service.prepare(
            "review selected software", SoftwareTargetQuery(display_name=name)
        )
        assert prepared.plan and prepared.preview and prepared.plan_confirmation
        reference = OptimizationTransactionReference(
            kind=kind, transaction_id=prepared.plan.transaction_id
        )
        first = reader.read(reference)
        assert first.outcome is None
        plan_gate = service.resolve_plan_confirmation(
            prepared.plan_confirmation.confirmation_id, True, prepared.plan, prepared.preview
        )
        runtime = service.prepare_runtime_confirmation(plan_gate.confirmation_id, prepared.plan)
        service.resolve_runtime_confirmation(
            runtime.confirmation.confirmation_id, True, prepared.plan, runtime.preview
        )
        assert reader.read(reference).outcome is None
        service.execute(runtime.confirmation.confirmation_id, prepared.plan, runtime.preview)
        result = reader.read(reference)
        assert result.outcome is OptimizationOutcomeType.APPLIED_VERIFIED
        assert result.confirmation_id == runtime.confirmation.confirmation_id
        assert result.plan_digest == first.plan_digest
        assert result.recovery is RollbackLevel.NONE
    finally:
        reader.close()
        env.close()


@pytest.mark.parametrize(
    "kind,workflow,filename",
    [
        (OptimizationReceiptKind.MSIX, run_msix_workflow, "state.sqlite3"),
        (OptimizationReceiptKind.CLEANUP, run_cleanup_workflow, "state.db"),
        (OptimizationReceiptKind.RECYCLE_BIN, run_empty_workflow, "state.db"),
        (OptimizationReceiptKind.PROCESS, run_process_workflow, "actions.db"),
        (OptimizationReceiptKind.RESIDUAL, run_residual_workflow, "state.db"),
    ],
)
def test_read_receipt_from_existing_exact_domain_workflow(tmp_path: Path, kind, workflow, filename):
    workflow(tmp_path)
    reader = OptimizationDomainResultReader(tmp_path / filename)
    try:
        table, _confirmation = _tables(kind)
        with reader._engine.connect() as connection:
            transaction_id = connection.execute(select(table.c.transaction_id)).scalar_one()
        result = reader.read(
            OptimizationTransactionReference(kind=kind, transaction_id=UUID(transaction_id))
        )
        assert result.outcome is OptimizationOutcomeType.APPLIED_VERIFIED
        assert result.confirmation_id is not None
        if kind is OptimizationReceiptKind.CLEANUP:
            assert result.recovery is RollbackLevel.MANUAL
            with pytest.raises(OptimizationRoutingError, match="MECHANISM"):
                reader.read(
                    result.reference.model_copy(
                        update={"kind": OptimizationReceiptKind.RECYCLE_BIN}
                    )
                )
    finally:
        reader.close()


@pytest.mark.parametrize(
    "attack",
    [
        "none",
        "missing_runtime",
        "missing_row",
        "missing_parent",
        "bad_parent",
        "state",
        "tier",
        "transaction",
        "plan_digest",
        "preview_digest",
        "row_parent",
    ],
)
def test_startup_receipt_requires_exact_consumed_lineage(tmp_path, attack):
    harness = _service(tmp_path)
    reader = OptimizationDomainResultReader(tmp_path / "state.db")
    plan, preview, review = harness.service.prepare_disable(
        "review one startup", harness.platform.value.identity
    )
    assert review.approved
    reference = OptimizationTransactionReference(
        kind=OptimizationReceiptKind.STARTUP, transaction_id=plan.transaction_id
    )
    try:
        assert reader.read(reference).outcome is None
        _confirm_execute(harness.service, plan, preview)
        first = reader.read(reference)
        assert first.outcome is OptimizationOutcomeType.APPLIED_VERIFIED
        assert first.recovery is RollbackLevel.FULL
        if attack == "none":
            return
        table, confirmations = _tables(reference.kind)
        # Tamper through the domain fixture's independent writable connection, not the
        # production reader (whose connections intentionally reject every write).
        with harness.repository._sessions.begin() as db:
            if attack in {"missing_runtime", "missing_row", "row_parent"}:
                changes = (
                    {"runtime_confirmation_id": None}
                    if attack == "missing_runtime"
                    else {"runtime_confirmation_id": str(uuid4())}
                    if attack == "missing_row"
                    else {"plan_confirmation_id": str(uuid4())}
                )
                db.execute(
                    update(table)
                    .where(table.c.transaction_id == str(plan.transaction_id))
                    .values(**changes)
                )
            else:
                changes = (
                    {"parent_confirmation_id": None}
                    if attack == "missing_parent"
                    else {"parent_confirmation_id": str(uuid4())}
                    if attack == "bad_parent"
                    else {"state": "APPROVED"}
                    if attack == "state"
                    else {"tier": "PLAN"}
                    if attack == "tier"
                    else {"transaction_id": str(uuid4())}
                    if attack == "transaction"
                    else {attack: "e" * 64}
                )
                db.execute(
                    update(confirmations)
                    .where(confirmations.c.confirmation_id == str(first.confirmation_id))
                    .values(**changes)
                )
        with pytest.raises(OptimizationRoutingError):
            reader.read(reference)
    finally:
        reader.close()
        harness.close()


@pytest.mark.parametrize(
    "state,expected",
    [
        ("CANCELLED", "USER_CANCELLED"),
        ("BLOCKED", "BLOCKED"),
        ("FAILED", "FAILED"),
        ("INTERRUPTED", "FAILED"),
        ("RESTORE_CONFLICT", "FAILED"),
        ("PREVIEWED", None),
    ],
)
def test_domain_denial_never_becomes_success(tmp_path, state, expected):
    harness = _service(tmp_path)
    reader = OptimizationDomainResultReader(tmp_path / "state.db")
    try:
        plan, _preview, _review = harness.service.prepare_disable(
            "review startup", harness.platform.value.identity
        )
        reference = OptimizationTransactionReference(
            kind=OptimizationReceiptKind.STARTUP, transaction_id=plan.transaction_id
        )
        table, _confirmations = _tables(reference.kind)
        with harness.repository._sessions.begin() as db:
            db.execute(
                update(table)
                .where(table.c.transaction_id == str(plan.transaction_id))
                .values(state=state)
            )
        assert reader.read(reference).outcome == expected
        with pytest.raises(OptimizationRoutingError, match="MISSING"):
            reader.read(reference.model_copy(update={"transaction_id": uuid4()}))
    finally:
        reader.close()
        harness.close()
