from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from pc_manager_agent.confirmation.privileged_actions import (
    PrivilegedActionConfirmationService,
)
from pc_manager_agent.domain.privileged_actions import (
    BrokerDecision,
    MachineMsiUninstallPayload,
    MockExecutionStatus,
    PrivilegedActionPlan,
    PrivilegedActionPreview,
    PrivilegedActionRequest,
    PrivilegedActionResult,
    PrivilegedActionType,
    PrivilegedVerificationStatus,
    PrivilegeRequirement,
    PrivilegeResolution,
    PrivilegeResolutionStatus,
    ServiceRestartPayload,
    ServiceStartupTypeChangePayload,
    canonical_model_digest,
)
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.service_actions import (
    ServiceStartupConfiguration,
    ServiceStartupType,
    ServiceState,
)
from pc_manager_agent.domain.software_uninstall_execution import MsiInstallContext
from pc_manager_agent.domain.system_diagnostics import SoftwareArchitecture, SoftwareScope
from pc_manager_agent.privileged.authentication import EphemeralHmacAuthenticator
from pc_manager_agent.privileged.builder import PrivilegedActionBuilder
from pc_manager_agent.privileged.registry import (
    PrivilegedActionManifest,
    PrivilegedActionRegistry,
    PrivilegedActionRegistryError,
    build_stage4x1_registry,
)
from pc_manager_agent.privileged.revalidation import (
    PrivilegedRevalidationError,
    ServicePrivilegedRevalidator,
)
from pc_manager_agent.privileged.serialization import (
    PrivilegedRequestSerializer,
    PrivilegedRequestTooLargeError,
    PrivilegedSerializationError,
    _canonical_json,
)
from tests.fixtures.privileged_actions import (
    build_privileged_test_stack,
    prepare_start,
    prepare_stop,
    required_resolution,
)


def _validated_request(request: PrivilegedActionRequest, **updates: object) -> None:
    raw = request.model_dump(mode="python")
    raw.update(updates)
    with pytest.raises(ValidationError):
        PrivilegedActionRequest.model_validate(raw)


def test_privilege_resolution_rejects_contradictory_outcomes() -> None:
    base = {
        "status": PrivilegeResolutionStatus.REQUIRED,
        "requirement": PrivilegeRequirement.ELEVATED_ADMIN_REQUIRED,
        "safety_allowed": True,
        "preflight_complete": True,
        "reason_code": "TEST",
        "explanation": "Synthetic evidence",
    }
    invalid = (
        {**base, "safety_allowed": False},
        {**base, "requirement": PrivilegeRequirement.UNKNOWN},
        {
            **base,
            "status": PrivilegeResolutionStatus.REQUIRED,
            "requirement": PrivilegeRequirement.SYSTEM_REQUIRED,
        },
    )
    for value in invalid:
        with pytest.raises(ValidationError):
            PrivilegeResolution.model_validate(value)
    assert len(required_resolution().canonical_digest()) == 64


def test_startup_and_msi_defined_only_payload_invariants(tmp_path: Path) -> None:
    stack = build_privileged_test_stack(tmp_path / "state.db")
    try:
        common = {
            "service_identity": stack.fake_service.identity,
            "expected_current_configuration": ServiceStartupConfiguration(
                startup_type=ServiceStartupType.MANUAL,
                delayed_auto_start=False,
            ),
            "expected_runtime_state": ServiceState.RUNNING,
            "impact_digest": "1" * 64,
            "backup_id": uuid4(),
            "backup_digest": "2" * 64,
        }
        with pytest.raises(ValidationError):
            ServiceStartupTypeChangePayload(
                **common,
                requested_startup_type=ServiceStartupType.MANUAL,
            )
        with pytest.raises(ValidationError):
            ServiceStartupTypeChangePayload(
                **{
                    **common,
                    "expected_current_configuration": ServiceStartupConfiguration(
                        startup_type=ServiceStartupType.AUTOMATIC,
                        delayed_auto_start=True,
                    ),
                },
                requested_startup_type=ServiceStartupType.MANUAL,
            )
        code = "{12345678-1234-1234-1234-123456789ABC}"
        with pytest.raises(ValidationError):
            MachineMsiUninstallPayload(
                product_code=code,
                product_code_digest="0" * 64,
                software_identity_digest="1" * 64,
                registration_digest="2" * 64,
            )
        payload = MachineMsiUninstallPayload(
            source_transaction_id=uuid4(),
            product_code=code,
            product_code_digest=canonical_model_digest(code),
            software_identity_digest="1" * 64,
            metadata_digest="2" * 64,
            capability_digest="3" * 64,
            registration_digest="2" * 64,
            execution_assessment_digest="4" * 64,
            preflight_digest="5" * 64,
            display_name="Synthetic Product",
            display_version="1.0",
            publisher="Example Publisher",
            install_context=MsiInstallContext.MACHINE,
            scope=SoftwareScope.LOCAL_MACHINE,
            architecture=SoftwareArchitecture.X64,
            source_anchor_digest="6" * 64,
        )
        assert payload.payload_type is PrivilegedActionType.MSI_UNINSTALL_MACHINE
    finally:
        stack.close()


