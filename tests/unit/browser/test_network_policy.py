import socket

import pytest

from pc_manager_agent.safety.browser.network import (
    BrowserNetworkPolicyError,
    BrowserUrlPolicy,
    BrowserUrlRedactor,
    SystemHostResolver,
    _origin,
)


class _Resolver:
    def __init__(self, *addresses: str) -> None:
        self.addresses = addresses

    def resolve(self, hostname: str) -> tuple[str, ...]:
        assert hostname
        return self.addresses


def test_https_public_origin_and_idn_are_normalized() -> None:
    policy = BrowserUrlPolicy(_Resolver("93.184.216.34", "2606:2800:220:1::"))
    result = policy.validate("https://例子.测试/path?q=1")
    assert result.ascii_hostname.startswith("xn--")
    assert result.unicode_hostname == "例子.测试"
    assert result.origin.startswith("https://xn--")


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("file:///C:/Windows/System32/config/SAM", "BROWSER_SCHEME_BLOCKED"),
        ("https://user:secret@example.com/", "BROWSER_URL_CREDENTIALS_BLOCKED"),
        ("https://localhost/", "BROWSER_LOCAL_HOST_BLOCKED"),
        ("https://intranet/", "BROWSER_SINGLE_LABEL_HOST_BLOCKED"),
        ("https://example.com:8443/", "BROWSER_NON_STANDARD_PORT_BLOCKED"),
        ("http://example.com/", "BROWSER_HTTP_REQUIRES_CONFIRMATION"),
    ],
)
def test_unsafe_url_shapes_fail_closed(url: str, code: str) -> None:
    with pytest.raises(BrowserNetworkPolicyError, match=code):
        BrowserUrlPolicy(_Resolver("93.184.216.34")).validate(url)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "::1",
        "fe80::1",
    ],
)
def test_private_or_metadata_dns_answers_are_blocked(address: str) -> None:
    with pytest.raises(BrowserNetworkPolicyError, match="NON_PUBLIC"):
        BrowserUrlPolicy(_Resolver(address)).validate("https://example.com/")


def test_http_and_redirect_limits_require_exact_acknowledgement() -> None:
    policy = BrowserUrlPolicy(_Resolver("93.184.216.34"))
    assert policy.validate("http://example.com/", allow_http=True).insecure_http
    with pytest.raises(BrowserNetworkPolicyError, match="REDIRECT_LIMIT"):
        policy.validate_redirect_chain(
            tuple("https://example.com/" for _ in range(7)),
            max_redirects=5,
        )


def test_audit_url_redaction_never_retains_values_or_fragments() -> None:
    url = "https://user:pass@example.com/path?token=secret&q=medical#private"
    redactor = BrowserUrlRedactor()
    assert redactor.redact(url) == "https://example.com/path"
    kept = redactor.redact(url, keep_safe_query_names=True)
    assert "secret" not in kept
    assert "medical" not in kept
    assert "token" not in kept
    assert "q=%5Bredacted%5D" in kept


def test_system_resolver_success_failure_and_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("93.184.216.34", 0))],
    )
    assert SystemHostResolver().resolve("example.com") == ("93.184.216.34",)

    def fail(*_args: object, **_kwargs: object) -> object:
        raise OSError("synthetic")

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    with pytest.raises(BrowserNetworkPolicyError, match="RESOLUTION_FAILED"):
        SystemHostResolver().resolve("example.com")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [])
    with pytest.raises(BrowserNetworkPolicyError, match="NO_ADDRESSES"):
        SystemHostResolver().resolve("example.com")


def test_additional_url_and_redaction_branches() -> None:
    policy = BrowserUrlPolicy(_Resolver("93.184.216.34"))
    with pytest.raises(BrowserNetworkPolicyError, match="TOO_LONG"):
        policy.validate("https://example.com/" + "a" * 4097)
    with pytest.raises(BrowserNetworkPolicyError, match="HOST_REQUIRED"):
        policy.validate("https:///missing")
    with pytest.raises(BrowserNetworkPolicyError, match="HOST_OR_PORT_INVALID"):
        policy.validate("https://example.com:not-a-port/")
    with pytest.raises(BrowserNetworkPolicyError, match="DNS_ADDRESS_INVALID"):
        BrowserUrlPolicy(_Resolver("not-an-ip")).validate("https://example.com/")
    ipv6 = BrowserUrlPolicy(_Resolver("2606:2800:220:1::1")).validate(
        "https://[2606:2800:220:1::1]/"
    )
    assert "[2606:2800:220:1::1]" in ipv6.normalized_url
    explicit = policy.validate("https://example.com:443/")
    assert explicit.normalized_url == "https://example.com:443/"
    assert _origin("https", "example.com", 8443) == "https://example.com:8443"
    assert BrowserUrlRedactor().redact("https://example.com:8443/path") == (
        "https://example.com:8443/path"
    )
    assert BrowserUrlRedactor().redact("https://example.com:bad/path") == "[invalid-url]"


def test_successful_redirect_chain_is_freshly_validated() -> None:
    policy = BrowserUrlPolicy(_Resolver("93.184.216.34"))
    results = policy.validate_redirect_chain(
        ("https://example.com/one", "https://example.com/two"),
        max_redirects=5,
    )
    assert len(results) == 2
