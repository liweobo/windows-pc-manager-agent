from __future__ import annotations

from dataclasses import replace

import pytest
from pydantic import BaseModel

from pc_manager_agent.agents.base import AgentIdentityFactory
from pc_manager_agent.domain.agents import (
    AgentCapabilityManifest,
    AgentRole,
    AgentToolProposal,
)
from pc_manager_agent.domain.context import DataClassification
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.safety.agent_capabilities import (
    AgentCapabilityError,
    AgentCapabilityRegistry,
    AgentDelegationPolicy,
    AgentToolAccessPolicy,
    build_agent_capability_registry,
    manifest_mapping,
)
from pc_manager_agent.tools.manifest import CancellationToken, ToolManifest
from pc_manager_agent.tools.registry import ToolRegistry


class _Input(BaseModel):
    value: int


class _Output(BaseModel):
    value: int


class _Tool:
    manifest = ToolManifest(
        name="file.scan",
        description="synthetic read",
        input_model=_Input,
        output_model=_Output,
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
        audit_fields=(),
        supported_platforms=("test",),
    )

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        assert not cancellation.is_cancelled
        return _Output(value=_Input.model_validate(request).value)


class _WriteTool:
    manifest = replace(
        _Tool.manifest,
        risk_level=RiskLevel.R1,
        read_only=False,
        supports_preview=True,
        rollback_level=RollbackLevel.FULL,
    )

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        return _Output(value=_Input.model_validate(request).value)


def test_default_matrix_is_complete_and_model_roles_cannot_write_memory() -> None:
    registry = build_agent_capability_registry()
    assert set(registry.roles) == set(AgentRole)
    for role in registry.roles:
        manifest = registry.manifest(role)
        assert not (manifest.model_backed and manifest.may_write_memory)
        assert manifest.may_execute_actions is False
        assert manifest.may_request_confirmation is False
    assert registry.manifest(AgentRole.FILE).proposed_tools == (
        "file.scan",
        "file.analyze.large",
        "file.analyze.inactive",
        "file.analyze.duplicates",
    )


def test_registry_defaults_deny_mutation_duplicate_and_incomplete_matrix() -> None:
    registry = AgentCapabilityRegistry()
    manifest = AgentCapabilityManifest(
        role=AgentRole.FILE,
        version="v1",
        readable_data=(DataClassification.USER_DATA,),
    )
    registry.register(manifest)
    with pytest.raises(AgentCapabilityError, match="Duplicate"):
        registry.register(manifest)
    with pytest.raises(AgentCapabilityError, match="Missing"):
        registry.seal()
    complete = build_agent_capability_registry()
    with pytest.raises(AgentCapabilityError, match="sealed"):
        complete.register(manifest)
    unsealed = AgentCapabilityRegistry()
    with pytest.raises(AgentCapabilityError, match="not sealed"):
        unsealed.manifest(AgentRole.FILE)
    with pytest.raises(ValueError, match="Model-backed"):
        AgentCapabilityManifest(
            role=AgentRole.PLANNER,
            version="v1",
            model_backed=True,
            may_write_memory=True,
        )


def test_tool_access_validates_runtime_identity_role_and_schema() -> None:
    capabilities = build_agent_capability_registry()
    policy = AgentToolAccessPolicy(capabilities)
    registry = ToolRegistry()
    registry.register(_Tool())
    identity = AgentIdentityFactory(capabilities).create(AgentRole.FILE)
    proposal = AgentToolProposal(
        task_id="11111111-1111-1111-1111-111111111111",
        node_id="22222222-2222-2222-2222-222222222222",
        tool_name="file.scan",
        arguments={"value": 7},
        rationale_code="READ_APPROVED_SCOPE",
    )
    assert _Input.model_validate(policy.validate(identity, proposal, registry)).value == 7

    forged = identity.model_copy(update={"manifest_digest": "0" * 64})
    with pytest.raises(AgentCapabilityError, match="identity"):
        policy.validate(forged, proposal, registry)
    with pytest.raises(AgentCapabilityError, match="not allowed"):
        policy.validate(
            AgentIdentityFactory(capabilities).create(AgentRole.PLANNER),
            proposal,
            registry,
        )

    write_registry = ToolRegistry()
    write_registry.register(_WriteTool())
    with pytest.raises(AgentCapabilityError, match="read-only"):
        policy.validate(identity, proposal, write_registry)


def test_delegation_is_narrowed_and_stale_or_escalating_requests_are_rejected() -> None:
    capabilities = build_agent_capability_registry()
    policy = AgentDelegationPolicy(capabilities)
    orchestrator = AgentIdentityFactory(capabilities).create(AgentRole.ORCHESTRATOR)
    assert policy.narrow(orchestrator, AgentRole.FILE, ("file.scan",)) == ("file.scan",)

    stale = orchestrator.model_copy(update={"manifest_digest": "0" * 64})
    with pytest.raises(AgentCapabilityError, match="stale"):
        policy.narrow(stale, AgentRole.FILE, ("file.scan",))
    with pytest.raises(AgentCapabilityError, match="cannot delegate"):
        policy.narrow(orchestrator, AgentRole.MEMORY_MANAGER, ())
    with pytest.raises(AgentCapabilityError, match="CAPABILITY_ESCALATION"):
        policy.narrow(orchestrator, AgentRole.FILE, ("file.move",))

    mapping = manifest_mapping(capabilities)
    assert mapping[AgentRole.FILE] == capabilities.manifest(AgentRole.FILE)
