import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

import httpx


def normalize_url(url: str) -> str:
    """
    Ensure URL has a scheme and reduce it to just scheme+host(+port),
    discarding any path/query/fragment. People commonly paste a full page
    URL copied from their browser (e.g. "https://example.com/landing/")
    rather than the bare domain - probing/proxying under that path (e.g.
    "/landing/mcp") would target the wrong thing, since every endpoint
    path elsewhere in this app is appended directly onto this base.

    Shared by analyzer.py, proxy.py, and main.py so a URL is normalized
    the same way no matter which entry point receives it.
    """
    url = url.strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        url = f"https://{url}"

    parsed = urlparse(url)
    if not parsed.hostname:
        return url.rstrip("/")

    base = f"{parsed.scheme}://{parsed.hostname}"
    if parsed.port:
        base += f":{parsed.port}"
    return base


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


async def resolve_canonical_base(url: str) -> str:
    """
    Many real sites blanket-redirect at the domain level (apex -> www,
    http -> https, bare-domain -> a trailing slash). Every other outbound
    call in this app deliberately does NOT follow redirects (SSRF
    hardening - see is_public_url), so such a site would otherwise look
    completely dead: every probe just bounces off a 3xx and the proxy's
    actual tool calls would too.

    This resolves that ONE-TIME, at proxy-creation time: follow redirects
    here (capped, so a redirect chain can't run away), then re-validate
    the FINAL destination against is_public_url before trusting it - a
    malicious target could otherwise pass the initial check on its own
    public URL and redirect everything after to an internal address.
    Falls back to the original URL on any failure (timeout, malformed
    response, or an unsafe final destination) rather than raising, since
    this is a best-effort convenience, not a required step.
    """
    try:
        async with httpx.AsyncClient(follow_redirects=True, max_redirects=5, timeout=6.0) as client:
            resp = await client.get(url)
    except Exception:
        return url

    resolved = resp.url
    if not resolved.host:
        return url

    base = f"{resolved.scheme}://{resolved.host}"
    if resolved.port and not ((resolved.scheme == "https" and resolved.port == 443) or (resolved.scheme == "http" and resolved.port == 80)):
        base += f":{resolved.port}"

    if base.rstrip("/") == url.rstrip("/"):
        return url

    is_safe, _ = await is_public_url(base)
    return base if is_safe else url
