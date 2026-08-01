import socket

import pytest

from plex_security import PlexUnsafeAddressError, validate_address


def _fake_addrinfo(*ips):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in ips]


def test_accepts_a_public_address(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _fake_addrinfo("8.8.8.8"))
    validate_address("http://plex.example.com:32400")  # does not raise


def test_defaults_to_http_scheme_when_none_given(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _fake_addrinfo("8.8.8.8"))
    validate_address("plex.example.com:32400")  # does not raise


def test_rejects_loopback(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _fake_addrinfo("127.0.0.1"))
    with pytest.raises(PlexUnsafeAddressError):
        validate_address("http://localhost:32400")


def test_rejects_rfc1918_private_range(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _fake_addrinfo("192.168.1.50"))
    with pytest.raises(PlexUnsafeAddressError):
        validate_address("http://192.168.1.50:32400")


def test_rejects_link_local_metadata_ip(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _fake_addrinfo("169.254.169.254"))
    with pytest.raises(PlexUnsafeAddressError):
        validate_address("http://169.254.169.254")


def test_rejects_ipv6_loopback(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _fake_addrinfo("::1"))
    with pytest.raises(PlexUnsafeAddressError):
        validate_address("http://[::1]:32400")


def test_rejects_ipv6_unique_local(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _fake_addrinfo("fd00::1"))
    with pytest.raises(PlexUnsafeAddressError):
        validate_address("http://[fd00::1]:32400")


def test_rejects_when_any_resolved_address_is_non_global(monkeypatch):
    # A hostname with two A records, only one of which is private -- unsafe
    # if either could be the one actually connected to.
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _fake_addrinfo("8.8.8.8", "10.0.0.1"))
    with pytest.raises(PlexUnsafeAddressError):
        validate_address("http://multi.example.com:32400")


def test_rejects_non_http_scheme():
    with pytest.raises(PlexUnsafeAddressError):
        validate_address("ftp://plex.example.com:32400")


def test_raises_when_hostname_does_not_resolve(monkeypatch):
    def _boom(*a, **k):
        raise socket.gaierror("Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    with pytest.raises(PlexUnsafeAddressError):
        validate_address("http://nonexistent.invalid:32400")


def test_rejects_multicast_address(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _fake_addrinfo("224.0.0.1"))
    with pytest.raises(PlexUnsafeAddressError):
        validate_address("http://multicast.example.com:32400")


def test_raises_on_out_of_range_port():
    with pytest.raises(PlexUnsafeAddressError):
        validate_address("http://plex.example.com:99999")


def test_raises_on_resolution_failure_unicode_error(monkeypatch):
    # UnicodeError from getaddrinfo (e.g., malformed hostname IDNA encoding)
    # is a subclass of ValueError, caught and wrapped as PlexUnsafeAddressError
    def _boom(*a, **k):
        raise UnicodeError("hostname too long")

    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    with pytest.raises(PlexUnsafeAddressError):
        validate_address("http://invalid.example.com:32400")


def test_raises_on_malformed_url_parsing():
    # Malformed IPv6 literal (missing closing bracket) raises ValueError from urlsplit
    with pytest.raises(PlexUnsafeAddressError):
        validate_address("http://[::1")
