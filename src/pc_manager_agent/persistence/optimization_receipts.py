"""Finite SELECT-only adapters for domain transaction and consumed-confirmation evidence."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from pydantic import Field, StrictBool
from sqlalchemy import Connection, Table, select

from pc_manager_agent.domain.msix_uninstall import MsixVerificationState
from pc_manager_agent.domain.optimization_actions import OptimizationOutcomeType
from pc_manager_agent.domain.optimization_receipts import (
    DomainReceiptSnapshot,
    OptimizationReceiptKind,
    OptimizationTransactionReference,
)
from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.process_actions import ProcessActionToolResult
from pc_manager_agent.domain.residual_cleanup import ResidualCleanupItemResult
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.software_uninstall_execution import MsiVerificationState
from pc_manager_agent.domain.startup_actions import StartupMutationResult
from pc_manager_agent.domain.system_cleanup_execution import (
    CleanupItemResult,
    RecycleBinEmptyResult,
)
from pc_manager_agent.domain.vendor_uninstall import VendorVerificationState
from pc_manager_agent.domain.winget_uninstall import WingetVerificationState
from pc_manager_agent.persistence.database import create_sqlite_engine
from pc_manager_agent.persistence.file_operations import (
    OperationItemRow,
    OperationTransactionRow,
    TransactionConfirmationRow,
    TrashRecoveryRow,
    UndoRecordRow,
)
from pc_manager_agent.persistence.msix_uninstall import MsixConfirmationRow, MsixTransactionRow
from pc_manager_agent.persistence.process_actions import (
    ProcessActionConfirmationRow,
    ProcessActionTransactionRow,
)
from pc_manager_agent.persistence.residual_cleanup import (
    ResidualCleanupConfirmationRow,
    ResidualCleanupItemRow,
    ResidualCleanupTransactionRow,
)
from pc_manager_agent.persistence.software_uninstall_execution import (
    MsiUninstallConfirmationRow,
    MsiUninstallTransactionRow,
)
from pc_manager_agent.persistence.startup_actions import (
    StartupConfirmationRow,
    StartupTransactionRow,
)
from pc_manager_agent.persistence.system_cleanup import (
    SystemCleanupConfirmationRow,
    SystemCleanupItemRow,
    SystemCleanupTransactionRow,
)
from pc_manager_agent.persistence.vendor_uninstall import (
    VendorUninstallConfirmationRow,
    VendorUninstallTransactionRow,
)
from pc_manager_agent.persistence.winget_uninstall import (
    WingetUninstallConfirmationRow,
    WingetUninstallTransactionRow,
)
from pc_manager_agent.recovery.models import TrashRecoveryRecord
from pc_manager_agent.rollback.models import UndoRecord
from pc_manager_agent.safety.optimization_actions import OptimizationRoutingError


class _MsiReceipt(FrozenModel):
    """Exact privacy-minimized verification schema persisted by the MSI service."""

    state: MsiVerificationState
    identity_present: StrictBool | None
    product_code_present: StrictBool | None
    replacement_candidates: int = Field(ge=0)


class _VendorReceipt(FrozenModel):
    """Exact privacy-minimized verification schema persisted by the Vendor service."""

    state: VendorVerificationState
    identity_present: StrictBool | None
    replacement_candidates: int = Field(ge=0)


class _WingetReceipt(FrozenModel):
    """Exact privacy-minimized dual-inventory schema persisted by the winget service."""

    state: WingetVerificationState
    package_present: StrictBool | None
    software_present: StrictBool | None


class _MsixReceipt(FrozenModel):
    """Only aggregate verification, never Package names or user-data paths."""

    transaction_id: UUID
    verification_state: MsixVerificationState
    original_full_name_present: StrictBool
    software_identity_present: StrictBool | None


def _tables(kind: OptimizationReceiptKind) -> tuple[Table, Table]:
    rows = {
        OptimizationReceiptKind.FILES: (OperationTransactionRow, TransactionConfirmationRow),
        OptimizationReceiptKind.PERSONAL_TRASH: (
            OperationTransactionRow,
            TransactionConfirmationRow,
        ),
        OptimizationReceiptKind.PROCESS: (
            ProcessActionTransactionRow,
            ProcessActionConfirmationRow,
        ),
        OptimizationReceiptKind.STARTUP: (StartupTransactionRow, StartupConfirmationRow),
        OptimizationReceiptKind.MSI: (MsiUninstallTransactionRow, MsiUninstallConfirmationRow),
        OptimizationReceiptKind.VENDOR: (
            VendorUninstallTransactionRow,
            VendorUninstallConfirmationRow,
        ),
        OptimizationReceiptKind.WINGET: (
            WingetUninstallTransactionRow,
            WingetUninstallConfirmationRow,
        ),
        OptimizationReceiptKind.MSIX: (MsixTransactionRow, MsixConfirmationRow),
        OptimizationReceiptKind.CLEANUP: (
            SystemCleanupTransactionRow,
            SystemCleanupConfirmationRow,
        ),
        OptimizationReceiptKind.RECYCLE_BIN: (
            SystemCleanupTransactionRow,
            SystemCleanupConfirmationRow,
        ),
        OptimizationReceiptKind.RESIDUAL: (
            ResidualCleanupTransactionRow,
            ResidualCleanupConfirmationRow,
        ),
    }[kind]
    return cast(Table, rows[0].__table__), cast(Table, rows[1].__table__)


class OptimizationDomainResultReader:
    """Read existing domain stores only. No executable, writer, guard or Broker is imported."""

    def __init__(self, database_path: Path) -> None:
        self._engine = create_sqlite_engine(database_path)

    def read(self, reference: OptimizationTransactionReference) -> DomainReceiptSnapshot:
        """Resolve exact immutable lineage and preserve incomplete/contradictory results."""
        reference = OptimizationTransactionReference.model_validate_json(
            reference.model_dump_json()
        )
        table, confirmations = _tables(reference.kind)
        with self._engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA query_only=ON")
            if connection.exec_driver_sql("PRAGMA quick_check").scalar_one() != "ok":
                raise OptimizationRoutingError("DOMAIN_STORE_CORRUPT")
            row = (
                connection.execute(
                    select(table).where(table.c.transaction_id == str(reference.transaction_id))
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise OptimizationRoutingError("DOMAIN_TRANSACTION_MISSING")
            state = str(row["state"]).upper()
            kind = reference.kind
            if kind in {OptimizationReceiptKind.FILES, OptimizationReceiptKind.PERSONAL_TRASH}:
                return self._read_files(connection, reference, dict(row), state)
            if kind in {OptimizationReceiptKind.CLEANUP, OptimizationReceiptKind.RECYCLE_BIN}:
                expected = (
                    "RECYCLE_BIN_EMPTY"
                    if kind is OptimizationReceiptKind.RECYCLE_BIN
                    else "ITEM_CLEANUP"
                )
                if str(row["transaction_kind"]).upper() != expected:
                    raise OptimizationRoutingError("DOMAIN_MECHANISM_MISMATCH")
            risk = RiskLevel(row.get("risk_level", "R2"))
            if kind is OptimizationReceiptKind.PROCESS and "FORCE" in str(row["action"]).upper():
                risk = RiskLevel.R2_HIGH_IMPACT
            recovery = RollbackLevel.NONE
            if kind is OptimizationReceiptKind.STARTUP:
                recovery = RollbackLevel.FULL
            elif kind in {OptimizationReceiptKind.CLEANUP, OptimizationReceiptKind.RESIDUAL}:
                recovery = RollbackLevel.MANUAL
            outcome = self._outcome(connection, reference, dict(row), state)
            confirmation_id = None
            if outcome in {
                OptimizationOutcomeType.APPLIED_VERIFIED,
                OptimizationOutcomeType.APPLIED_UNVERIFIED,
            }:
                confirmation_id = self._consumed_lineage(connection, confirmations, dict(row))
            return DomainReceiptSnapshot(
                reference=reference,
                plan_id=UUID(row["plan_id"]),
                plan_digest=row["plan_digest"],
                created_at=self._aware(row["created_at"]),
                updated_at=self._aware(row["updated_at"]),
                state=state,
                outcome=outcome,
                confirmation_id=confirmation_id,
                risk=risk,
                recovery=recovery,
            )

    @staticmethod
    def _read_files(
        connection: Connection,
        reference: OptimizationTransactionReference,
        row: Mapping[str, Any],
        state: str,
    ) -> DomainReceiptSnapshot:
        trash = reference.kind is OptimizationReceiptKind.PERSONAL_TRASH
        items = tuple(
            connection.execute(
                select(OperationItemRow.__table__).where(
                    OperationItemRow.transaction_id == str(reference.transaction_id)
                )
            ).mappings()
        )
        allowed = (
            {"RECYCLE_FILE", "RECYCLE_DIRECTORY"}
            if trash
            else {
                "CREATE_DIRECTORY",
                "MOVE_FILE",
                "MOVE_DIRECTORY",
                "RENAME_FILE",
                "RENAME_DIRECTORY",
            }
        )
        if not items or any(item["operation_type"] not in allowed for item in items):
            raise OptimizationRoutingError("FILE_RECEIPT_MECHANISM_MISMATCH")
        outcome = None
        gate = None
        if state in {"FAILED", "UNKNOWN", "INTERRUPTED", "ROLLBACK_FAILED"}:
            outcome = OptimizationOutcomeType.FAILED
        elif state == "CANCELLED":
            outcome = OptimizationOutcomeType.USER_CANCELLED
        elif state in {"COMPLETED", "PARTIALLY_COMPLETED"}:
            if row["confirmation_id"] is None or row["confirmed_at"] is None:
                raise OptimizationRoutingError("FILE_CONFIRMATION_MISSING")
            gate = UUID(row["confirmation_id"])
            verified = (
                state == "COMPLETED"
                and len(items) == row["operation_count"]
                and all(item["state"] == "COMPLETED" for item in items)
            )
            if trash:
                confirmations = tuple(
                    connection.execute(
                        select(TransactionConfirmationRow.__table__).where(
                            TransactionConfirmationRow.transaction_id
                            == str(reference.transaction_id)
                        )
                    ).mappings()
                )
                # Stage 2B retains the initial Preview in the transaction, while the
                # confirmed runtime gate references its freshly revalidated Preview.
                runtime_gates = tuple(
                    item
                    for item in confirmations
                    if item["tier"] == "RUNTIME"
                    and item["state"] == "CONSUMED"
                    and item["confirmation_id"] == row["confirmation_id"]
                )
                plan_gates = tuple(
                    item
                    for item in confirmations
                    if item["tier"] == "PLAN"
                    and item["state"] == "APPROVED"
                    and item["binding_digest"] == row["preview_digest"]
                )
                if len(runtime_gates) != 1 or len(plan_gates) != 1:
                    raise OptimizationRoutingError("FILE_CONFIRMATION_LINEAGE_MISMATCH")
                gate = UUID(runtime_gates[0]["confirmation_id"])
                records = tuple(
                    connection.execute(
                        select(TrashRecoveryRow.__table__).where(
                            TrashRecoveryRow.transaction_id == str(reference.transaction_id)
                        )
                    ).mappings()
                )
                verified = verified and len(records) == len(items)
                for record in records:
                    recovery = TrashRecoveryRecord.model_validate(record["recovery_data"])
                    verified = (
                        verified
                        and recovery.transaction_id == reference.transaction_id
                        and str(recovery.operation_id) == record["operation_id"]
                        and record["operation_id"] in {item["operation_id"] for item in items}
                        and record["status"] == recovery.status.value
                        and record["record_digest"] == recovery.canonical_digest()
                        and recovery.status.value == "AVAILABLE"
                        and recovery.verification_status is not None
                        and recovery.verification_status.value == "VERIFIED_RECYCLED"
                    )
            else:
                records = tuple(
                    connection.execute(
                        select(UndoRecordRow.__table__).where(
                            UndoRecordRow.transaction_id == str(reference.transaction_id)
                        )
                    ).mappings()
                )
                verified = verified and len(records) == len(items)
                for record in records:
                    undo = UndoRecord.model_validate(record["undo_data"])
                    verified = (
                        verified
                        and undo.transaction_id == reference.transaction_id
                        and str(undo.operation_id) == record["operation_id"]
                        and record["operation_id"] in {item["operation_id"] for item in items}
                        and record["status"] == undo.status.value
                        and record["record_digest"] == undo.canonical_digest()
                        and undo.status.value == "AVAILABLE"
                        and undo.after_state is not None
                    )
            outcome = (
                OptimizationOutcomeType.APPLIED_VERIFIED
                if verified
                else OptimizationOutcomeType.APPLIED_UNVERIFIED
            )
        return DomainReceiptSnapshot(
            reference=reference,
            plan_id=UUID(row["plan_id"]),
            plan_digest=row["plan_digest"],
            created_at=OptimizationDomainResultReader._aware(row["created_at"]),
            updated_at=OptimizationDomainResultReader._aware(row["updated_at"]),
            state=state,
            outcome=outcome,
            confirmation_id=gate,
            risk=RiskLevel.R2 if trash else RiskLevel.R1,
            recovery=RollbackLevel.MANUAL if trash else RollbackLevel.FULL,
        )

    @staticmethod
    def _aware(value: datetime) -> datetime:
        # SQLite DateTime strips timezone information; all domain writers use UTC.
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    @staticmethod
    def _consumed_lineage(connection: Connection, table: Table, row: Mapping[str, Any]) -> UUID:
        runtime_id = row.get("runtime_confirmation_id")
        if not runtime_id:
            raise OptimizationRoutingError("DOMAIN_CONFIRMATION_MISSING")
        runtime = (
            connection.execute(select(table).where(table.c.confirmation_id == runtime_id))
            .mappings()
            .one_or_none()
        )
        if runtime is None or not runtime["parent_confirmation_id"]:
            raise OptimizationRoutingError("DOMAIN_CONFIRMATION_MISSING")
        plan = (
            connection.execute(
                select(table).where(table.c.confirmation_id == runtime["parent_confirmation_id"])
            )
            .mappings()
            .one_or_none()
        )
        if plan is None:
            raise OptimizationRoutingError("DOMAIN_CONFIRMATION_MISSING")
        for confirmation, tier in ((plan, "PLAN"), (runtime, "RUNTIME")):
            payload = confirmation.get("payload", confirmation)
            if (
                str(confirmation["state"]).upper() != "CONSUMED"
                or str(confirmation["tier"]).upper() != tier
                or confirmation["transaction_id"] != row["transaction_id"]
                or payload.get("plan_digest") != row["plan_digest"]
                # The domain deliberately replaces Preview at runtime. Its plan gate
                # binds the same immutable plan, not the later Preview timestamp/hash.
                or (tier == "RUNTIME" and payload.get("preview_digest") != row["preview_digest"])
            ):
                raise OptimizationRoutingError("DOMAIN_CONFIRMATION_LINEAGE_MISMATCH")
        if row.get("plan_confirmation_id") not in {None, plan["confirmation_id"]}:
            raise OptimizationRoutingError("DOMAIN_CONFIRMATION_PARENT_MISMATCH")
        return UUID(runtime_id)

    @staticmethod
    def _outcome(
        connection: Connection,
        reference: OptimizationTransactionReference,
        row: Mapping[str, Any],
        state: str,
    ) -> OptimizationOutcomeType | None:
        if state in {"CANCELLED", "USER_CANCELLED"}:
            return OptimizationOutcomeType.USER_CANCELLED
        if state in {"BLOCKED", "ACCESS_DENIED", "PRIVILEGE_REQUIRED"}:
            return OptimizationOutcomeType.BLOCKED
        if state in {"FAILED", "INTERRUPTED", "RESTORE_CONFLICT"}:
            return OptimizationOutcomeType.FAILED
        terminal = {
            "COMPLETED",
            "GRACEFUL_COMPLETED",
            "GRACEFUL_TIMEOUT",
            "VERIFIED_REMOVED",
            "COMPLETED_UNVERIFIED",
            "REBOOT_REQUIRED",
            "PARTIALLY_COMPLETED",
        }
        if state not in terminal:
            return None
        kind = reference.kind
        verified = False
        if kind is OptimizationReceiptKind.PROCESS and row["result"] is not None:
            process = ProcessActionToolResult.model_validate(row["result"])
            identities = tuple(item.identity_digest for item in process.members)
            targets_match = (
                process.action.value == row["action"]
                and len(process.members) == row["process_count"]
                and len(set(identities)) == len(identities)
                and hashlib.sha256("\n".join(sorted(identities)).encode()).hexdigest()
                == row["target_set_digest"]
            )
            if targets_match and all(
                item.state.value == "ALREADY_EXITED" for item in process.members
            ):
                return OptimizationOutcomeType.NO_LONGER_APPLICABLE
            verified = targets_match and process.all_exited
        elif kind is OptimizationReceiptKind.STARTUP and row["result"] is not None:
            startup = StartupMutationResult.model_validate(row["result"])
            verified = (
                startup.verified
                and startup.action.value == row["action"]
                and startup.identity_digest == row["identity_digest"]
            )
        elif kind is OptimizationReceiptKind.MSI and row["verification_result"] is not None:
            result = _MsiReceipt.model_validate(row["verification_result"])
            verified = (
                result.state.value == "verified_removed"
                and result.identity_present is False
                and result.product_code_present is False
            )
        elif kind is OptimizationReceiptKind.VENDOR and row["verification_result"] is not None:
            vendor = _VendorReceipt.model_validate(row["verification_result"])
            verified = vendor.state.value == "verified_removed" and vendor.identity_present is False
        elif kind is OptimizationReceiptKind.WINGET and row["verification_result"] is not None:
            winget = _WingetReceipt.model_validate(row["verification_result"])
            verified = (
                winget.state.value == "verified_removed"
                and winget.package_present is False
                and winget.software_present is False
            )
        elif kind is OptimizationReceiptKind.MSIX and row["result"] is not None:
            msix = _MsixReceipt.model_validate(row["result"])
            verified = (
                msix.transaction_id == reference.transaction_id
                and msix.verification_state.value in {"verified_removed", "already_removed"}
                and not msix.original_full_name_present
            )
        elif kind in {OptimizationReceiptKind.CLEANUP, OptimizationReceiptKind.RESIDUAL}:
            verified = OptimizationDomainResultReader._verified_cleanup_items(
                connection, reference, row["plan_payload"]["total_items"]
            )
        elif kind is OptimizationReceiptKind.RECYCLE_BIN and row["result_payload"] is not None:
            empty = RecycleBinEmptyResult.model_validate(row["result_payload"])
            verified = (
                empty.transaction_id == reference.transaction_id
                and empty.verification_status.value == "RECYCLE_BIN_EMPTY_VERIFIED"
                and empty.after is not None
                and empty.after.item_count == 0
            )
        if state in {
            "GRACEFUL_TIMEOUT",
            "PARTIALLY_COMPLETED",
            "COMPLETED_UNVERIFIED",
            "REBOOT_REQUIRED",
        }:
            verified = False
        return (
            OptimizationOutcomeType.APPLIED_VERIFIED
            if verified
            else OptimizationOutcomeType.APPLIED_UNVERIFIED
        )

    @staticmethod
    def _verified_cleanup_items(
        connection: Connection, reference: OptimizationTransactionReference, expected_count: int
    ) -> bool:
        """Match every result to its reserved item; never substitute another item's success."""
        cleanup = reference.kind is OptimizationReceiptKind.CLEANUP
        table = SystemCleanupItemRow if cleanup else ResidualCleanupItemRow
        result_type = CleanupItemResult if cleanup else ResidualCleanupItemResult
        rows = tuple(
            connection.execute(
                select(table.__table__).where(table.transaction_id == str(reference.transaction_id))
            ).mappings()
        )
        if not rows or len(rows) != expected_count:
            return False
        for item in rows:
            if item["result_payload"] is None:
                return False
            result = result_type.model_validate(item["result_payload"])
            if (
                result.state.value != "VERIFIED"
                or item["state"] != "VERIFIED"
                or str(result.item_ref) != item["item_ref"]
                or str(result.operation_id) != item["operation_id"]
                or result.sequence != item["sequence"]
                or str(result.source_candidate_id) != item["source_candidate_id"]
                or result.verification_status.value
                not in {
                    "ORIGINAL_OBJECT_REMOVED" if cleanup else "ORIGINAL_IDENTITY_REMOVED",
                    "ORIGINAL_REMOVED_NEW_OBJECT_PRESENT",
                }
            ):
                return False
        return True

    def close(self) -> None:
        """Dispose the reader's private read-only connections."""
        self._engine.dispose()
