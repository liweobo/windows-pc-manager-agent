"""Stage 4X2 framing, authentication, trust, and availability unit tests."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from pc_manager_agent.domain.elevated_broker import (
    BrokerBinaryIdentity,
    BrokerTrustMode,
    IpcMessageType,
    SignatureStatus,
    WindowsProcessIdentity,
)
from pc_manager_agent.platform_support.windows.named_pipe import pipe_name
from pc_manager_agent.privileged.availability import (
    BrokerAvailabilityStatus,
    PrivilegedBrokerAvailabilityService,
)
from pc_manager_agent.privileged.broker_identity import BrokerTrustError, BrokerTrustPolicy
from pc_manager_agent.privileged.ipc_protocol import (
    BrokerFrameCodec,
    BrokerIpcAuthenticationError,
    BrokerIpcProtocolError,
    BrokerMessageSequence,
    IpcSessionAuthenticator,
    build_authenticated_frame,
    build_plain_frame,
    verify_authenticated_frame,
)


def _binary(*, signature: SignatureStatus = SignatureStatus.VALID) -> BrokerBinaryIdentity:
    return BrokerBinaryIdentity(
        path_hash="1" * 64,
        file_id="volume:file",
        sha256="2" * 64,
        size_bytes=4096,
        product_version="0.1.0",
        signature_status=signature,
        signer_fingerprint="3" * 64 if signature is SignatureStatus.VALID else None,
        trusted_location=True,
    )


def _process(*, elevated: bool = False, session_id: int = 7) -> WindowsProcessIdentity:
    return WindowsProcessIdentity(
        user_sid="S-1-5-21-1-2-3-1001",
        session_id=session_id,
        process_id=100 if not elevated else 200,
        process_creation_time_ns=1,
        image_path_hash="4" * 64,
        image_sha256="2" * 64 if elevated else "5" * 64,
        product_version="0.1.0",
        elevated=elevated,
        integrity_level="HIGH" if elevated else "MEDIUM",
    )


def test_length_prefixed_frame_round_trip() -> None:
    codec = BrokerFrameCodec(2048)
    frame = build_plain_frame(
        message_type=IpcMessageType.CLIENT_HELLO,
        broker_instance_id=uuid4(),
        request_id=uuid4(),
        sequence=1,
        payload={"purpose": "test"},
    )
    assert codec.decode_prefixed(codec.encode(frame)) == frame


@pytest.mark.parametrize(
    "payload",
    [b'{"protocol_version":1,"protocol_version":1}', b'{"value":NaN}'],
)
def test_decoder_rejects_duplicate_keys_and_nonfinite_numbers(payload: bytes) -> None:
    with pytest.raises(BrokerIpcProtocolError):
        BrokerFrameCodec().decode_payload(payload)


def test_decoder_rejects_oversized_prefix_before_reading_payload() -> None:
    codec = BrokerFrameCodec(1024)
    with pytest.raises(BrokerIpcProtocolError):
        codec.decode_prefixed((1025).to_bytes(4, "little"))


def test_authenticated_frame_rejects_payload_tamper() -> None:
    authenticator = IpcSessionAuthenticator(b"x" * 32, key_id="test.session")
    frame = build_authenticated_frame(
        message_type=IpcMessageType.REQUEST,
        broker_instance_id=uuid4(),
        request_id=uuid4(),
        sequence=4,
        payload={"value": "original"},
        authenticator=authenticator,
    )
    tampered = frame.model_copy(update={"integrity": authenticator.sign(b"wrong")})
    with pytest.raises(BrokerIpcAuthenticationError):
        verify_authenticated_frame(tampered, authenticator)


def test_authenticated_frame_uses_a_new_ephemeral_key() -> None:
    first = IpcSessionAuthenticator.generate(key_id="first")
    second = IpcSessionAuthenticator.generate(key_id="second")
    message = b"one request"
    assert first.verify(message, first.sign(message))
    assert not second.verify(message, first.sign(message))


def test_development_trust_requires_exact_hash_and_manifest() -> None:
    identity = _binary()
    BrokerTrustPolicy(BrokerTrustMode.DEVELOPMENT, identity.sha256).require_binary(identity)
    with pytest.raises(BrokerTrustError):
        BrokerTrustPolicy(BrokerTrustMode.DEVELOPMENT, "f" * 64).require_binary(identity)


def test_production_trust_requires_pinned_valid_signer() -> None:
    identity = _binary()
    policy = BrokerTrustPolicy(
        BrokerTrustMode.PRODUCTION,
        identity.sha256,
        expected_signer_fingerprint=identity.signer_fingerprint,
    )
    policy.require_binary(identity)
    with pytest.raises(BrokerTrustError):
        BrokerTrustPolicy(BrokerTrustMode.PRODUCTION, identity.sha256).require_binary(identity)


@pytest.mark.parametrize("different", ["caller", "session", "account", "broker_hash"])
def test_pipe_peer_policy_rejects_identity_substitution(different: str) -> None:
    caller = _process()
    expected = caller
    broker = _process(elevated=True)
    binary = _binary()
    if different == "caller":
        expected = caller.model_copy(update={"process_id": 101})
    elif different == "session":
        broker = broker.model_copy(update={"session_id": 8})
    elif different == "account":
        broker = broker.model_copy(update={"user_sid": "S-1-5-21-9-9-9-1002"})
    else:
        broker = broker.model_copy(update={"image_sha256": "f" * 64})
    with pytest.raises(BrokerTrustError):
        BrokerTrustPolicy.require_pipe_peers(
            caller=caller,
            expected_caller=expected,
            broker=broker,
            expected_broker_binary=binary,
        )


class _Inspector:
    def __init__(self, identity: BrokerBinaryIdentity) -> None:
        self.identity = identity

    def inspect(self, path: Path) -> BrokerBinaryIdentity:
        del path
        return self.identity


def test_availability_fails_closed_for_elevated_main(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pc_manager_agent.privileged.availability.sys.platform", "win32")
    result = PrivilegedBrokerAvailabilityService(
        _Inspector(_binary()),
        enabled=True,
        broker_path=Path("C:/Program Files/Agent/broker.exe"),
        expected_sha256="2" * 64,
        trust_mode=BrokerTrustMode.DEVELOPMENT,
        caller_identity=_process(elevated=True),
    ).inspect()
    assert result.status is BrokerAvailabilityStatus.MAIN_AGENT_ELEVATED
    assert not result.ready


def test_availability_reports_ready_only_after_trust(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pc_manager_agent.privileged.availability.sys.platform", "win32")
    result = PrivilegedBrokerAvailabilityService(
        _Inspector(_binary()),
        enabled=True,
        broker_path=Path("C:/build/broker.exe"),
        expected_sha256="2" * 64,
        trust_mode=BrokerTrustMode.DEVELOPMENT,
        caller_identity=_process(),
    ).inspect()
    assert result.status is BrokerAvailabilityStatus.READY
    assert result.ready


def test_pipe_endpoint_name_accepts_only_one_opaque_identifier() -> None:
    assert pipe_name("a" * 43).endswith("a" * 43)
    for invalid in ("short", "../" + "a" * 40, "a" * 44):
        with pytest.raises(ValueError):
            pipe_name(invalid)


def test_frame_json_never_uses_python_object_serialization() -> None:
    frame = build_plain_frame(
        message_type=IpcMessageType.BROKER_READY,
        broker_instance_id=uuid4(),
        request_id=uuid4(),
        sequence=0,
        payload={"encoding": "strict-json"},
    )
    decoded = json.loads(BrokerFrameCodec().encode(frame)[4:])
    assert decoded["payload"] == {"encoding": "strict-json"}


def test_authenticator_and_codec_configuration_bounds() -> None:
    with pytest.raises(ValueError):
        IpcSessionAuthenticator(b"short", key_id="test")
    with pytest.raises(ValueError):
        IpcSessionAuthenticator(b"x" * 32, key_id="")
    with pytest.raises(ValueError):
        BrokerFrameCodec(100)


def test_codec_rejects_trailing_truncated_wrong_version_and_large_encode() -> None:
    codec = BrokerFrameCodec(1024)
    frame = build_plain_frame(
        message_type=IpcMessageType.BROKER_READY,
        broker_instance_id=uuid4(),
        request_id=uuid4(),
        sequence=0,
        payload={"x": "y"},
    )
    encoded = codec.encode(frame)
    for invalid in (encoded[:-1], encoded + b"x", b"\x00\x00\x00\x00"):
        with pytest.raises(BrokerIpcProtocolError):
            codec.decode_prefixed(invalid)
    with pytest.raises(BrokerIpcProtocolError, match="version"):
        codec.decode_payload(b'{"protocol_version":2}')
    large = frame.model_copy(update={"payload": {"value": "x" * 2000}})
    with pytest.raises(BrokerIpcProtocolError, match="exceeds"):
        codec.encode(large)


def test_fixed_message_sequence_rejects_wrong_order_and_count() -> None:
    sequence = BrokerMessageSequence()
    first = build_plain_frame(
        message_type=IpcMessageType.BROKER_READY,
        broker_instance_id=uuid4(),
        request_id=uuid4(),
        sequence=0,
        payload={"x": "y"},
    )
    sequence.require(first, IpcMessageType.BROKER_READY)
    with pytest.raises(BrokerIpcProtocolError, match="sequence"):
        sequence.require(first, IpcMessageType.BROKER_READY)


@pytest.mark.parametrize(
    ("enabled", "platform", "path", "sha", "expected"),
    [
        (False, "win32", Path("C:/broker.exe"), "2" * 64, BrokerAvailabilityStatus.DISABLED),
        (
            True,
            "linux",
            Path("C:/broker.exe"),
            "2" * 64,
            BrokerAvailabilityStatus.UNSUPPORTED_PLATFORM,
        ),
        (True, "win32", None, None, BrokerAvailabilityStatus.NOT_CONFIGURED),
    ],
)
def test_availability_reports_early_fail_closed_reasons(
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
    platform: str,
    path: Path | None,
    sha: str | None,
    expected: BrokerAvailabilityStatus,
) -> None:
    monkeypatch.setattr("pc_manager_agent.privileged.availability.sys.platform", platform)
    service = PrivilegedBrokerAvailabilityService(
        _Inspector(_binary()),
        enabled=enabled,
        broker_path=path,
        expected_sha256=sha,
        trust_mode=BrokerTrustMode.DEVELOPMENT,
        caller_identity=_process(),
    )
    assert service.inspect().status is expected
    with pytest.raises(BrokerTrustError):
        service.require_ready()


def test_binary_and_peer_trust_reject_reparse_manifest_and_standard_broker() -> None:
    identity = _binary()
    policy = BrokerTrustPolicy(BrokerTrustMode.DEVELOPMENT, identity.sha256)
    for update in (
        {"reparse_point": True},
        {"manifest_execution_level": "highestAvailable"},
    ):
        with pytest.raises((BrokerTrustError, ValueError)):
            policy.require_binary(identity.model_copy(update=update))
    caller = _process()
    with pytest.raises(BrokerTrustError, match="exactly HIGH"):
        BrokerTrustPolicy.require_pipe_peers(
            caller=caller,
            expected_caller=caller,
            broker=_process(),
            expected_broker_binary=identity,
        )


def test_pipe_peer_policy_rejects_system_integrity_broker() -> None:
    """Stage 4X3 permits a HIGH UAC Broker, never SYSTEM or TrustedInstaller."""
    caller = _process()
    with pytest.raises(BrokerTrustError, match="exactly HIGH"):
        BrokerTrustPolicy.require_pipe_peers(
            caller=caller,
            expected_caller=caller,
            broker=_process(elevated=True).model_copy(update={"integrity_level": "SYSTEM"}),
            expected_broker_binary=_binary(),
        )
