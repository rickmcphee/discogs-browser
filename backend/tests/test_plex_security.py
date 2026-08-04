import socket

import httpx
import pytest

from plex_security import PlexUnsafeAddressError, validate_address


def _fake_addrinfo(*ips):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in ips]


def test_accepts_a_public_address(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _fake_addrinfo("8.8.8.8"))
    validate_address("https://plex.example.com:32400")  # does not raise


def test_rejects_when_no_scheme_given_since_it_defaults_to_http(monkeypatch):
    # A bare host:port has no scheme to reject on its own merits -- it's
    # parsed as http:// (for hostname/port extraction only) and rejected by
    # the same https-only check as an explicit http:// address would be.
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _fake_addrinfo("8.8.8.8"))
    with pytest.raises(PlexUnsafeAddressError):
        validate_address("plex.example.com:32400")


def test_rejects_plain_http_scheme(monkeypatch):
    # https is required, not merely preferred -- see validate_address's
    # docstring for why (X-Plex-Token would otherwise cross the public
    # internet in the clear).
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _fake_addrinfo("8.8.8.8"))
    with pytest.raises(PlexUnsafeAddressError):
        validate_address("http://plex.example.com:32400")


def test_rejects_loopback(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _fake_addrinfo("127.0.0.1"))
    with pytest.raises(PlexUnsafeAddressError):
        validate_address("https://localhost:32400")


def test_rejects_rfc1918_private_range(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _fake_addrinfo("192.168.1.50"))
    with pytest.raises(PlexUnsafeAddressError):
        validate_address("https://192.168.1.50:32400")


def test_rejects_link_local_metadata_ip(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _fake_addrinfo("169.254.169.254"))
    with pytest.raises(PlexUnsafeAddressError):
        validate_address("https://169.254.169.254")


def test_rejects_ipv6_loopback(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _fake_addrinfo("::1"))
    with pytest.raises(PlexUnsafeAddressError):
        validate_address("https://[::1]:32400")


def test_rejects_ipv6_unique_local(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _fake_addrinfo("fd00::1"))
    with pytest.raises(PlexUnsafeAddressError):
        validate_address("https://[fd00::1]:32400")


def test_rejects_when_any_resolved_address_is_non_global(monkeypatch):
    # A hostname with two A records, only one of which is private -- unsafe
    # if either could be the one actually connected to.
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _fake_addrinfo("8.8.8.8", "10.0.0.1"))
    with pytest.raises(PlexUnsafeAddressError):
        validate_address("https://multi.example.com:32400")


def test_rejects_non_https_scheme():
    with pytest.raises(PlexUnsafeAddressError):
        validate_address("ftp://plex.example.com:32400")


def test_raises_when_hostname_does_not_resolve(monkeypatch):
    def _boom(*a, **k):
        raise socket.gaierror("Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    with pytest.raises(PlexUnsafeAddressError):
        validate_address("https://nonexistent.invalid:32400")


def test_rejects_multicast_address(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _fake_addrinfo("224.0.0.1"))
    with pytest.raises(PlexUnsafeAddressError):
        validate_address("https://multicast.example.com:32400")


def test_raises_on_out_of_range_port():
    with pytest.raises(PlexUnsafeAddressError):
        validate_address("https://plex.example.com:99999")


def test_raises_on_resolution_failure_unicode_error(monkeypatch):
    # UnicodeError from getaddrinfo (e.g., malformed hostname IDNA encoding)
    # is a subclass of ValueError, caught and wrapped as PlexUnsafeAddressError
    def _boom(*a, **k):
        raise UnicodeError("hostname too long")

    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    with pytest.raises(PlexUnsafeAddressError):
        validate_address("https://invalid.example.com:32400")


def test_raises_on_malformed_url_parsing():
    # Malformed IPv6 literal (missing closing bracket) raises ValueError from urlsplit
    with pytest.raises(PlexUnsafeAddressError):
        validate_address("https://[::1")


def test_rejects_ipv6_multicast_address(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _fake_addrinfo("ff02::1"))
    with pytest.raises(PlexUnsafeAddressError):
        validate_address("https://[ff02::1]:32400")


def test_rejects_a_hostname_that_rebinds_to_private_between_two_calls(monkeypatch):
    # validate_address gives no durable guarantee -- it's re-run before every
    # use specifically so a hostname that resolved safely once (e.g. at
    # settings-save time) but is later repointed at a private address gets
    # caught on the very next call, not just at save time. Simulates that by
    # having the same hostname resolve differently across two calls.
    calls = {"n": 0}

    def _rebinding_getaddrinfo(*a, **k):
        calls["n"] += 1
        return _fake_addrinfo("8.8.8.8" if calls["n"] == 1 else "10.0.0.1")

    monkeypatch.setattr(socket, "getaddrinfo", _rebinding_getaddrinfo)

    validate_address("https://rebinding.example.com:32400")  # first call: safe, does not raise
    with pytest.raises(PlexUnsafeAddressError):
        validate_address("https://rebinding.example.com:32400")  # second call: now private, rejected


def test_rejects_nat64_encoded_loopback(monkeypatch):
    # 64:ff9b::7f00:1 is the NAT64 (RFC 6052) encoding of 127.0.0.1. Python's
    # ipaddress.is_global doesn't special-case this range, so it reads as
    # globally routable unless checked explicitly.
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _fake_addrinfo("64:ff9b::7f00:1"))
    with pytest.raises(PlexUnsafeAddressError):
        validate_address("https://[64:ff9b::7f00:1]:32400")


def test_rejects_nat64_encoded_private_address(monkeypatch):
    # 64:ff9b::a00:1 encodes 10.0.0.1 -- same NAT64 prefix, different embedded range.
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _fake_addrinfo("64:ff9b::a00:1"))
    with pytest.raises(PlexUnsafeAddressError):
        validate_address("https://[64:ff9b::a00:1]:32400")


def test_resolves_the_same_hostname_httpx_will_actually_request(monkeypatch):
    # Regression: validate_address used to parse base_url with urllib's urlsplit,
    # while the real outbound request (plex.py's _get) is built as a plain string
    # and parsed by httpx's own URL parser when the request fires. A Unicode
    # "ideographic full stop" (U+3002) label separator diverges between the two --
    # urlsplit keeps it verbatim in .hostname, httpx.URL normalizes it to a
    # regular period -- so validate_address could resolve/validate a different
    # hostname than the one httpx actually connects to.
    captured = {}

    def _capture_and_resolve(host, *a, **k):
        captured["host"] = host
        return _fake_addrinfo("8.8.8.8")

    monkeypatch.setattr(socket, "getaddrinfo", _capture_and_resolve)

    url = "https://evil.com。internal.example.com:32400"
    validate_address(url)  # does not raise -- the resolved address is public

    assert captured["host"] == httpx.URL(url).host == "evil.com.internal.example.com"
