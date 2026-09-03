from slowapi import Limiter
from slowapi.util import get_ipaddr

# get_ipaddr (not get_remote_address) because Render sits behind a reverse
# proxy - request.client.host is the proxy's internal IP for every request,
# which would make the rate limit shared across all users instead of
# per-client. get_ipaddr reads X-Forwarded-For, which Render sets correctly.
limiter = Limiter(key_func=get_ipaddr)
