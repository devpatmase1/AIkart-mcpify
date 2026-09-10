"""
A checked-in, categorized corpus of real-world URLs for testing MCPify's
detection/proxy logic at scale (100+ targets) instead of ad hoc manual
spot-checks. Mix of stable, well-known public APIs, real marketing/SPA
sites, real OAuth-protected MCP servers, and synthetic edge cases (SSRF
vectors, malformed input, SSL edge cases from badssl.com's dedicated
test suite).

Third-party sites change or go down independent of MCPify's own code, so
this is NOT meant to be run on every commit (see test_real_world_corpus.py
for which categories get hard assertions vs. an informational report) -
run it on a schedule instead (nightly/weekly CI cron) and triage a
failure as "target changed" vs. "our code broke" before treating it as a
real regression.
"""

CORPUS = [
    # ---- native MCP servers (real, publicly known, some OAuth-protected) ----
    ("native_mcp", "https://catfact.ninja"),
    ("native_mcp", "https://mcp.deepwiki.com"),
    ("native_mcp", "https://api.stocklake.dev"),
    ("native_mcp", "https://mcp.sentry.dev"),  # OAuth-protected (401 + WWW-Authenticate)
    ("native_mcp", "https://mcp.supermetrics.com"),  # OAuth-protected
    ("native_mcp", "https://vibeprospecting.explorium.ai"),  # OAuth-protected
    ("native_mcp", "https://slack.com"),  # OAuth-protected, found via this corpus

    # ---- public REST APIs, no auth required, stable ----
    ("rest_api", "https://jsonplaceholder.typicode.com"),
    ("rest_api", "https://dog.ceo/api"),
    ("rest_api", "https://pokeapi.co"),
    ("rest_api", "https://httpbin.org"),
    ("rest_api", "https://randomuser.me"),
    ("rest_api", "https://api.adviceslip.com"),
    ("rest_api", "https://api.open-meteo.com"),
    ("rest_api", "https://openlibrary.org"),
    ("rest_api", "https://api.spacexdata.com"),
    ("rest_api", "https://api.coingecko.com/api/v3"),
    ("rest_api", "https://api.github.com"),
    ("rest_api", "https://official-joke-api.appspot.com"),
    ("rest_api", "https://api.agify.io"),
    ("rest_api", "https://api.genderize.io"),
    ("rest_api", "https://api.nationalize.io"),
    ("rest_api", "http://numbersapi.com"),
    ("rest_api", "https://date.nager.at"),
    ("rest_api", "https://api.ipify.org"),
    ("rest_api", "https://restcountries.com"),
    ("rest_api", "https://api.quotable.io"),
    ("rest_api", "https://reqres.in"),
    ("rest_api", "https://dummyjson.com"),
    ("rest_api", "https://fakestoreapi.com"),
    ("rest_api", "https://rickandmortyapi.com"),
    ("rest_api", "https://swapi.dev"),
    ("rest_api", "https://api.exchangerate-api.com"),
    ("rest_api", "http://api.open-notify.org"),
    ("rest_api", "https://deckofcardsapi.com"),
    ("rest_api", "https://jservice.io"),
    ("rest_api", "https://api.thecatapi.com"),
    ("rest_api", "https://api.chucknorris.io"),
    ("rest_api", "https://api.kanye.rest"),
    ("rest_api", "https://type.fit"),
    ("rest_api", "https://api.zippopotam.us"),
    ("rest_api", "https://api.tvmaze.com"),
    ("rest_api", "https://opentdb.com"),
    ("rest_api", "https://api.frankfurter.app"),
    ("rest_api", "https://api.first.org"),

    # ---- marketing / SPA / docs-only sites (real companies) ----
    ("marketing", "https://stripe.com"),
    ("marketing", "https://vercel.com"),
    ("marketing", "https://n8n.io"),
    ("marketing", "https://www.notion.com"),
    ("marketing", "https://www.figma.com"),
    ("marketing", "https://www.anthropic.com"),
    ("marketing", "https://openai.com"),
    ("marketing", "https://chatgpt.com"),
    ("marketing", "https://www.cloudflare.com"),
    ("marketing", "https://www.google.com"),
    ("marketing", "https://www.amazon.com"),
    ("marketing", "https://www.microsoft.com"),
    ("marketing", "https://www.apple.com"),
    ("marketing", "https://www.netflix.com"),
    ("marketing", "https://www.spotify.com"),
    ("marketing", "https://www.airbnb.com"),
    ("marketing", "https://www.uber.com"),
    ("marketing", "https://www.salesforce.com"),
    ("marketing", "https://www.adobe.com"),
    ("marketing", "https://www.shopify.com"),
    ("marketing", "https://x.com"),
    ("marketing", "https://www.linkedin.com"),
    ("marketing", "https://www.reddit.com"),
    ("marketing", "https://www.dropbox.com"),
    ("marketing", "https://zoom.us"),
    ("marketing", "https://www.atlassian.com"),
    ("marketing", "https://www.digitalocean.com"),
    ("marketing", "https://higgsfield.ai"),
    ("marketing", "https://www.opera.com"),

    # ---- redirect chains (http->https, apex->www) ----
    ("redirect", "http://google.com"),
    ("redirect", "http://github.com"),
    ("redirect", "http://openai.com"),
    ("redirect", "http://microsoft.com"),
    ("redirect", "http://apple.com"),
    ("redirect", "http://facebook.com"),
    ("redirect", "http://twitter.com"),
    ("redirect", "http://amazon.com"),
    ("redirect", "http://netflix.com"),
    ("redirect", "http://spotify.com"),

    # ---- SSL edge cases (badssl.com's dedicated test suite) ----
    ("ssl_edge", "https://expired.badssl.com"),
    ("ssl_edge", "https://self-signed.badssl.com"),
    ("ssl_edge", "https://wrong.host.badssl.com"),
    ("ssl_edge", "https://untrusted-root.badssl.com"),
    ("ssl_edge", "https://revoked.badssl.com"),
    ("ssl_edge", "https://pinning-test.badssl.com"),
    ("ssl_edge", "https://sha1-intermediate.badssl.com"),
    ("ssl_edge", "https://rc4.badssl.com"),
    ("ssl_edge", "https://dh480.badssl.com"),
    ("ssl_edge", "https://null.badssl.com"),

    # ---- SSRF attack vectors (must all be refused) ----
    ("ssrf", "http://localhost"),
    ("ssrf", "http://127.0.0.1"),
    ("ssrf", "http://127.0.0.1:8080"),
    ("ssrf", "http://169.254.169.254"),
    ("ssrf", "http://169.254.170.2"),
    ("ssrf", "http://0.0.0.0"),
    ("ssrf", "http://10.0.0.1"),
    ("ssrf", "http://172.16.0.1"),
    ("ssrf", "http://192.168.1.1"),
    ("ssrf", "http://[::1]"),
    ("ssrf", "http://2130706433"),  # decimal-encoded 127.0.0.1
    ("ssrf", "http://0177.0.0.1"),  # octal-encoded 127.0.0.1

    # ---- malformed / garbage input (must not crash) ----
    ("malformed", "javascript:alert(1)"),
    ("malformed", "data:text/html;base64,PHNjcmlwdD4="),
    ("malformed", "not-a-url-at-all"),
    ("malformed", "ftp://example.com"),
    ("malformed", ""),
    ("malformed", "   "),
    ("malformed", "http://"),
    ("malformed", "https://"),
    ("malformed", "http://" + "a" * 2000 + ".com"),
    ("malformed", "http://exa mple.com"),

    # ---- unreachable / DNS failure ----
    ("unreachable", "https://this-domain-definitely-does-not-exist-xyz123abc.com"),
    ("unreachable", "https://another-fake-domain-that-should-never-resolve-99.com"),
    ("unreachable", "https://totally-made-up-nonexistent-host-42.net"),

    # ---- misc real-world oddities ----
    ("misc", "https://en.wikipedia.org"),
    ("misc", "https://docs.python.org"),
    ("misc", "https://www.wikipedia.org"),
    ("misc", "https://countries.trevorblades.com"),  # GraphQL API
    ("misc", "https://api.nasa.gov"),
    ("misc", "https://developer.mozilla.org"),
]
