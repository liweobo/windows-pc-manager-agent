"""Speech transport consent does not grant an existing domain's plan/execution authority."""

import asyncio
from threading import Event

import pytest
from tests.unit.voice.test_foundation import FakeCapture, FakeSTT

from pc_manager_agent.app.voice import VoiceServices, build_voice_services
from pc_manager_agent.config.voice import VoiceSettings
from pc_manager_agent.confirmation.state_machine import ConfirmationError
from pc_manager_agent.domain.user_requests import RequestDomain, VoiceInteractionContext
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.voice.audio import MicrophonePermissionService
from pc_manager_agent.voice.providers import VoiceProviderService
from pc_manager_agent.voice.push_to_talk import PushToTalkController


def test_reviewed_voice_still_requires_real_scan_plan_confirmation(runtime, tmp_path, request):
    services = build_voice_services(tmp_path, runtime.audit, VoiceSettings())
    request.addfinalizer(services.close)
    services = VoiceServices(
        services.coordinator,
        VoiceProviderService(services.coordinator, FakeSTT("扫描大文件"), None),
        services.router,
    )
    ptt = PushToTalkController(
        FakeCapture(), MicrophonePermissionService(lambda: False), services.coordinator
    )
    reference = ptt.press(VoiceInteractionContext(), user_gesture=True, visible=True)
    ptt.release()
    asyncio.run(
        services.providers.transcribe(services.providers.prepare_stt(reference), True, Event())
    )
    request, route = services.router.submit(reference, "扫描大文件")
    assert route.domain is RequestDomain.FILES
    root = tmp_path / "selected"
    root.mkdir()
    fixture = root / "synthetic.txt"
    fixture.write_text("synthetic fixture", encoding="utf-8")
    orchestrator = runtime.create_scan_orchestrator(root)
    plan, review = orchestrator.prepare_plan(root)
    assert review.approved
    with pytest.raises(ConfirmationError, match="not confirmed"):
        orchestrator.execute(plan, CancellationToken())
    consent = orchestrator.request_plan_confirmation(plan)
    orchestrator.resolve_plan_confirmation(consent.confirmation_id, True, plan)
    report = orchestrator.execute(plan, CancellationToken())
    assert report.summary.files_seen == 1
    assert fixture.read_text(encoding="utf-8") == "synthetic fixture"
    events = runtime.audit.list_recent(100)
    assert all(
        "扫描大文件"
        not in str((event.original_request, event.plan, event.parameters, event.result))
        for event in events
    )
    assert request.text not in repr(request)
    services.close()


def test_voice_audit_accepts_only_a_git_identifier(runtime, tmp_path, monkeypatch):
    for value in ("a" * 40, "invalid metadata"):
        monkeypatch.setenv("PC_MANAGER_GIT_COMMIT", value)
        services = build_voice_services(tmp_path, runtime.audit, VoiceSettings())
        try:
            services.coordinator.begin(VoiceInteractionContext())
            event = runtime.audit.list_recent(1)[0]
            assert event.git_commit == (value if value == "a" * 40 else None)
        finally:
            services.close()
