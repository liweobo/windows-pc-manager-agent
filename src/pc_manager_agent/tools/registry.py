"""Allow-list registry for deterministic tools."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ValidationError

from pc_manager_agent.tools.manifest import CancellationToken, RegisteredTool, ToolManifest


class ToolRegistryError(RuntimeError):
    """Base registry failure."""


class UnknownToolError(ToolRegistryError):
    """Raised when a plan references a non-registered tool."""


class ToolInputError(ToolRegistryError):
    """Raised when tool arguments fail their declared schema."""


class ToolOutputError(ToolRegistryError):
    """Raised when a tool violates its declared output schema."""


class ToolRegistry:
    """Registry that is the only route to deterministic tool execution."""

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, tool: RegisteredTool) -> None:
        """Register one tool and reject name collisions."""
        name = tool.manifest.name
        if name in self._tools:
            msg = f"Tool already registered: {name}"
            raise ToolRegistryError(msg)
        self._tools[name] = tool

    def manifest(self, name: str) -> ToolManifest:
        """Return a registered manifest or fail closed."""
        try:
            return self._tools[name].manifest
        except KeyError as exc:
            raise UnknownToolError(name) from exc

    def validate_input(self, name: str, arguments: Mapping[str, object]) -> BaseModel:
        """Validate raw plan arguments against the registered input schema."""
        manifest = self.manifest(name)
        try:
            return manifest.input_model.model_validate(dict(arguments))
        except ValidationError as exc:
            raise ToolInputError(str(exc)) from exc

    def execute(
        self,
        name: str,
        arguments: Mapping[str, object],
        cancellation: CancellationToken | None = None,
    ) -> BaseModel:
        """Validate input, execute an allow-listed tool, and validate output."""
        try:
            tool = self._tools[name]
        except KeyError as exc:
            raise UnknownToolError(name) from exc
        request = self.validate_input(name, arguments)
        token = cancellation or CancellationToken()
        result = tool.execute(request, token)
        if not isinstance(result, tool.manifest.output_model):
            actual = type(result).__name__
            expected = tool.manifest.output_model.__name__
            msg = f"Tool {name} returned {actual}, expected {expected}"
            raise ToolOutputError(msg)
        return result

    @property
    def names(self) -> tuple[str, ...]:
        """Return registered tool names in deterministic order."""
        return tuple(sorted(self._tools))