def test_plan_preview_request_and_result_reject_contradictions(tmp_path: Path) -> None:
    stack = build_privileged_test_stack(tmp_path / "state.db")
    try:
        envelope = prepare_stop(stack)
        snapshot = stack.repository.snapshot(envelope.request)
        plan_raw = snapshot.plan.model_dump(mode="python")
        for updates in (
            {"payload_digest": "f" * 64},
            {"risk_level": RiskLevel.R2},
            {"created_at": datetime(2026, 1, 1)},
        ):
            with pytest.raises(ValidationError):
                PrivilegedActionPlan.model_validate({**plan_raw, **updates})

        blocked = required_resolution().model_copy(
            update={
                "status": PrivilegeResolutionStatus.BLOCKED,
                "safety_allowed": False,
            }
        )
        preview_raw = snapshot.preview.model_dump(mode="python")
        for updates in (
            {"risk_level": RiskLevel.R2},
            {"privilege_resolution": blocked},
            {"generated_at": datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=8)))},
        ):
            with pytest.raises(ValidationError):
                PrivilegedActionPreview.model_validate({**preview_raw, **updates})

        request = envelope.request
        restart = ServiceRestartPayload(
            service_identity=stack.fake_service.identity,
            expected_startup_configuration_digest=(
                stack.fake_service.startup_configuration.canonical_digest()
            ),
            expected_dependency_digest=stack.fake_service.dependency_digest,
        )
        invalid_request_updates = (
            {"payload_digest": "f" * 64},
            {"action_type": PrivilegedActionType.SERVICE_RESTART, "payload": request.payload},
            {"risk_level": RiskLevel.R2},
            {"privilege_requirement": PrivilegeRequirement.STANDARD_USER},
            {"expires_at": request.created_at},
            {"expires_at": request.created_at + timedelta(seconds=601)},
            {"created_at": request.created_at.replace(tzinfo=None)},
            {
                "created_at": request.created_at.astimezone(timezone(timedelta(hours=8))),
                "expires_at": request.expires_at.astimezone(timezone(timedelta(hours=8))),
            },
            {"payload": restart, "action_type": request.action_type},
        )
        for updates in invalid_request_updates:
            _validated_request(request, **updates)

        now = datetime.now(UTC)
        invalid_results = (
            {"execution_completed": True, "result_code": "BAD", "message": "bad"},
            {
                "execution_status": MockExecutionStatus.MOCK_VALIDATED,
                "result_code": "BAD",
                "message": "bad",
            },
            {
                "verification_status": PrivilegedVerificationStatus.VERIFIED,
                "result_code": "BAD",
                "message": "bad",
            },
            {"completed_at": now.replace(tzinfo=None), "result_code": "BAD", "message": "bad"},
        )
        for value in invalid_results:
            with pytest.raises(ValidationError):
                PrivilegedActionResult.model_validate(value)
    finally:
        stack.close()


def test_serialization_authentication_and_builder_constructor_guards(tmp_path: Path) -> None:
    for limit in (1_023, 1_048_577):
        with pytest.raises(ValueError):
            PrivilegedRequestSerializer(limit)
    serializer = PrivilegedRequestSerializer()
    assert serializer.max_request_bytes == 32_768
    with pytest.raises(PrivilegedSerializationError):
        serializer.deserialize(b"[]")
    with pytest.raises(PrivilegedSerializationError):
        _canonical_json(float("nan"))
    with pytest.raises(ValueError):
        EphemeralHmacAuthenticator(b"short")
    with pytest.raises(ValueError):
        EphemeralHmacAuthenticator(b"a" * 32, key_id="")
    generated = EphemeralHmacAuthenticator.generate()
    assert generated.verify(b"message", generated.sign(b"message"))

    stack = build_privileged_test_stack(tmp_path / "state.db")
    try:
        envelope = prepare_stop(stack)
        tiny = PrivilegedRequestSerializer(1_024)
        with pytest.raises(PrivilegedRequestTooLargeError):
            tiny.serialize(envelope)
        assert serializer.canonical_result_bytes(
            PrivilegedActionResult(
                broker_decision=BrokerDecision.REJECTED,
                result_code="REJECTED",
                message="Rejected",
            )
        )
        confirmations = PrivilegedActionConfirmationService(stack.repository)
        for ttl in (14, 601):
            with pytest.raises(ValueError):
                PrivilegedActionBuilder(
                    serializer,
                    generated,
                    confirmations,
                    request_ttl_seconds=ttl,
                )
    finally:
        stack.close()


