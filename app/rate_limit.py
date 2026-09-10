from fastapi import Request
from slowapi import Limiter


def get_trusted_client_ip(request: Request) -> str:
    """
    Not slowapi's built-in get_ipaddr: it returns the WHOLE raw
    X-Forwarded-For header value verbatim, with no parsing at all - a
    client can send its own X-Forwarded-For and get treated as a
    different "client" on every request, bypassing rate limiting
    entirely. request.client.host alone doesn't work either (see below).

    Render sits in front of this app as the one trusted reverse proxy: it
    appends the real client IP as the LAST entry of X-Forwarded-For
    (standard reverse-proxy behavior), so any value a client forged into
    that header ends up to the left of it, never the rightmost entry.
    Taking the last entry is safe as long as exactly one trusted hop
    stands between the client and this app - true for Render's setup,
    not true if another proxy is ever added in front of it without
    updating this.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            return parts[-1]
    if request.client and request.client.host:
        return request.client.host
    return "127.0.0.1"


limiter = Limiter(key_func=get_trusted_client_ip)
