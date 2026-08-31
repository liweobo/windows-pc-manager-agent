"""Safe deterministic speech summaries; no arbitrary chat/document text enters TTS."""

from enum import StrEnum

from pydantic import Field

from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.voice import FrozenVoiceModel, VoiceError
from pc_manager_agent.safety.voice import SafeSpeechOutputPolicy, SpeechDecision


class SpeechOutcome(StrEnum):
    """Presentation facts, supplied by existing business verification or local input state."""

    PREPARATION = "PREPARATION"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"


class SpeechSummaryFacts(FrozenVoiceModel):
    """Finite aggregate projection; no paths, names, credentials, raw errors or document bodies."""

    outcome: SpeechOutcome
    verified_items: int = Field(default=0, ge=0, le=1000000)
    pending_items: int = Field(default=0, ge=0, le=1000000)
    risk: RiskLevel | None = None
    recovery: RollbackLevel | None = None


class SpeechSummaryBuilder:
    """Speak exact result semantics without an LLM reinterpreting success or measured numbers."""

    def build(self, facts: SpeechSummaryFacts, maximum_chars: int = 300) -> str:
        """Render a short allow-listed statement; long details remain on screen."""
        phrases = {
            SpeechOutcome.PREPARATION: "请求已交给原业务页面。请检查目标和计划，尚未批准执行。",
            SpeechOutcome.AWAITING_CONFIRMATION: (
                "这个操作需要你在屏幕上确认，语音不能代替业务确认。"
            ),
            SpeechOutcome.VERIFIED: (
                f"已有 {facts.verified_items} 项通过业务结果验证。详情请查看屏幕。"
            ),
            SpeechOutcome.UNVERIFIED: "最终状态尚未可靠验证，不能确认操作成功，请查看屏幕详情。",
            SpeechOutcome.PARTIAL: (
                f"部分完成，已验证 {facts.verified_items} 项，"
                f"仍有 {facts.pending_items} 项需要查看。"
            ),
            SpeechOutcome.FAILED: "本次操作未能完成，请查看原业务页面中的实际状态和恢复说明。",
            SpeechOutcome.BLOCKED: "请求已被安全规则阻止，语音和用户确认都不能绕过这些规则。",
            SpeechOutcome.CANCEL_REQUESTED: (
                "已请求停止后续步骤。这不是撤销，已完成的操作不会自动恢复。"
            ),
        }
        text = phrases[facts.outcome]
        if facts.risk is not None:
            text += f" 风险等级 {facts.risk.value}。"
        if facts.recovery is RollbackLevel.NONE:
            text += " 此操作没有自动回滚；重新安装不等于撤销。"
        elif facts.recovery is RollbackLevel.MANUAL:
            text += " 如需恢复，请按原业务页面说明手动处理。"
        elif facts.recovery in {RollbackLevel.FULL, RollbackLevel.PARTIAL}:
            text += " 恢复仍需核对当前状态并在原页面重新确认。"
        if SafeSpeechOutputPolicy().check(text, maximum_chars) is not SpeechDecision.ALLOW_SUMMARY:
            raise VoiceError("VOICE_SPEECH_POLICY_BLOCKED")
        return text
