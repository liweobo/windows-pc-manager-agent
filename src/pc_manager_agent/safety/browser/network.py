"""URL, origin, DNS, and audit-redaction boundaries for browser networking."""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import ClassVar, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


class BrowserNetworkPolicyError(ValueError):
    """Raised when a URL or resolved address cannot enter the browser worker."""


class HostResolver(Protocol):
    """Resolve one hostname for a fresh preflight check."""

    def resolve(self, hostname: str) -> tuple[str, ...]:
        """Return all distinct A and AAAA address strings."""


class SystemHostResolver:
    """Use the operating-system resolver without accepting a caller-supplied address."""

    def resolve(self, hostname: str) -> tuple[str, ...]:
        """Resolve and deduplicate every address or fail closed."""
        try:
            answers = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise BrowserNetworkPolicyError("BROWSER_DNS_RESOLUTION_FAILED") from exc
        addresses = tuple(sorted({str(answer[4][0]) for answer in answers}))
        if not addresses:
            raise BrowserNetworkPolicyError("BROWSER_DNS_NO_ADDRESSES")
        return addresses


@dataclass(frozen=True, slots=True)
class ValidatedBrowserUrl:
    """Fresh public-network URL evidence; it is not reusable execution authority."""

    normalized_url: str
    origin: str
    ascii_hostname: str
    unicode_hostname: str
    addresses: tuple[str, ...]
    insecure_http: bool


def _origin(scheme: str, hostname: str, port: int | None) -> str:
    default = (scheme == "https" and port in {None, 443}) or (
        scheme == "http" and port in {None, 80}
    )
    return f"{scheme}://{hostname}" if default else f"{scheme}://{hostname}:{port}"


class BrowserUrlPolicy:
    """Permit only fresh public HTTP(S) destinations on their standard port."""

    _blocked_names: ClassVar[frozenset[str]] = frozenset(
        {
            "localhost",
            "localhost.localdomain",
            "metadata.google.internal",
            "instance-data",
        }
    )

    def __init__(self, resolver: HostResolver | None = None) -> None:
        self._resolver = resolver or SystemHostResolver()

    def validate(self, url: str, *, allow_http: bool = False) -> ValidatedBrowserUrl:
        """Normalize one URL and freshly reject credentials, private DNS, and unsafe ports."""
        if len(url) > 4_096:
            raise BrowserNetworkPolicyError("BROWSER_URL_TOO_LONG")
        parts = urlsplit(url)
        scheme = parts.scheme.casefold()
        if scheme not in {"http", "https"}:
            raise BrowserNetworkPolicyError("BROWSER_SCHEME_BLOCKED")
        if scheme == "http" and not allow_http:
            raise BrowserNetworkPolicyError("BROWSER_HTTP_REQUIRES_CONFIRMATION")
        if parts.username is not None or parts.password is not None:
            raise BrowserNetworkPolicyError("BROWSER_URL_CREDENTIALS_BLOCKED")
        hostname = parts.hostname
        if hostname is None or not hostname.strip():
            raise BrowserNetworkPolicyError("BROWSER_HOST_REQUIRED")
        lowered = hostname.rstrip(".").casefold()
        if lowered in self._blocked_names or lowered.endswith((".localhost", ".local")):
            raise BrowserNetworkPolicyError("BROWSER_LOCAL_HOST_BLOCKED")
        if ":" not in lowered and "." not in lowered and not _is_ip_literal(lowered):
            raise BrowserNetworkPolicyError("BROWSER_SINGLE_LABEL_HOST_BLOCKED")
        try:
            ascii_hostname = lowered.encode("idna").decode("ascii")
            unicode_hostname = ascii_hostname.encode("ascii").decode("idna")
            port = parts.port
        except (UnicodeError, ValueError) as exc:
            raise BrowserNetworkPolicyError("BROWSER_HOST_OR_PORT_INVALID") from exc
        expected_port = 443 if scheme == "https" else 80
        if port not in {None, expected_port}:
            raise BrowserNetworkPolicyError("BROWSER_NON_STANDARD_PORT_BLOCKED")
        addresses = self._resolver.resolve(ascii_hostname)
        for address in addresses:
            try:
                parsed = ipaddress.ip_address(address.split("%", 1)[0])
            except ValueError as exc:
                raise BrowserNetworkPolicyError("BROWSER_DNS_ADDRESS_INVALID") from exc
            if not parsed.is_global:
                raise BrowserNetworkPolicyError("BROWSER_NON_PUBLIC_ADDRESS_BLOCKED")
        netloc = ascii_hostname
        if ":" in ascii_hostname and not ascii_hostname.startswith("["):
            netloc = f"[{ascii_hostname}]"
        if port is not None:
            netloc = f"{netloc}:{port}"
        normalized = urlunsplit((scheme, netloc, parts.path or "/", parts.query, parts.fragment))
        return ValidatedBrowserUrl(
            normalized_url=normalized,
            origin=_origin(scheme, ascii_hostname, port),
            ascii_hostname=ascii_hostname,
            unicode_hostname=unicode_hostname,
            addresses=addresses,
            insecure_http=scheme == "http",
        )

    def validate_redirect_chain(
        self,
        urls: tuple[str, ...],
        *,
        max_redirects: int,
        allow_http: bool = False,
    ) -> tuple[ValidatedBrowserUrl, ...]:
        """Freshly validate every hop and enforce an explicit redirect limit."""
        if len(urls) - 1 > max_redirects:
            raise BrowserNetworkPolicyError("BROWSER_REDIRECT_LIMIT_EXCEEDED")
        return tuple(self.validate(url, allow_http=allow_http) for url in urls)


class BrowserUrlRedactor:
    """Remove credentials, fragments, and sensitive query values before audit."""

    _sensitive_names: ClassVar[frozenset[str]] = frozenset(
        {
            "access_token",
            "auth",
            "authorization",
            "code",
            "cookie",
            "key",
            "password",
            "secret",
            "session",
            "signature",
            "sig",
            "token",
        }
    )

    def redact(self, url: str, *, keep_safe_query_names: bool = False) -> str:
        """Return an audit-safe URL; values are never retained."""
        try:
            parts = urlsplit(url)
            hostname = parts.hostname or "[invalid-host]"
            port = parts.port
        except ValueError:
            return "[invalid-url]"
        netloc = hostname
        if port is not None:
            netloc = f"{netloc}:{port}"
        query = ""
        if keep_safe_query_names:
            names = [
                (name, "[redacted]")
                for name, _value in parse_qsl(parts.query, keep_blank_values=True)
                if name.casefold() not in self._sensitive_names
            ]
            query = urlencode(names)
        return urlunsplit((parts.scheme.casefold(), netloc, parts.path, query, ""))


def _is_ip_literal(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname.strip("[]"))
    except ValueError:
        return False
    return True
