import ipaddress
import socket

import httpx


class PlexUnsafeAddressError(Exception):
    pass


# RFC 6052 NAT64: embeds an IPv4 address in the low 32 bits of an IPv6
# address. ipaddress.is_global doesn't special-case it, so e.g.
# 64:ff9b::7f00:1 (NAT64-encoded 127.0.0.1) reads as globally routable.
_NAT64_PREFIX = ipaddress.ip_network("64:ff9b::/96")


def validate_address(base_url: str) -> None:
    """Raises PlexUnsafeAddressError unless base_url is an http(s) address
    that currently resolves only to globally-routable IPs. Callers still
    issue their own httpx call against base_url afterward -- this only
    gates whether that call is allowed to happen at all.

    Parses base_url with httpx's own URL parser (not urllib.parse) so the
    hostname checked here is exactly the hostname httpx will resolve for
    the real request."""
    try:
        url = base_url if "://" in base_url else f"http://{base_url}"
        parsed = httpx.URL(url)
    except httpx.InvalidURL as e:
        raise PlexUnsafeAddressError(f"Invalid URL format: {e}") from e

    if parsed.scheme not in ("http", "https"):
        raise PlexUnsafeAddressError(f"Unsupported scheme: {parsed.scheme!r}")

    hostname = parsed.host
    if not hostname:
        raise PlexUnsafeAddressError("No hostname in address")

    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addrinfo = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
    except ValueError as e:
        raise PlexUnsafeAddressError(f"Invalid address or port: {e}") from e
    except socket.gaierror as e:
        raise PlexUnsafeAddressError(f"Could not resolve host: {e}") from e

    for info in addrinfo:
        ip = info[4][0]
        addr = ipaddress.ip_address(ip)
        if addr in _NAT64_PREFIX:
            raise PlexUnsafeAddressError(f"Address resolves to a NAT64-encoded range: {ip}")
        if not addr.is_global or addr.is_multicast:
            raise PlexUnsafeAddressError(f"Address resolves to a non-public range: {ip}")
