"""Exercise the real transport and pool with fake DNS and network streams."""

import asyncio
import socket
import ssl
from unittest.mock import AsyncMock

import httpcore
import httpx
import pytest

from app.core import teacher_model_transport as transport


HOST = "models.example.com"
BASE_URL = f"https://{HOST}/gateway/v1"
PUBLIC_V4 = "8.8.8.8"
PUBLIC_V6 = "2606:4700:4700::1111"
OK = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}"


def _answer(ip, *, scope=0):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    address = (ip, 443, 0, scope) if family == socket.AF_INET6 else (ip, 443)
    return family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", address


class _FakeStream(httpcore.AsyncMockStream):
    def __init__(self, response=OK):
        super().__init__([response])
        self.start_tls = AsyncMock(return_value=self)
        self.write = AsyncMock()


@pytest.fixture
async def network(monkeypatch):
    dns = AsyncMock(return_value=[_answer(PUBLIC_V4), _answer(PUBLIC_V6)])
    delegate = httpcore.AsyncNetworkBackend()
    delegate.connect_tcp = AsyncMock(return_value=_FakeStream())
    delegate.connect_unix_socket = AsyncMock(side_effect=AssertionError("Unexpected Unix socket"))
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", dns)
    for method in ("create_connection", "create_unix_connection"):
        monkeypatch.setattr(loop, method, AsyncMock(side_effect=AssertionError("Unexpected real socket")))
    monkeypatch.setattr(httpcore, "AnyIOBackend", lambda: delegate)
    return dns, delegate


@pytest.mark.parametrize(("value", "expected"), [
    ("HTTPS://Models.Example.COM.:443/gateway/v1/", BASE_URL),
    (f"https://{HOST}/", f"https://{HOST}"),
    (f"https://{HOST}/tenant%2Dname/v1", f"https://{HOST}/tenant%2Dname/v1"),
    ("https://b\u00fccher.de/v1", "https://xn--bcher-kva.de/v1"),
])
def test_normalize_teacher_endpoint(value, expected):
    assert transport.normalize_teacher_endpoint(value) == expected


@pytest.mark.parametrize("value", [
    "", "models.example.com/v1", "//models.example.com/v1", "https:///v1",
    "http://models.example.com", "file:///tmp/model", "https://models.example.com:444",
    "https://models.example.com:", "https://models.example.com:abc",
    "https://models.example.com:65536", "https://user:token@models.example.com",
    "https://@models.example.com", "https://models.example.com?q=1",
    "https://models.example.com?", "https://models.example.com#",
    "https://models.example.com#v1", " https://models.example.com",
    "https://models.\nexample.com", "https://models.example.com\\@elsewhere.com",
    "https://localhost", "https://models.localhost", "https://models.local", "https://model.internal",
    "https://models..example.com", "https://_models.example.com", "https://-models.example.com",
    "https://models.example.com..", "https://models%2eexample.com",
    "https://8.8.8.8", "https://127.1", "https://2130706433", "https://0x7f.0.0.0x1",
    "https://[2606:4700:4700::1111]", "https://[::1", "https://[::ffff:8.8.8.8]",
    "https://https://models.example.com", "https://models.example.com/https://elsewhere.com",
    "https://models.example.com//v1", "https://models.example.com/v1/../other",
    "https://models.example.com/v1/%2e%2e", "https://models.example.com/v1/%00",
    "https://models.example.com/v1/%2f", "https://models.example.com/v1/%252f",
    "https://models.example.com/v1/%xx", "https://models.example.com/chat/completions",
    "https://models.example.com/v1/CHAT/%63ompletions/",
])
def test_rejects_invalid_teacher_endpoint(value):
    with pytest.raises(ValueError):
        transport.normalize_teacher_endpoint(value)


async def test_pins_ip_but_keeps_original_host_and_verified_tls(network, monkeypatch, tmp_path):
    dns, delegate = network
    monkeypatch.setenv("HTTPS_PROXY", "http://ignored.example.com:8080")
    monkeypatch.setenv("ALL_PROXY", "http://ignored.example.com:8080")
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "missing-ca.pem"))
    monkeypatch.setenv("SSLKEYLOGFILE", str(tmp_path / "tls-keys.log"))

    async with transport.create_teacher_async_http_client(BASE_URL, timeout=2) as client:
        assert isinstance(client, httpx.AsyncClient)
        assert client.trust_env is False
        dns.assert_not_awaited()
        async with client.stream("POST", "chat/completions", json={"model": "test"}) as response:
            assert response.status_code == 200
            assert await response.aread() == b"{}"

    dns.assert_awaited_once_with(
        f"{HOST}.", 443, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP,
    )
    delegate.connect_tcp.assert_awaited_once()
    assert delegate.connect_tcp.await_args.kwargs["host"] == PUBLIC_V4
    assert delegate.connect_tcp.await_args.kwargs["port"] == 443
    assert delegate.connect_tcp.await_args.kwargs["timeout"] == 2
    stream = delegate.connect_tcp.return_value
    stream.start_tls.assert_awaited_once()
    tls = stream.start_tls.await_args.kwargs
    assert tls["server_hostname"] == HOST
    assert tls["ssl_context"].verify_mode == ssl.CERT_REQUIRED
    assert tls["ssl_context"].check_hostname is True
    assert tls["ssl_context"].keylog_filename is None
    sent = b"".join(call.args[0] for call in stream.write.await_args_list)
    assert sent.startswith(b"POST /gateway/v1/chat/completions HTTP/1.1\r\n")
    assert f"host: {HOST}\r\n".encode() in sent.lower()


