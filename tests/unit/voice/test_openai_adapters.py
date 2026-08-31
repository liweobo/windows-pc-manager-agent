"""Official SDK calls over a local mock transport: no credentials, network, microphone or cost."""

import asyncio
import json
import logging

import httpx
import pytest
from openai import AsyncOpenAI

from pc_manager_agent.config.voice import VoiceSettings
from pc_manager_agent.domain.voice import CapturedAudio, VoiceError
from pc_manager_agent.providers.speech_to_text.openai import OpenAISpeechToTextProvider
from pc_manager_agent.providers.text_to_speech.openai import OpenAITextToSpeechProvider


def test_sdk_requests_are_bounded_fixed_endpoint_and_no_fake_confidence():
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.path.endswith("transcriptions"):
            return httpx.Response(
                200, json={"text": "检查内存"}, headers={"x-request-id": "fake-trace"}
            )
        return httpx.Response(200, content=b"\0\0" * 240)

    async def scenario():
        async with AsyncOpenAI(
            api_key="synthetic-unused",
            max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        ) as client:
            stt = OpenAISpeechToTextProvider(VoiceSettings(), client)
            tts = OpenAITextToSpeechProvider(VoiceSettings(), client)
            assert "api.openai.com" in stt.destination and "coral" in tts.destination
            result = await stt.transcribe(CapturedAudio(pcm=b"\0\0" * 20), "zh")
            assert result.confidence is None and result.provider_request_id == "fake-trace"
            audio = await tts.synthesize("请查看屏幕。")
            assert len(audio.pcm) == 480

    asyncio.run(scenario())
    assert len(requests) == 2
    assert b'filename="input.wav"' in requests[0].content
    assert b"RIFF" in requests[0].content
    assert json.loads(requests[1].content)["response_format"] == "pcm"


@pytest.mark.parametrize("status", [401, 429, 500])
def test_provider_errors_no_retry_or_sensitive_public_message(status):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(status, json={"error": {"message": "private-body", "type": "fake"}})

    async def scenario():
        async with AsyncOpenAI(
            api_key="synthetic-unused",
            max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        ) as client:
            with pytest.raises(VoiceError, match=r"^VOICE_STT_REQUEST_FAILED$"):
                await OpenAISpeechToTextProvider(VoiceSettings(), client).transcribe(
                    CapturedAudio(pcm=b"\0\0"), "auto"
                )
            with pytest.raises(VoiceError, match=r"^VOICE_TTS_REQUEST_FAILED$"):
                await OpenAITextToSpeechProvider(VoiceSettings(), client).synthesize("安全提示")

    asyncio.run(scenario())
    assert len(requests) == 2


def test_empty_configuration_and_output_limit():
    with pytest.raises(VoiceError, match="CONFIGURATION"):
        asyncio.run(
            OpenAISpeechToTextProvider(VoiceSettings()).transcribe(CapturedAudio(pcm=b"00"), "auto")
        )
    tts = OpenAITextToSpeechProvider(VoiceSettings())
    with pytest.raises(VoiceError, match="CONFIGURATION"):
        asyncio.run(tts.synthesize("安全提示"))
    with pytest.raises(VoiceError, match="LENGTH"):
        asyncio.run(tts.synthesize(""))


def test_oversize_tts_stops_stream():
    async def scenario():
        async with AsyncOpenAI(
            api_key="synthetic-unused",
            max_retries=0,
            http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda _: httpx.Response(200, content=b"00" * (120 * 24000 + 1))
                )
            ),
        ) as client:
            with pytest.raises(VoiceError, match="AUDIO_LIMIT"):
                await OpenAITextToSpeechProvider(VoiceSettings(), client).synthesize("安全提示")

    asyncio.run(scenario())


@pytest.mark.parametrize("logger_name", ["openai", "openai._base_client", "httpx", "httpcore"])
def test_debug_request_logging_blocks_before_payload_or_network(caplog, logger_name):
    with caplog.at_level(logging.DEBUG, logger=logger_name):
        with pytest.raises(VoiceError, match="VERBOSE_PROVIDER_LOGGING_BLOCKED"):
            asyncio.run(
                OpenAISpeechToTextProvider(VoiceSettings()).transcribe(
                    CapturedAudio(pcm=b"synthetic-pcm!"), "auto"
                )
            )
        with pytest.raises(VoiceError, match="VERBOSE_PROVIDER_LOGGING_BLOCKED"):
            asyncio.run(OpenAITextToSpeechProvider(VoiceSettings()).synthesize("安全摘要"))
    assert "synthetic-pcm" not in caplog.text
