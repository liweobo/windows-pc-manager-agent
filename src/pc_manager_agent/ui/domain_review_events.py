"""Observation-only domain UI events; no observer can approve or execute a transaction."""

from __future__ import annotations

from uuid import UUID

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog

from pc_manager_agent.domain.optimization_receipts import (
    OptimizationReceiptKind,
    OptimizationTransactionReference,
)


class ObservedDomainDialog(QDialog):
    """Publish a reference after domain preparation, before either execution approval."""

    domain_preview_ready = Signal(object)

    def publish_domain_preview(self, kind: OptimizationReceiptKind, transaction_id: UUID) -> None:
        """Notify optional read-only observers; never emit a result or confirmation token."""
        self.domain_preview_ready.emit(
            OptimizationTransactionReference(kind=kind, transaction_id=transaction_id)
        )
