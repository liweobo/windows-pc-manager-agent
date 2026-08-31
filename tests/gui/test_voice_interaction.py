"""GUI tests never enumerate/open host microphones, speakers or speech network connections."""

import asyncio
from uuid import uuid4

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout
from tests.unit.voice.test_foundation import FakeCapture, FakeSTT, FakeTTS

from pc_manager_agent.app.voice import VoiceServices, build_voice_services
from pc_manager_agent.config.voice import VoiceSettings
from pc_manager_agent.domain.user_requests import RequestChannel, RequestDomain, UserRequest
from pc_manager_agent.domain.voice import SpeechAudio, VoiceError, VoiceState
from pc_manager_agent.ui.domain_review_events import ObservedDomainDialog
from pc_manager_agent.ui.main_window import MainWindow
from pc_manager_agent.ui.voice_controller import VoiceUiController
from pc_manager_agent.ui.voice_controls import VoiceControls
from pc_manager_agent.voice.audio import MicrophonePermissionService
from pc_manager_agent.voice.providers import VoiceProviderService
from pc_manager_agent.voice.speech import SpeechOutcome, SpeechSummaryFacts


class SilentPlayback:
    def __init__(self):
        self.played, self.stops = 0, 0

    def play(self, audio):
        self.played += 1

    def stop(self):
        self.stops += 1


@pytest.fixture
def voice_ui(runtime, tmp_path):
    services = build_voice_services(
        tmp_path, runtime.audit, VoiceSettings(spoken_response_mode="SHORT")
    )
    services = VoiceServices(
        services.coordinator,
        VoiceProviderService(services.coordinator, FakeSTT(), FakeTTS()),
        services.router,
    )
    capture, playback = FakeCapture(), SilentPlayback()
    controller = VoiceUiController(
        services, capture, playback, MicrophonePermissionService(lambda: False)
    )
    yield controller, capture, playback
    assert controller.shutdown()


def test_widget_capture_review_submit_once(qtbot, voice_ui):
    from pc_manager_agent.domain.user_requests import VoiceInteractionContext

    controller, capture, playback = voice_ui
    controls = VoiceControls(controller, VoiceInteractionContext)
    qtbot.addWidget(controls)
    controls.show()
    assert capture.started == 0 and not controls.submit.isEnabled()
    requests = []
    controller.request_ready.connect(lambda request, route: requests.append((request, route)))
    qtbot.mousePress(controls.ptt, Qt.MouseButton.LeftButton)
    assert capture.started == 1 and controller.ptt.active
    qtbot.mouseRelease(controls.ptt, Qt.MouseButton.LeftButton)
    assert capture.stopped and controls.upload.isEnabled()
    controller.transcribe(controller.disclosure(), True)
    qtbot.waitUntil(lambda: controller.job is None)
    assert controls.editor.toPlainText() == "检查内存"
    controls.editor.setPlainText("检查 CPU")
    qtbot.mouseClick(controls.submit, Qt.MouseButton.LeftButton)
    controller.submit("检查 CPU")
    assert len(requests) == 1 and requests[0][0].channel is RequestChannel.VOICE_EDITED
    assert not controls.submit.isEnabled() and playback.played == 0


def test_hide_and_barge_in_stop_audio(qtbot, voice_ui):
    from pc_manager_agent.domain.user_requests import VoiceInteractionContext

    controller, capture, playback = voice_ui
    controls = VoiceControls(controller, VoiceInteractionContext)
    qtbot.addWidget(controls)
    controls.show()
    playback.play(SpeechAudio(pcm=b"00", provider="fake"))
    qtbot.mousePress(controls.ptt, Qt.MouseButton.LeftButton)
    assert playback.stops > 0
    controls.hide()
    assert capture.stopped and not controller.ptt.active
    assert controller.services.coordinator.state is VoiceState.CANCELLED


def test_late_results_never_play_or_replace_text(qtbot, voice_ui):
    controller, _capture, playback = voice_ui
    controller._completed(uuid4(), SpeechAudio(pcm=b"00", provider="fake"), "")
    assert playback.played == 0 and controller.review_text == ""
    controller.hardware_error("MICROPHONE_UNAVAILABLE")
    assert "MICROPHONE_UNAVAILABLE" in controller.message


@pytest.mark.parametrize("text", ["确认", "yes", "我承担风险，执行吧", "确认清空回收站"])
def test_spoken_confirmation_cannot_click_business_controls(qtbot, runtime, text):
    window = MainWindow(runtime)
    qtbot.addWidget(window)
    request = UserRequest(channel=RequestChannel.VOICE, text=text, context=window._voice_context())
    window._route_request(request, window._request_dispatcher.route(request))
    assert window._plan is None and window._confirmation is None
    assert "未批准" in window.statusBar().currentMessage()