def test_registry_and_action_specific_revalidation_fail_closed(tmp_path: Path) -> None:
    stack = build_privileged_test_stack(tmp_path / "state.db")
    try:
        handler = ServicePrivilegedRevalidator(stack.fake_state)
        registry = build_stage4x1_registry(handler)
        assert registry.action_types == (
            PrivilegedActionType.SERVICE_START,
            PrivilegedActionType.SERVICE_STOP,
        )
        with pytest.raises(PrivilegedActionRegistryError):
            registry.require(PrivilegedActionType.SERVICE_RESTART)
        manifest = registry.require(PrivilegedActionType.SERVICE_STOP)
        with pytest.raises(PrivilegedActionRegistryError):
            registry.register(manifest)
        for risk, privilege in (
            (RiskLevel.R2, PrivilegeRequirement.ELEVATED_ADMIN_REQUIRED),
            (RiskLevel.R3, PrivilegeRequirement.STANDARD_USER),
        ):
            isolated = PrivilegedActionRegistry()
            with pytest.raises(PrivilegedActionRegistryError):
                isolated.register(
                    PrivilegedActionManifest(
                        action_type=PrivilegedActionType.SERVICE_STOP,
                        payload_model=manifest.payload_model,
                        risk_floor=risk,
                        required_privilege=privilege,
                        handler=handler,
                        audit_policy="DIGESTS_ONLY",
                    )
                )

        envelope = prepare_stop(stack)
        request = envelope.request
        with pytest.raises(PrivilegedRevalidationError) as error:
            handler.require(
                request.model_copy(
                    update={
                        "payload": ServiceRestartPayload(
                            service_identity=stack.fake_service.identity,
                            expected_startup_configuration_digest=(
                                stack.fake_service.startup_configuration.canonical_digest()
                            ),
                            expected_dependency_digest=stack.fake_service.dependency_digest,
                        )
                    }
                )
            )
        assert error.value.decision is BrokerDecision.ACTION_NOT_ALLOWLISTED
        assert not handler.execute(request.model_copy(update={"payload": "unsupported"}))
        assert handler.verify(request.model_copy(update={"payload": "unsupported"})) is None
    finally:
        stack.close()


@pytest.mark.parametrize(
    ("change", "expected"),
    (
        ("missing", BrokerDecision.TARGET_CHANGED),
        ("identity", BrokerDecision.TARGET_CHANGED),
        ("safety", BrokerDecision.SAFETY_BLOCKED),
        ("privilege", BrokerDecision.PRIVILEGE_UNSUPPORTED),
        ("state", BrokerDecision.PRECONDITION_FAILED),
        ("configuration", BrokerDecision.TARGET_CHANGED),
        ("dependency", BrokerDecision.TARGET_CHANGED),
    ),
)
def test_revalidator_rejects_each_fresh_evidence_change(
    tmp_path: Path,
    change: str,
    expected: BrokerDecision,
) -> None:
    stack = build_privileged_test_stack(tmp_path / f"{change}.db")
    try:
        envelope = prepare_stop(stack)
        current = stack.fake_service
        if change == "missing":
            state = type(stack.fake_state)()
        else:
            if change == "identity":
                current = current.model_copy(
                    update={
                        "identity": current.identity.model_copy(
                            update={"binary_path_fingerprint": "f" * 64}
                        )
                    }
                )
            elif change == "safety":
                current = current.model_copy(update={"safety_allowed": False})
            elif change == "privilege":
                current = current.model_copy(
                    update={
                        "privilege_resolution": required_resolution().model_copy(
                            update={
                                "status": PrivilegeResolutionStatus.NOT_REQUIRED,
                                "requirement": PrivilegeRequirement.STANDARD_USER,
                            }
                        )
                    }
                )
            elif change == "state":
                current = current.model_copy(update={"state": ServiceState.STOPPED})
            elif change == "configuration":
                current = current.model_copy(
                    update={
                        "startup_configuration": ServiceStartupConfiguration(
                            startup_type=ServiceStartupType.AUTOMATIC,
                            delayed_auto_start=False,
                        )
                    }
                )
            else:
                current = current.model_copy(update={"dependency_digest": "f" * 64})
            state = type(stack.fake_state)((current,))
        handler = ServicePrivilegedRevalidator(state)
        with pytest.raises(PrivilegedRevalidationError) as error:
            handler.require(envelope.request)
        assert error.value.decision is expected
    finally:
        stack.close()


def test_fake_start_executor_and_verifier_are_finite(tmp_path: Path) -> None:
    stack = build_privileged_test_stack(tmp_path / "state.db")
    try:
        stack.fake_state.set_service_state(
            stack.fake_service.identity.service_name,
            ServiceState.STOPPED,
        )
        envelope = prepare_start(stack)
        handler = ServicePrivilegedRevalidator(stack.fake_state)
        assert handler.require(envelope.request).state is ServiceState.STOPPED
        assert handler.execute(envelope.request)
        verified = handler.verify(envelope.request)
        assert verified is not None and verified.state is ServiceState.RUNNING
    finally:
        stack.close()
