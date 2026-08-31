"""Voice is untrusted input, never a new writer, background listener or Broker capability."""

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from pc_manager_agent.domain.user_requests import UserRequest, VoiceInteractionContext

ROOT = Path(__file__).resolve().parents[2]


def test_voice_core_has_no_system_writer_or_confirmation_dependency():
    for path in (ROOT / "src/pc_manager_agent/voice").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert not any(
                    part in module
                    for part in (
                        "platform_support",
                        "broker",
                        "tools.registry",
                        "confirmation.state_machine",
                        "confirmation.privileged",
                        "PySide6",
                    )
                )
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"eval", "exec", "compile"}
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {
                    "Popen",
                    "system",
                    "ShellExecuteEx",
                    "resolve_plan_confirmation",
                }


def test_broker_entry_import_graph_has_no_voice_or_audio():
    source = ROOT / "src"
    pending = ["pc_manager_agent.broker.main"]
    visited = set()
    while pending:
        module = pending.pop()
        if module in visited:
            continue
        visited.add(module)
        assert not any(
            part in module for part in ("voice", "speech_to_text", "text_to_speech", "QtMultimedia")
        )
        path = source.joinpath(*module.split(".")).with_suffix(".py")
        if not path.exists():
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                pending.append(node.module)
            elif isinstance(node, ast.Import):
                pending.extend(alias.name for alias in node.names)


@pytest.mark.parametrize(
    "field", ["tool_name", "command", "approved", "risk_level", "administrator", "payload"]
)
def test_voice_context_cannot_carry_authority(field):
    with pytest.raises(ValidationError):
        VoiceInteractionContext.model_validate({field: "anything"})
    with pytest.raises(ValidationError):
        UserRequest.model_validate({"channel": "VOICE", "text": "yes", field: "anything"})