def test_voice_navigation_uses_same_office_route_and_no_file_grant(qtbot, runtime):
    window = MainWindow(runtime)
    qtbot.addWidget(window)
    request = UserRequest(
        channel=RequestChannel.VOICE, text="总结文档", context=window._voice_context()
    )
    route = window._request_dispatcher.route(request)
    assert route.domain is RequestDomain.OFFICE
    window._route_request(request, route)
    assert window._tabs.currentWidget() is window._office_tab
    assert window._office_tab._goal.text() == "总结文档"


def test_changed_surface_blocks_old_recording(qtbot, runtime):
    window = MainWindow(runtime)
    qtbot.addWidget(window)
    request = UserRequest(
        channel=RequestChannel.VOICE, text="总结文档", context=window._voice_context()
    )
    window._tabs.setCurrentIndex(1)
    window._route_request(request, window._request_dispatcher.route(request))
    assert window._tabs.currentIndex() == 1
    assert "页面已变化" in window.statusBar().currentMessage()


def test_modal_controls_share_one_owner_and_cancel_only_that_dialog(qtbot, runtime):
    window = MainWindow(runtime)
    qtbot.addWidget(window)
    dialog = ObservedDomainDialog(window)
    dialog.setLayout(QVBoxLayout())
    dialog.open()
    qtbot.waitUntil(lambda: bool(window._voice_dialogs))
    controls = dialog.findChild(VoiceControls)
    assert controls.controller is window._voice
    request = UserRequest(channel=RequestChannel.VOICE, text="取消", context=controls._context())
    window._route_request(request, window._request_dispatcher.route(request))
    assert not dialog.isVisible()


def test_reconfigure_and_decline_upload(qtbot, voice_ui):
    from pc_manager_agent.domain.user_requests import VoiceInteractionContext

    controller, capture, _playback = voice_ui
    controller.press(VoiceInteractionContext(), visible=True)
    controller.release()
    controller.transcribe(controller.disclosure(), False)
    qtbot.waitUntil(lambda: controller.job is None)
    assert "REJECTED" in controller.message
    assert controller.review_text == "" and capture.stopped
    controller.configure(VoiceSettings())
    assert not controller.services.providers.configured


def test_cancel_inflight_stt_without_late_review(qtbot, voice_ui):
    from pc_manager_agent.domain.user_requests import VoiceInteractionContext

    controller, _capture, _playback = voice_ui

    class WaitingSTT(FakeSTT):
        async def transcribe(self, audio, language_hint):
            await asyncio.sleep(30)

    controller.services.providers._stt = WaitingSTT()
    controller.press(VoiceInteractionContext(), visible=True)
    controller.release()
    controller.transcribe(controller.disclosure(), True)
    controller.cancel()
    qtbot.waitUntil(lambda: controller.pool.activeThreadCount() == 0)
    assert controller.review_text == "" and controller.job is None


def test_playback_has_separate_start_stop_audit(qtbot, voice_ui):
    controller, _capture, playback = voice_ui
    proposal = controller.speech_proposal(SpeechSummaryFacts(outcome=SpeechOutcome.UNVERIFIED))
    controller.speak(proposal, True)
    qtbot.waitUntil(lambda: controller.job is None)
    assert playback.played == 1 and controller.speaking
    controller.stop_speech()
    events = controller.services.coordinator.audit._repository.list_recent(20)
    codes = {event.parameters.get("code") for event in events}
    assert "VOICE_PLAYBACK_REQUESTED" in codes and "VOICE_PLAYBACK_STOPPED" in codes


def test_playback_device_failure_does_not_leave_speaking_state(qtbot, voice_ui, monkeypatch):
    controller, _capture, playback = voice_ui

    def fail_playback(audio):
        raise VoiceError("VOICE_OUTPUT_FAILED")

    monkeypatch.setattr(playback, "play", fail_playback)
    proposal = controller.speech_proposal(SpeechSummaryFacts(outcome=SpeechOutcome.UNVERIFIED))
    controller.speak(proposal, True)
    qtbot.waitUntil(lambda: controller.job is None)
    assert not controller.speaking and controller._playback_reference is None
    assert "VOICE_OUTPUT_FAILED" in controller.message


def test_audit_failure_stops_hardware_and_blocks_future_speech(qtbot, voice_ui, monkeypatch):
    from pc_manager_agent.domain.user_requests import VoiceInteractionContext

    controller, capture, playback = voice_ui
    proposal = controller.speech_proposal(SpeechSummaryFacts(outcome=SpeechOutcome.UNVERIFIED))
    controller.speak(proposal, True)
    qtbot.waitUntil(lambda: controller.job is None)

    def fail_audit(*args, **kwargs):
        raise VoiceError("VOICE_AUDIT_UNAVAILABLE")

    monkeypatch.setattr(controller.services.coordinator.audit, "record", fail_audit)
    controller.cancel()
    assert playback.stops and capture.stopped and controller._playback_audit_failed
    assert "AUDIT_UNAVAILABLE" in controller.message
    controller.press(VoiceInteractionContext(), visible=True)
    assert capture.started == 0 and "RESTART_REQUIRED" in controller.message
    with pytest.raises(VoiceError, match="RESTART_REQUIRED"):
        controller.speak(proposal, True)
