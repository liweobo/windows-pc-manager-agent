from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict

from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.tools.manifest import CancellationToken, ToolManifest
from pc_manager_agent.tools.registry import (
    ToolInputError,
    ToolOutputError,
    ToolRegistry,
    ToolRegistryError,
    UnknownToolError,
)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int


class Output(BaseModel):
    value: int


class ExampleTool:
    def __init__(self, *, wrong_output: bool = False) -> None:
        self._wrong_output = wrong_output
        self._manifest = ToolManifest(
            name="test.echo",
            description="echo",
            input_model=Input,
            output_model=Output,
            risk_level=RiskLevel.R0,
            required_permissions=(),
            read_only=True,
            idempotent=True,
            supports_cancellation=True,
            rollback_level=RollbackLevel.NONE,
            preconditions=(),
            postconditions=(),
            timeout_seconds=1,
            max_batch_size=1,
            audit_fields=("value",),
            supported_platforms=("windows",),
        )

    @property
    def manifest(self) -> ToolManifest:
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        assert isinstance(request, Input)
        if self._wrong_output:
            return Input(value=request.value)
        return Output(value=request.value)


def test_registry_validates_and_executes_registered_tool() -> None:
    registry = ToolRegistry()
    registry.register(ExampleTool())
    result = registry.execute("test.echo", {"value": 7})
    assert result == Output(value=7)
    assert registry.names == ("test.echo",)


def test_registry_rejects_unknown_duplicate_invalid_and_wrong_output() -> None:
    registry = ToolRegistry()
    with pytest.raises(UnknownToolError):
        registry.execute("test.missing", {})
    tool = ExampleTool()
    registry.register(tool)
    with pytest.raises(ToolRegistryError):
        registry.register(tool)
    with pytest.raises(ToolInputError):
        registry.execute("test.echo", {"value": "bad", "extra": 1})
    wrong = ToolRegistry()
    wrong.register(ExampleTool(wrong_output=True))
    with pytest.raises(ToolOutputError):
        wrong.execute("test.echo", {"value": 1})


def test_manifest_rejects_invalid_or_contradictory_metadata() -> None:
    base = {
        "description": "x",
        "input_model": Input,
        "output_model": Output,
        "risk_level": RiskLevel.R0,
        "required_permissions": (),
        "read_only": True,
        "idempotent": True,
        "supports_cancellation": True,
        "rollback_level": RollbackLevel.NONE,
        "preconditions": (),
        "postconditions": (),
        "timeout_seconds": 1,
        "max_batch_size": 1,
        "audit_fields": (),
        "supported_platforms": ("windows",),
    }
    with pytest.raises(ValueError, match="Invalid tool name"):
        ToolManifest(name="INVALID", **base)
    with pytest.raises(ValueError, match="read-only"):
        ToolManifest(name="test.write", **{**base, "read_only": False})
    with pytest.raises(ValueError, match="positive"):
        ToolManifest(name="test.zero", **{**base, "timeout_seconds": 0})
    with pytest.raises(ValueError, match="R2"):
        ToolManifest(
            name="test.r2-without-runtime-confirmation",
            **{
                **base,
                "risk_level": RiskLevel.R2,
                "read_only": False,
                "rollback_level": RollbackLevel.MANUAL,
                "supports_preview": True,
            },
        )
    with pytest.raises(ValueError, match="reserved for R2"):
        ToolManifest(
            name="test.r1-runtime-confirmation",
            **{
                **base,
                "risk_level": RiskLevel.R1,
                "read_only": False,
                "rollback_level": RollbackLevel.FULL,
                "supports_preview": True,
                "requires_runtime_confirmation": True,
            },
        )


def test_cancellation_token() -> None:
    token = CancellationToken()
    assert not token.is_cancelled
    token.cancel()
    assert token.is_cancelled
