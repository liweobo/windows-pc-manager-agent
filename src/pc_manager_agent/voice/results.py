"""Read-only factual speech projection; a closed dialog or process exit is never success."""

from collections.abc import Callable
from datetime import datetime

from pc_manager_agent.domain.optimization_actions import OptimizationOutcomeType
from pc_manager_agent.domain.optimization_receipts import (
    DomainReceiptSnapshot,
    OptimizationTransactionReference,
)
from pc_manager_agent.voice.speech import SpeechOutcome, SpeechSummaryFacts


class VoiceResultSummaryService:
    """Reuse verified domain receipts without any access to their executor or confirmation APIs."""

    def __init__(
        self,
        read: Callable[[OptimizationTransactionReference], DomainReceiptSnapshot],
        started_at: datetime,
    ) -> None:
        self._read, self._started_at = read, started_at

    def summarize(self, reference: OptimizationTransactionReference) -> SpeechSummaryFacts:
        """Read one fresh bound receipt; absent/old/contradictory evidence remains unverified."""
        try:
            receipt = self._read(reference)
        except Exception:
            return SpeechSummaryFacts(outcome=SpeechOutcome.UNVERIFIED)
        if receipt.reference != reference or receipt.created_at < self._started_at:
            return SpeechSummaryFacts(outcome=SpeechOutcome.UNVERIFIED)
        if (
            receipt.outcome is OptimizationOutcomeType.APPLIED_VERIFIED
            and receipt.confirmation_id is not None
        ):
            return SpeechSummaryFacts(
                outcome=SpeechOutcome.VERIFIED,
                verified_items=1,
                risk=receipt.risk,
                recovery=receipt.recovery,
            )
        outcomes = {
            OptimizationOutcomeType.BLOCKED: SpeechOutcome.BLOCKED,
            OptimizationOutcomeType.FAILED: SpeechOutcome.FAILED,
            OptimizationOutcomeType.USER_CANCELLED: SpeechOutcome.CANCEL_REQUESTED,
        }
        return SpeechSummaryFacts(
            outcome=outcomes.get(receipt.outcome, SpeechOutcome.UNVERIFIED)
            if receipt.outcome
            else SpeechOutcome.UNVERIFIED,
            risk=receipt.risk,
            recovery=receipt.recovery,
        )