async def test_failed_tls_sends_no_http_bytes(network):
    _, delegate = network
    stream = delegate.connect_tcp.return_value
    stream.start_tls.side_effect = httpcore.ConnectError("certificate rejected")

    async with transport.create_teacher_async_http_client(BASE_URL, timeout=2) as client:
        with pytest.raises(httpx.ConnectError, match="certificate rejected"):
            await client.get("models")

    stream.write.assert_not_awaited()


@pytest.mark.parametrize("ip", [
    "0.0.0.0", "10.0.0.1", "100.64.0.1", "127.0.0.1", "168.63.129.16",
    "169.254.169.254", "192.0.0.9", "192.0.2.1", "192.88.99.2", "198.18.0.1",
    "224.0.0.1", "240.0.0.1", "::1", "::ffff:8.8.8.8", "64:ff9b::a00:1",
    "2001:db8::1", "2002:a00:1::1", "2620:4f:8000::1", "3fff::1",
    "fc00::1", "fe80::1", "ff02::1",
])
async def test_any_blocked_dns_answer_rejects_entire_lookup(network, ip):
    dns, delegate = network
    dns.return_value = [_answer(PUBLIC_V4), _answer(ip)]

    async with transport.create_teacher_async_http_client(BASE_URL, timeout=2) as client:
        with pytest.raises(httpx.ConnectError, match="blocked address"):
            await client.get("models")

    dns.assert_awaited_once()
    delegate.connect_tcp.assert_not_awaited()


async def test_fallback_uses_only_vetted_ips_from_same_lookup(network):
    dns, delegate = network
    delegate.connect_tcp.side_effect = [httpcore.ConnectError("first IP unavailable"), _FakeStream()]

    async with transport.create_teacher_async_http_client(BASE_URL, timeout=2) as client:
        assert (await client.get("models")).status_code == 200

    dns.assert_awaited_once()
    assert [call.kwargs["host"] for call in delegate.connect_tcp.await_args_list] == [PUBLIC_V4, PUBLIC_V6]


async def test_new_tcp_connection_rechecks_dns(network):
    dns, delegate = network
    dns.side_effect = [[_answer(PUBLIC_V4)], [_answer("127.0.0.1")]]

    async with transport.create_teacher_async_http_client(BASE_URL, timeout=2) as client:
        assert (await client.get("models")).status_code == 200
        with pytest.raises(httpx.ConnectError, match="blocked address"):
            await client.get("models")

    assert dns.await_count == 2
    delegate.connect_tcp.assert_awaited_once()


@pytest.mark.parametrize("answers", [[], [_answer(PUBLIC_V6, scope=1)]])
async def test_rejects_empty_dns_and_scoped_ipv6(network, answers):
    dns, delegate = network
    dns.return_value = answers

    async with transport.create_teacher_async_http_client(BASE_URL, timeout=2) as client:
        with pytest.raises(httpx.ConnectError):
            await client.get("models")

    delegate.connect_tcp.assert_not_awaited()


async def test_dns_error_and_timeout_do_not_dial(network):
    dns, delegate = network

    async def stalled_dns(*args, **kwargs):
        await asyncio.Future()

    dns.side_effect = socket.gaierror(socket.EAI_NONAME, "Name not found")
    async with transport.create_teacher_async_http_client(BASE_URL, timeout=0.01) as client:
        with pytest.raises(httpx.ConnectError, match="Could not resolve"):
            await client.get("models")
        dns.side_effect = stalled_dns
        with pytest.raises(httpx.ConnectTimeout):
            await client.get("models")

    delegate.connect_tcp.assert_not_awaited()


@pytest.mark.parametrize(("method", "url", "kwargs"), [
    ("GET", f"http://{HOST}/models", {}),
    ("GET", "https://elsewhere.example.com/models", {}),
    ("GET", f"https://{HOST}:444/models", {}),
    ("GET", f"https://{HOST}:0/models", {}),
    ("GET", f"https://user:token@{HOST}/models", {}),
    ("GET", BASE_URL, {"headers": {"Host": "elsewhere.example.com"}}),
    ("GET", BASE_URL, {"headers": [("Host", HOST), ("Host", "elsewhere.example.com")]}),
    ("GET", BASE_URL, {"extensions": {"sni_hostname": "elsewhere.example.com"}}),
    ("CONNECT", BASE_URL, {}),
])
async def test_transport_rejects_other_origins_and_authority_overrides(network, method, url, kwargs):
    dns, delegate = network

    async with transport.create_teacher_async_http_client(BASE_URL, timeout=2) as client:
        with pytest.raises(httpx.ConnectError):
            await client.send(client.build_request(method, url, **kwargs))

    dns.assert_not_awaited()
    delegate.connect_tcp.assert_not_awaited()


@pytest.mark.parametrize("location", [b"/other", b"https://elsewhere.example.com/other"])
async def test_redirects_not_followed_even_when_requested(network, location):
    dns, delegate = network
    delegate.connect_tcp.return_value = _FakeStream(
        b"HTTP/1.1 307 Temporary Redirect\r\nLocation: " + location
        + b"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
    )

    async with transport.create_teacher_async_http_client(BASE_URL, timeout=2) as client:
        response = await client.get("models", follow_redirects=True)
        assert response.status_code == 307
        assert response.history == []

    dns.assert_awaited_once()
    delegate.connect_tcp.assert_awaited_once()


async def test_unix_socket_rejected_before_delegate(network):
    dns, delegate = network
    backend = transport._GuardedNetworkBackend(HOST, delegate)
    with pytest.raises(httpcore.ConnectError, match="Unix sockets are not allowed"):
        await backend.connect_unix_socket("/tmp/model.sock")
    dns.assert_not_awaited()
    delegate.connect_unix_socket.assert_not_awaited()
