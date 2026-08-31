"""Guard strict authority-free schemas and the per-function documentation contract."""

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from pc_manager_agent.config.voice import VoiceSettings
from pc_manager_agent.domain.user_requests import RequestChannel, UserRequest
from pc_manager_agent.domain.voice import CapturedAudio, SpeechToTextResult, VoiceError
from pc_manager_agent.orchestration.user_requests import (
    UserRequestDispatcher,
    diagnostic_preparation_goal,
    optimization_preparation_goal,
)


def test_voice_api_covers_all_named_functions():
    repository = Path(__file__).resolve().parents[3]
    root = repository / "src/pc_manager_agent"
    paths = set(root.glob("**/*voice*.py"))
    for package in ("voice", "providers/speech_to_text", "providers/text_to_speech"):
        paths.update((root / package).glob("*.py"))
    paths.add(root / "domain/user_requests.py")
    paths.add(root / "orchestration/user_requests.py")
    paths.add(root / "providers/speech_logging.py")
    document = (repository / "docs/api-voice-interaction.md").read_text(encoding="utf-8")

    def check(node, prefix=""):
        for value in node.body:
            if isinstance(value, ast.ClassDef):
                check(value, prefix + value.name + ".")
            elif isinstance(value, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = prefix + value.name
                assert f"### `{name}`" in document
                signature = f"{name}({ast.unparse(value.args)}) -> "
                signature += ast.unparse(value.returns) if value.returns else "None"
                assert signature in document

    for path in paths:
        if path.name == "__init__.py":
            continue
        assert path.relative_to(root).as_posix() in document
        check(ast.parse(path.read_text(encoding="utf-8")))


def test_schemas_exclude_authority_and_payload_serialization():
    schema = UserRequest.model_json_schema()
    assert schema["additionalProperties"] is False
    assert "approved" not in schema["properties"]
    pcm = CapturedAudio(pcm=b"00")
    assert "pcm" not in pcm.model_dump()
    assert "pcm" not in CapturedAudio.model_json_schema(mode="serialization")["properties"]
    result = SpeechToTextResult(text="synthetic", is_final=True, provider="fake")
    assert "synthetic" not in repr(result) and "synthetic" not in result.model_dump_json()
    with pytest.raises(ValidationError):
        SpeechToTextResult.model_validate(
            {"text": "hello", "is_final": True, "provider": "fake", "tool": "run"}
        )
    assert str(VoiceError("private exception response")) == "VOICE_OPERATION_FAILED"
    with pytest.raises(ValidationError) as failure:
        CapturedAudio(pcm=b"synthetic-odd")
    assert "synthetic-odd" not in str(failure.value)


@pytest.mark.parametrize(
    "text", ["CPU", "内存", "磁盘", "进程", "服务列表", "已安装软件", "启动项", "性能", "系统状态"]
)
def test_diagnostic_goals_are_finite_and_do_not_contain_transcript(text):
    original = f"测试前缀，请检查{text}，谢谢"
    assert "测试前缀" not in diagnostic_preparation_goal(original)


def test_multi_action_and_vague_process_requests_require_clarification():
    dispatcher = UserRequestDispatcher()
    for text in ("检查内存然后关闭 Chrome", "close all processes"):
        assert dispatcher.route(
            UserRequest(channel=RequestChannel.VOICE, text=text)
        ).domain.value in {"AMBIGUOUS", "PROCESS"}


def test_environment_settings_exclude_secrets(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-unused")
    monkeypatch.setenv("PC_MANAGER_VOICE_PROVIDER", "openai")
    monkeypatch.setenv("PC_MANAGER_VOICE_MAX_VOICE_INPUT_SECONDS", "30")
    settings = VoiceSettings.from_environment()
    assert settings.provider == "openai" and settings.max_voice_input_seconds == 30
    assert "synthetic-unused" not in settings.model_dump_json()
    assert "synthetic-unused" not in repr(settings)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("C盘满了", "空间分析"),
        ("开机慢", "开机慢"),
        ("电脑慢", "电脑变慢"),
        ("优化建议", "快速检查"),
        ("后台占用", "后台负载"),
        ("响应慢", "响应性能、电脑变慢"),
    ],
)
def test_optimization_voice_keeps_minimal_explicit_scope(text, expected):
    assert optimization_preparation_goal(text) == expected


def test_readonly_service_query_is_not_service_mutation():
    route = UserRequestDispatcher().route(
        UserRequest(channel=RequestChannel.VOICE, text="查看服务列表")
    )
    assert route.domain.value == "DIAGNOSTICS"
