"""Visible microphone/review/disclosure controls mirrored around one shared controller."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QHideEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pc_manager_agent.domain.user_requests import VoiceInteractionContext
from pc_manager_agent.domain.voice import VoiceState
from pc_manager_agent.ui.voice_controller import VoiceUiController
from pc_manager_agent.voice.speech import SpeechOutcome, SpeechSummaryFacts


class VoiceControls(QWidget):
    """No confirm/execute buttons are clicked on behalf of recognized speech."""

    def __init__(
        self,
        controller: VoiceUiController,
        context: Callable[[], VoiceInteractionContext],
        parent: QWidget | None = None,
        summary: Callable[[], SpeechSummaryFacts] | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller, self._context = controller, context
        self._summary = summary or (lambda: SpeechSummaryFacts(outcome=SpeechOutcome.PREPARATION))
        self._owns_capture = False
        self._shown_text = ""
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.ptt = QPushButton("按住说话（空格键亦可）")
        self.ptt.setAutoRepeat(False)
        self.ptt.setAutoDefault(False)
        self.upload = QPushButton("查看音频外发确认")
        self.cancel_button = QPushButton("停止语音 / 丢弃录音")
        self.speak_button = QPushButton("播报安全提示")
        self.settings_button = QPushButton("语音设置")
        for button in (
            self.ptt,
            self.upload,
            self.cancel_button,
            self.speak_button,
            self.settings_button,
        ):
            row.addWidget(button)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("识别文字仅在本地内存中显示。检查并修改后再提交。")
        self.editor.setMaximumHeight(90)
        self.submit = QPushButton("文字已检查，提交请求（不是操作确认）")
        layout.addLayout(row)
        layout.addWidget(self.status)
        layout.addWidget(self.editor)
        layout.addWidget(self.submit)
        self.ptt.pressed.connect(self._press)
        self.ptt.released.connect(self._release)
        self.upload.clicked.connect(self._upload)
        self.cancel_button.clicked.connect(controller.cancel)
        self.submit.clicked.connect(lambda: controller.submit(self.editor.toPlainText()))
        self.speak_button.clicked.connect(self._speak)
        self.settings_button.clicked.connect(self._settings)
        controller.changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        """Reflect shared state without overwriting a user's ongoing review edits."""
        self.status.setText(self.controller.message)
        state = self.controller.services.coordinator.state
        if self._shown_text != self.controller.review_text:
            self._shown_text = self.controller.review_text
            self.editor.setPlainText(self._shown_text)
        reviewing = state is VoiceState.REVIEWING_TRANSCRIPT
        self.editor.setEnabled(reviewing)
        self.submit.setEnabled(reviewing)
        self.upload.setEnabled(state is VoiceState.AWAITING_DISCLOSURE)
        self.speak_button.setEnabled(not self.controller.ptt.active and self.controller.job is None)
        self.ptt.setText("正在录音，松开结束" if self.controller.ptt.active else "按住说话")

    def _press(self) -> None:
        self.controller.press(self._context(), visible=self.isVisible())
        self._owns_capture = self.controller.ptt.active

    def _release(self) -> None:
        if self._owns_capture:
            self._owns_capture = False
            self.controller.release()

    def _upload(self) -> None:
        try:
            value = self.controller.disclosure()
            approved = self._ask(
                "仅确认本次音频上传，不确认任何电脑操作",
                f"目标：{value.destination}\n音频：{value.quantity} 字节，"
                f"约 {value.quantity / 48000:.1f} 秒。\n"
                "将上传完整录音用于转写，可能产生 API 费用；音频发送前无法可靠识别秘密。"
                "请勿上传密码、令牌、敏感谈话或旁人的声音。\n"
                "本应用不保存录音，但云端保留政策由供应商决定；取消无法撤回已发送数据。\n"
                "本次批准有时限且只能使用一次。是否上传？",
            )
            self.controller.transcribe(value, approved)
        except Exception as exc:
            self.controller.error(exc)
            self.refresh()

    def _speak(self) -> None:
        try:
            # Deliberately no success inference from arbitrary widget text/window closure.
            proposal = self.controller.speech_proposal(self._summary())
            approved = self._ask(
                "AI 合成语音：本次文字外发确认",
                f"目标：{proposal.disclosure.destination}\n发送的全部文字：\n{proposal.text}\n"
                "可能产生 API 费用。不会发送当前对话、文件路径或文档正文。是否继续？",
            )
            self.controller.speak(proposal, approved)
        except Exception as exc:
            self.controller.error(exc)
            self.refresh()

    def _ask(self, title: str, text: str) -> bool:
        dialog = QMessageBox(
            QMessageBox.Icon.Warning,
            title,
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            self,
        )
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setDefaultButton(QMessageBox.StandardButton.No)
        return dialog.exec() == QMessageBox.StandardButton.Yes

    def hideEvent(self, event: QHideEvent) -> None:
        """A disappearing capture control cannot leave the microphone live behind a tray."""
        if self._owns_capture:
            self._owns_capture = False
            self.controller.cancel()
        super().hideEvent(event)

    def _settings(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("本次运行的语音偏好（不保存密钥）")
        layout = QFormLayout(dialog)
        settings = self.controller.services.coordinator.settings
        provider, language, output = QComboBox(), QComboBox(), QComboBox()
        provider.addItems(["disabled", "openai"])
        provider.setCurrentText(settings.provider)
        language.addItems(["auto", "zh", "en"])
        language.setCurrentText(settings.language_hint)
        output.addItems(["OFF", "SHORT", "NORMAL"])
        output.setCurrentText(settings.spoken_response_mode)
        layout.addRow("供应商", provider)
        layout.addRow("语言提示", language)
        layout.addRow("播报模式（仅手动触发）", output)
        info = QLabel(
            f"STT：{settings.stt_model}；TTS：{settings.tts_model}\n"
            f"单次录音上限 {settings.max_voice_input_seconds} 秒；"
            "默认设备需支持 24kHz 单声道 PCM。\n"
            "OpenAI 仅使用官方地址，API Key 只读环境变量 OPENAI_API_KEY。\n"
            "首次使用请检查 Windows 设置 → 隐私和安全性 → 麦克风 → 允许桌面应用访问。\n"
            "不常驻监听；不使用声纹；识别结果必须人工检查；所有业务确认仍在原窗口。"
        )
        info.setWordWrap(True)
        layout.addRow(info)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                raw = settings.model_dump()
                raw.update(
                    provider=provider.currentText(),
                    language_hint=language.currentText(),
                    spoken_response_mode=output.currentText(),
                    api_key=settings.api_key,
                )
                self.controller.configure(type(settings).model_validate(raw))
            except Exception as exc:
                self.controller.error(exc)
                self.refresh()
