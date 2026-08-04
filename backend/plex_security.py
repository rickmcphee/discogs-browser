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
    """Raises PlexUnsafeAddressError unless base_url is an https address
    that currently resolves only to globally-routable IPs. Callers still
    issue their own httpx call against base_url afterward -- this only
    gates whether that call is allowed to happen at all.

    https is required, not merely preferred: this app now requires the
    Plex server be reachable over the public internet (see
    2026-08-01-plex-reachability-ssrf-design.md), so a plain http:// address
    would carry X-Plex-Token in the clear across the public internet, not
    just a trusted LAN as in the original single-owner design. A bare
    address with no scheme defaults to http:// below (for parsing only) and
    is rejected by the same check -- callers must supply an explicit
    https:// address.

    Parses base_url with httpx's own URL parser (not urllib.parse) so the
    hostname checked here is exactly the hostname httpx will resolve for
    the real request."""
    try:
        url = base_url if "://" in base_url else f"http://{base_url}"
        parsed = httpx.URL(url)
    except httpx.InvalidURL as e:
        raise PlexUnsafeAddressError(f"Invalid URL format: {e}") from e

    if parsed.scheme != "https":
        raise PlexUnsafeAddressError(f"Unsupported scheme: {parsed.scheme!r}")

    hostname = parsed.host
    if not hostname:
        raise PlexUnsafeAddressError("No hostname in address")

    try:
        port = parsed.port or 443  # scheme is always "https" past the check above
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
