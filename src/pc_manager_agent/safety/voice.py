"""Deterministic voice input/output safety; no tool, microphone, provider or approval calls."""

from __future__ import annotations

import re
import unicodedata
from enum import StrEnum

from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.voice import ConfidenceLevel, SpeechToTextResult, VoiceError


class SensitiveTranscriptRedactor:
    """Conservative secret detection, not a guarantee of comprehensive data-loss prevention."""

    _secret = re.compile(
        r"(?i)(api[ _-]?key|password|passwd|secret|token|cookie|credential|"
        r"authorization|密码|口令|密钥|令牌|验证码|银行卡|账号|账户|私钥|助记词)"
        r"|\bsk-[A-Za-z0-9_-]{8,}|\bgh[pousr]_[A-Za-z0-9_]{8,}"
        r"|-----BEGIN [A-Z ]*PRIVATE KEY|\bBearer\s+\S+|\b\d{12,19}\b"
    )

    def contains_sensitive(self, text: str) -> bool:
        """Check normalized text before routing or speech; never inspect credentials on disk."""
        return bool(self._secret.search(unicodedata.normalize("NFKC", text)))

    def redact(self, text: str) -> str:
        """Drop the entire sensitive utterance instead of guessing where a secret ends."""
        return "[SENSITIVE_TRANSCRIPT_REDACTED]" if self.contains_sensitive(text) else text


class TranscriptConfidencePolicy:
    """Require human review regardless of quality; quality can never lower action risk."""

    def assess(self, result: SpeechToTextResult) -> ConfidenceLevel:
        """Return UNKNOWN without evidence; reject partial, empty, oversized or control text."""
        if not result.is_final:
            raise VoiceError("VOICE_PARTIAL_TRANSCRIPT_NOT_ROUTABLE")
        self.validate_text(result.text)
        if result.confidence is None:
            return ConfidenceLevel.UNKNOWN
        if result.confidence >= 0.9:
            return ConfidenceLevel.HIGH
        return ConfidenceLevel.MEDIUM if result.confidence >= 0.7 else ConfidenceLevel.LOW

    def validate_text(self, text: str) -> None:
        """Reject unsafe control characters and obvious secrets before creating a request."""
        if not text.strip() or len(text) > 4000:
            raise VoiceError("VOICE_TRANSCRIPT_INVALID")
        if any(unicodedata.category(char) in {"Cc", "Cf"} and char not in "\n\t" for char in text):
            raise VoiceError("VOICE_TRANSCRIPT_CONTROL_CHARACTERS")
        if SensitiveTranscriptRedactor().contains_sensitive(text):
            raise VoiceError("VOICE_SENSITIVE_TRANSCRIPT_BLOCKED")


class VoiceConfirmationPolicy:
    """V1 cannot approve ANY business plan via speech, including R0 or edited 'yes'."""

    def require_visual(self, risk: RiskLevel) -> None:
        """Always deny spoken approval with the domain-supplied risk, never infer its risk."""
        raise VoiceError(f"VOICE_CONFIRMATION_NOT_ALLOWED_FOR_{risk.value}")


class SpeechDecision(StrEnum):
    """Safe presentation outcomes; no business lifecycle implications."""

    ALLOW_SUMMARY = "ALLOW_SUMMARY"
    TEXT_ONLY = "TEXT_ONLY"
    BLOCK_SPEECH = "BLOCK_SPEECH"


class SafeSpeechOutputPolicy:
    """Only locally built aggregate summaries may be synthesized; arbitrary UI text is excluded."""

    def check(self, text: str, maximum_chars: int) -> SpeechDecision:
        """Reject secrets, paths, long opaque values, code and any oversized output."""
        if not text or len(text) > maximum_chars:
            return SpeechDecision.TEXT_ONLY
        if SensitiveTranscriptRedactor().contains_sensitive(text) or re.search(
            r"[A-Za-z]:[\\/]|\\\\|https?://|`|[a-fA-F0-9]{24,}|"
            r"(?i:confirmation[_ -]?id|nonce|powershell|cmd\.exe|ignore.*polic)",
            text,
        ):
            return SpeechDecision.BLOCK_SPEECH
        return SpeechDecision.ALLOW_SUMMARY
