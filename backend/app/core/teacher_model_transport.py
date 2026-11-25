"""Direct, same-origin HTTPS transport for teacher-configured model endpoints."""

import asyncio
import ipaddress
import re
import socket
from collections.abc import Iterable
from urllib.parse import unquote_to_bytes, urlsplit

import httpcore
import httpx


_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_LOCAL_SUFFIXES = {
    "alt", "arpa", "corp", "example", "home", "internal", "invalid", "lan",
    "local", "localdomain", "localhost", "onion", "test",
}
_PATH = re.compile(r"(?:[A-Za-z0-9._~!$&'()*+,=@/-]|%[0-9A-Fa-f]{2})*")
_UNSAFE_SEGMENT_BYTES = re.compile(rb"[\x00-\x20\x7f/\\:%?#;]")

# Block special-purpose ranges that can pass is_global, including on Python 3.12.
_SPECIAL_V4 = tuple(map(ipaddress.IPv4Network, (
    "192.0.0.0/24", "192.31.196.0/24", "192.52.193.0/24", "192.88.99.0/24",
    "192.175.48.0/24", "168.63.129.16/32",  # Azure fabric virtual IP.
)))
_GLOBAL_UNICAST_V6 = ipaddress.IPv6Network("2000::/3")
_SPECIAL_V6 = tuple(map(ipaddress.IPv6Network, (
    "2001::/23", "2001:db8::/32", "2002::/16", "2620:4f:8000::/48", "3fff::/20",
)))


