import asyncio
import ipaddress
import socket
from urllib.parse import urlparse, urljoin

import httpx


def normalize_url(url: str) -> str:
    """
    Ensure URL has a scheme and strip query string/fragment - those are
    never meaningful as part of a base URL. The path IS kept: plenty of
    real APIs are deliberately hosted under a path on a shared domain
    (e.g. "https://dog.ceo/api", "https://api.stripe.com/v1") and every
    endpoint call this app makes is built by appending onto this base, so
    dropping that path silently breaks those APIs - verified live against
    dog.ceo/api, where every call_api call 404'd once the /api prefix was
    lost. This used to strip the path too (to handle someone pasting a
    full marketing-page URL from their browser, e.g.
    "https://example.com/landing/page?x=1", rather than the bare domain -
    see git history), but that traded a silent wrong-data bug (a real API
    quietly returning 404s from the wrong base) for a softer, recoverable
    one (a mis-scoped page still yields a working proxy, just possibly
    probed under the wrong sub-path - the user can retry with the
    corrected URL if so). Silent wrong data is worse, so path wins.

    Shared by analyzer.py, proxy.py, and main.py so a URL is normalized
    the same way no matter which entry point receives it.
    """
    url = url.strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        url = f"https://{url}"

    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        # e.g. "javascript:alert(1)" parses with a non-numeric "port" -
        # urlparse defers that error to attribute access. Not a URL we can
        # make sense of; let is_public_url's hostname check reject it
        # cleanly instead of raising here.
        return url.rstrip("/")

    if not hostname:
        return url.rstrip("/")

    base = f"{parsed.scheme}://{hostname}"
    if port:
        base += f":{port}"
    path = parsed.path.rstrip("/")
    if path:
        base += path
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

    This resolves that ONE-TIME, at proxy-creation time - but validates
    EACH hop's destination against is_public_url BEFORE connecting to it,
    not just the final one. httpx's own follow_redirects=True would
    connect to every hop first and only let us inspect the destination
    afterward - a malicious target's first hop could point straight at an
    internal address and that request would already have happened by the
    time anything checked it (a "blind" SSRF: no response body comes
    back to the caller, but the request itself still reaches the internal
    target). Falls back to the original URL on any failure (timeout,
    malformed response, too many hops, or an unsafe hop) rather than
    raising, since this is a best-effort convenience, not a required step.
    """
    current = url
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            for _ in range(5):
                resp = await client.get(current)
                if resp.status_code not in (301, 302, 303, 307, 308):
                    break
                location = resp.headers.get("location")
                if not location:
                    break
                next_url = urljoin(current, location)
                is_safe, _ = await is_public_url(next_url)
                if not is_safe:
                    return url
                current = next_url
            else:
                # Exhausted the hop budget without landing on a final page.
                return url
    except Exception:
        return url

    if current.rstrip("/") == url.rstrip("/"):
        return url
    return normalize_url(current)
