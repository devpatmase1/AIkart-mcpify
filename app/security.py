import asyncio
import ipaddress
import socket
from urllib.parse import urlparse


def _is_unsafe_ip(ip_str: str) -> bool:
    """Returns True if the IP is private, loopback, link-local, reserved, or otherwise internal."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


async def is_public_url(url: str) -> tuple[bool, str]:
    """
    Validates that a target URL's scheme is HTTP(S) and its hostname resolves
    only to public IP addresses, to prevent SSRF against internal/cloud-metadata
    infrastructure via the URL-probing and proxy-creation endpoints.

    Returns (is_safe, reason). reason is empty when is_safe is True.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "Malformed URL."

    if parsed.scheme not in ("http", "https"):
        return False, "Only http/https URLs are allowed."

    hostname = parsed.hostname
    if not hostname:
        return False, "URL has no hostname."

    if hostname.lower() in ("localhost", "0.0.0.0", "::1"):
        return False, "Requests to localhost are not allowed."

    try:
        loop = asyncio.get_event_loop()
        infos = await loop.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False, f"Could not resolve hostname: {hostname}"
    except Exception as e:
        return False, f"DNS resolution error: {e}"

    if not infos:
        return False, f"Could not resolve hostname: {hostname}"

    for info in infos:
        ip_str = info[4][0]
        if _is_unsafe_ip(ip_str):
            return False, f"Target resolves to a non-public address ({ip_str}); not allowed."

    return True, ""
