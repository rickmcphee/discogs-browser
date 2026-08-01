import ipaddress
import socket
from urllib.parse import urlsplit


class PlexUnsafeAddressError(Exception):
    pass


def validate_address(base_url: str) -> None:
    """Raises PlexUnsafeAddressError unless base_url is an http(s) address
    that currently resolves only to globally-routable IPs. Callers still
    issue their own httpx call against base_url afterward -- this only
    gates whether that call is allowed to happen at all."""
    url = base_url if "://" in base_url else f"http://{base_url}"
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise PlexUnsafeAddressError(f"Unsupported scheme: {parts.scheme!r}")

    hostname = parts.hostname
    if not hostname:
        raise PlexUnsafeAddressError("No hostname in address")
    port = parts.port or (443 if parts.scheme == "https" else 80)

    try:
        addrinfo = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise PlexUnsafeAddressError(f"Could not resolve host: {e}") from e

    for info in addrinfo:
        ip = info[4][0]
        if not ipaddress.ip_address(ip).is_global:
            raise PlexUnsafeAddressError(f"Address resolves to a non-public range: {ip}")