def normalize_teacher_endpoint(value: str) -> str:
    """Validate and canonicalize a base URL without resolving DNS."""
    if (
        not isinstance(value, str)
        or any(char in value for char in "\\?#")
        or any(char.isspace() or ord(char) <= 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError("Expected an HTTPS base URL without a query or fragment")

    url = urlsplit(value)
    if url.scheme != "https" or not url.netloc:
        raise ValueError("Expected an absolute HTTPS base URL")

    host, separator, port = url.netloc.partition(":")
    if not host or any(char in url.netloc for char in "@%[]"):
        raise ValueError("Expected a public DNS hostname without userinfo or an IP literal")
    if separator and port != "443":
        raise ValueError("Only HTTPS port 443 is allowed")

    try:
        host = httpx.URL(f"https://{host}").raw_host.decode("ascii").removesuffix(".")
    except (httpx.InvalidURL, UnicodeError) as exc:
        raise ValueError("Invalid DNS hostname") from exc
    labels = host.split(".")
    if (
        len(host) > 253
        or len(labels) < 2
        or not all(_DNS_LABEL.fullmatch(label) for label in labels)
        or not labels[-1][0].isalpha()  # Also excludes legacy numeric IPv4 spellings.
        or labels[-1] in _LOCAL_SUFFIXES
    ):
        raise ValueError("Expected a public DNS hostname")

    if "//" in url.path or not _PATH.fullmatch(url.path):
        raise ValueError("Invalid HTTPS base URL path")
    path = url.path.rstrip("/")
    segments = [unquote_to_bytes(segment) for segment in path.split("/")]
    if any(
        segment in (b".", b"..") or _UNSAFE_SEGMENT_BYTES.search(segment)
        for segment in segments
    ):
        raise ValueError("Invalid HTTPS base URL path")
    if [segment.lower() for segment in segments[-2:]] == [b"chat", b"completions"]:
        raise ValueError("Expected a base URL, not the chat completions endpoint")

    return f"https://{host}{path}"


def _is_public_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if not address.is_global or address.is_multicast or address.is_reserved:
        return False
    if isinstance(address, ipaddress.IPv4Address):
        return not any(address in network for network in _SPECIAL_V4)
    return (
        address.scope_id is None
        and address in _GLOBAL_UNICAST_V6  # Excludes IPv4-mapped and NAT64 prefixes.
        and not any(address in network for network in _SPECIAL_V6)
    )


class _GuardedNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, hostname: str, delegate: httpcore.AsyncNetworkBackend) -> None:
        self._hostname = hostname
        self._delegate = delegate

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        if host != self._hostname or port != 443 or local_address is not None:
            raise httpcore.ConnectError("Connection outside the teacher model origin")

        try:
            async with asyncio.timeout(timeout):
                try:
                    # Use an absolute DNS name to avoid resolver search suffixes.
                    answers = await asyncio.get_running_loop().getaddrinfo(
                        f"{host}.", port, family=socket.AF_UNSPEC,
                        type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP,
                    )
                except OSError as exc:
                    raise httpcore.ConnectError("Could not resolve teacher model hostname") from exc

                vetted: dict[str, None] = {}
                for family, _, _, _, sockaddr in answers:
                    if family not in (socket.AF_INET, socket.AF_INET6):
                        raise httpcore.ConnectError("Unexpected teacher model address family")
                    try:
                        address = ipaddress.ip_address(sockaddr[0])
                    except ValueError as exc:
                        raise httpcore.ConnectError("Invalid teacher model address") from exc
                    expected_version = 4 if family == socket.AF_INET else 6
                    if (
                        address.version != expected_version
                        or (family == socket.AF_INET6 and sockaddr[3] != 0)
                        or not _is_public_ip(address)
                    ):
                        raise httpcore.ConnectError("Teacher model hostname resolved to a blocked address")
                    vetted[str(address)] = None
                # Vet every answer before dialing, then pass only numeric IPs to the delegate.
                last_error = httpcore.ConnectError("Teacher model hostname has no usable addresses")
                for ip in vetted:
                    try:
                        return await self._delegate.connect_tcp(
                            host=ip, port=port, timeout=timeout, socket_options=socket_options,
                        )
                    except httpcore.ConnectError as exc:
                        last_error = exc
                raise last_error
        except TimeoutError as exc:
            raise httpcore.ConnectTimeout("Teacher model TCP connection timed out") from exc

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise httpcore.ConnectError("Unix sockets are not allowed for teacher model connections")


class _TeacherTransport(httpx.AsyncHTTPTransport):
    def __init__(self, hostname: str) -> None:
        self._hostname = hostname
        # HTTPX 0.26 has no network_backend argument. Its request/stream adapter
        # and lifecycle methods use _pool; install a direct HTTPCore 1.x pool here.
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(trust_env=False),
            network_backend=_GuardedNetworkBackend(hostname, httpcore.AnyIOBackend()),
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        if (
            url.scheme != "https"
            or url.raw_host != self._hostname.encode("ascii")
            or url.port not in (None, 443)
            or url.userinfo
        ):
            raise httpx.ConnectError("Request outside the teacher model origin", request=request)
        host_headers = [value.lower() for value in request.headers.get_list("host")]
        if (
            host_headers not in ([self._hostname], [f"{self._hostname}:443"])
            or "sni_hostname" in request.extensions
            or request.method == "CONNECT"
        ):
            raise httpx.ConnectError("Teacher model authority cannot be overridden", request=request)
        return await super().handle_async_request(request)


class _TeacherAsyncClient(httpx.AsyncClient):
    async def send(
        self,
        request: httpx.Request,
        *,
        stream: bool = False,
        auth=httpx.USE_CLIENT_DEFAULT,
        follow_redirects=httpx.USE_CLIENT_DEFAULT,
    ) -> httpx.Response:
        return await super().send(request, stream=stream, auth=auth, follow_redirects=False)


def create_teacher_async_http_client(base_url: str, *, timeout: float) -> httpx.AsyncClient:
    """Build an asyncio client whose only egress is verified HTTPS to this origin."""
    endpoint = normalize_teacher_endpoint(base_url)
    return _TeacherAsyncClient(
        base_url=endpoint,
        timeout=timeout,
        transport=_TeacherTransport(httpx.URL(endpoint).raw_host.decode("ascii")),
        follow_redirects=False,
        trust_env=False,
    )
