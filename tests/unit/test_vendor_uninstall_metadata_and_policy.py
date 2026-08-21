"""Unit coverage for strict Vendor metadata, argv, argument, and trust boundaries."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from pc_manager_agent.domain.software_uninstall_analysis import (
    RawInstalledSoftwareEntry,
    RegistryHive,
    RegistryView,
    SoftwareSource,
)
from pc_manager_agent.domain.system_diagnostics import SoftwareArchitecture, SoftwareScope
from pc_manager_agent.domain.vendor_uninstall import (
    VendorArgumentDecision,
    VendorAuthenticodeEvidence,
    VendorAuthenticodeStatus,
    VendorExecutableFileIdentity,
    VendorExecutableObservation,
    VendorInstallLocationRelation,
    VendorPublisherMatch,
    VendorTrustDecision,
)
from pc_manager_agent.orchestration.software_inventory import normalize_raw_entry
from pc_manager_agent.orchestration.vendor_uninstall_metadata import (
    VendorMetadataError,
    VendorUninstallMetadataParser,
    windows_command_line_to_argv,
)
from pc_manager_agent.safety import vendor_executable_trust as trust_module
from pc_manager_agent.safety.vendor_argument_policy import VendorArgumentPolicy
from pc_manager_agent.safety.vendor_executable_trust import (
    VendorExecutableTrustError,
    VendorExecutableTrustValidator,
    conservative_publisher_match,
    resolve_vendor_executable,
)


class _ExecutablePlatform:
    def __init__(self, observation: VendorExecutableObservation) -> None:
        self.observation = observation

    def inspect(
        self,
        executable: Path,
        install_location: Path,
        publisher: str,
    ) -> VendorExecutableObservation:
        assert executable == Path(r"C:\Program Files\Example\uninstall.exe")
        assert install_location == Path(r"C:\Program Files\Example")
        assert publisher == "Example Corporation"
        return self.observation


def _raw(
    command: str | None = '"C:\\Program Files\\Example\\uninstall.exe" /remove',
    *,
    hive: RegistryHive = RegistryHive.CURRENT_USER,
) -> RawInstalledSoftwareEntry:
    return RawInstalledSoftwareEntry(
        raw_source_id=r"HKCU\Software\Example",
        source=SoftwareSource.VENDOR,
        display_name="Example App",
        display_version="1.0",
        publisher="Example Corporation",
        install_location=Path(r"C:\Program Files\Example"),
        scope=SoftwareScope.CURRENT_USER,
        architecture=SoftwareArchitecture.X64,
        registry_hive=hive,
        registry_view=RegistryView.X64,
        registry_key=r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Example",
        uninstall_string=command,
        quiet_uninstall_string='"C:\\evil.exe" /quiet',
    )


def _observation() -> VendorExecutableObservation:
    return VendorExecutableObservation(
        file_identity=VendorExecutableFileIdentity(
            executable_path=Path(r"C:\Program Files\Example\uninstall.exe"),
            volume_serial=1,
            file_id="01",
            size_bytes=100,
            created_ns=1,
            modified_ns=2,
            attributes=0,
            sha256=hashlib.sha256(b"example").hexdigest(),
        ),
        local_fixed_volume=True,
        reparse_free=True,
        blocked_location=False,
        install_location_relation=VendorInstallLocationRelation.INSIDE_INSTALL_LOCATION,
        authenticode=VendorAuthenticodeEvidence(
            status=VendorAuthenticodeStatus.VALID,
            signer_subject="Example Corp",
            signer_organization="Example Corp",
        ),
        publisher_match=VendorPublisherMatch.MATCHED,
    )


def test_parser_preserves_exact_windows_argv_and_ignores_quiet_metadata() -> None:
    parser = VendorUninstallMetadataParser(
        lambda value: (r"C:\Program Files\Example\uninstall.exe", "/remove")
    )
    parsed = parser.parse(_raw())

    assert parsed.executable_token == r"C:\Program Files\Example\uninstall.exe"
    assert parsed.raw_arguments == ("/remove",)
    assert parsed.argument_fingerprint() != _raw().command_metadata_digest()


@pytest.mark.parametrize(
    "command",
    (
        None,
        "",
        '"C:\\Example\\uninstall.exe /remove',
        "C:\\Example\\uninstall.exe\x00/remove",
    ),
)
def test_parser_blocks_absent_or_malformed_metadata(command: str | None) -> None:
    with pytest.raises(VendorMetadataError):
        VendorUninstallMetadataParser(lambda value: (value,)).parse(_raw(command))


def test_parser_blocks_machine_registry_source() -> None:
    with pytest.raises(VendorMetadataError, match="HKEY_CURRENT_USER"):
        VendorUninstallMetadataParser(lambda value: (value,)).parse(
            _raw(hive=RegistryHive.LOCAL_MACHINE)
        )


@pytest.mark.parametrize(
    "raw",
    (
        _raw().model_copy(update={"source": SoftwareSource.MSIX}),
        _raw().model_copy(update={"scope": SoftwareScope.LOCAL_MACHINE}),
    ),
)
def test_parser_blocks_nonregistry_or_nonuser_metadata(raw: RawInstalledSoftwareEntry) -> None:
    with pytest.raises(VendorMetadataError):
        VendorUninstallMetadataParser(lambda value: (value,)).parse(raw)


@pytest.mark.parametrize(
    "argv_parser",
    (
        lambda value: (),
        lambda value: ("",),
        lambda value: ("C:\\App\\uninstall.exe", *("x" for _ in range(33))),
        lambda value: ("C:\\App\\uninstall.exe", "x" * 32_769),
    ),
)
def test_parser_blocks_empty_oversized_or_ambiguous_argv(argv_parser) -> None:
    with pytest.raises(VendorMetadataError):
        VendorUninstallMetadataParser(argv_parser).parse(_raw())


def test_parser_wraps_windows_argv_failure() -> None:
    def fail(value: str) -> tuple[str, ...]:
        raise OSError("synthetic parsing failure")

    with pytest.raises(VendorMetadataError, match="parsing failed"):
        VendorUninstallMetadataParser(fail).parse(_raw())


def test_native_windows_parser_handles_spaces_unicode_and_quoted_arguments() -> None:
    command = '"C:\\程序 Files\\示例\\uninstall.exe" "argument with spaces" /remove'
    assert windows_command_line_to_argv(command) == (
        r"C:\程序 Files\示例\uninstall.exe",
        "argument with spaces",
        "/remove",
    )


@pytest.mark.parametrize("arguments", ((), ("/remove",), ("--uninstall",)))
def test_argument_policy_allows_only_finite_interactive_forms(arguments: tuple[str, ...]) -> None:
    assert VendorArgumentPolicy().assess(arguments).decision is VendorArgumentDecision.ALLOW


@pytest.mark.parametrize(
    "arguments",
    (
        ("/quiet",),
        ("/remove", "/norestart"),
        ("/remove&calc.exe",),
        ("@answer.txt",),
        (r"C:\payload.ps1",),
        ("/delete-data",),
        ("/unknown",),
    ),
)
def test_argument_policy_blocks_injection_and_semantic_expansion(
    arguments: tuple[str, ...],
) -> None:
    assessment = VendorArgumentPolicy().assess(arguments)
    assert assessment.decision is VendorArgumentDecision.BLOCK
    assert assessment.argument_count == len(arguments)


def test_trust_identity_binds_exact_arguments_and_all_evidence() -> None:
    raw = _raw()
    software = normalize_raw_entry(raw)
    assert software is not None
    parsed = VendorUninstallMetadataParser(
        lambda value: (r"C:\Program Files\Example\uninstall.exe", "/remove")
    ).parse(raw)
    assessment = VendorArgumentPolicy().assess(parsed.raw_arguments)

    identity = VendorExecutableTrustValidator(_ExecutablePlatform(_observation())).build_identity(
        software,
        raw,
        parsed,
        assessment,
    )

    assert identity.trust.decision is VendorTrustDecision.TRUSTED_FOR_EXECUTION
    assert identity.arguments == ("/remove",)
    assert len(identity.invariant_digest()) == 64


def test_trust_defaults_to_insufficient_when_signature_is_unknown() -> None:
    raw = _raw()
    software = normalize_raw_entry(raw)
    assert software is not None
    observation = _observation().model_copy(
        update={"authenticode": VendorAuthenticodeEvidence(status=VendorAuthenticodeStatus.UNKNOWN)}
    )
    parsed = VendorUninstallMetadataParser(
        lambda value: (r"C:\Program Files\Example\uninstall.exe",)
    ).parse(raw)
    identity = VendorExecutableTrustValidator(_ExecutablePlatform(observation)).build_identity(
        software,
        raw,
        parsed,
        VendorArgumentPolicy().assess(()),
    )
    assert identity.trust.decision is VendorTrustDecision.INSUFFICIENT_EVIDENCE


def test_trust_collects_every_failed_mandatory_gate() -> None:
    raw = _raw()
    software = normalize_raw_entry(raw)
    assert software is not None
    observation = _observation().model_copy(
        update={
            "local_fixed_volume": False,
            "reparse_free": False,
            "blocked_location": True,
            "install_location_relation": VendorInstallLocationRelation.OUTSIDE_INSTALL_LOCATION,
            "authenticode": VendorAuthenticodeEvidence(status=VendorAuthenticodeStatus.INVALID),
            "publisher_match": VendorPublisherMatch.MISMATCHED,
        }
    )
    parsed = VendorUninstallMetadataParser(
        lambda value: (r"C:\Program Files\Example\uninstall.exe", "/quiet")
    ).parse(raw)
    identity = VendorExecutableTrustValidator(_ExecutablePlatform(observation)).build_identity(
        software,
        raw,
        parsed,
        VendorArgumentPolicy().assess(parsed.raw_arguments),
    )

    assert identity.trust.decision is VendorTrustDecision.INSUFFICIENT_EVIDENCE
    assert len(identity.trust.reasons) == 7
    assert identity.trust.evidence == ()


def test_trust_requires_install_location_and_publisher() -> None:
    raw = _raw()
    software = normalize_raw_entry(raw)
    assert software is not None
    parsed = VendorUninstallMetadataParser(
        lambda value: (r"C:\Program Files\Example\uninstall.exe",)
    ).parse(raw)
    with pytest.raises(VendorExecutableTrustError, match="publisher"):
        VendorExecutableTrustValidator(_ExecutablePlatform(_observation())).build_identity(
            software.model_copy(update={"publisher": None}),
            raw,
            parsed,
            VendorArgumentPolicy().assess(()),
        )


@pytest.mark.parametrize(
    "token",
    (r"C:\Windows\System32\cmd.exe", r"C:\Program Files\Example\remove.ps1"),
)
def test_trust_blocks_interpreters_and_non_executables(token: str) -> None:
    raw = _raw()
    software = normalize_raw_entry(raw)
    assert software is not None
    parsed = VendorUninstallMetadataParser(lambda value: (token,)).parse(raw)
    with pytest.raises(VendorExecutableTrustError):
        VendorExecutableTrustValidator(_ExecutablePlatform(_observation())).build_identity(
            software,
            raw,
            parsed,
            VendorArgumentPolicy().assess(()),
        )


@pytest.mark.parametrize(
    "token",
    (
        "uninstall.exe",
        r"%TEMP%\uninstall.exe",
        r"\\server\share\uninstall.exe",
        r"C:\App\..\evil.exe",
    ),
)
def test_executable_resolver_blocks_search_expansion_network_and_traversal(token: str) -> None:
    with pytest.raises(VendorExecutableTrustError):
        resolve_vendor_executable(token)


def test_executable_resolver_blocks_empty_home_device_and_ambiguous_normalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for token in ("", "~/uninstall.exe", r"\\.\C:\uninstall.exe"):
        with pytest.raises(VendorExecutableTrustError):
            resolve_vendor_executable(token)
    monkeypatch.setattr(trust_module.os.path, "abspath", lambda value: "relative.exe")
    with pytest.raises(VendorExecutableTrustError, match="ambiguous"):
        resolve_vendor_executable(r"C:\App\uninstall.exe")


def test_publisher_matching_is_conservative() -> None:
    assert (
        conservative_publisher_match("Example Corporation", "Example Corp")
        is VendorPublisherMatch.MATCHED
    )
    assert (
        conservative_publisher_match("Example Corporation", "Example Labs")
        is VendorPublisherMatch.MISMATCHED
    )
    assert conservative_publisher_match("Example", None) is VendorPublisherMatch.UNKNOWN
    assert conservative_publisher_match("Inc.", "Corp.") is VendorPublisherMatch.UNKNOWN
