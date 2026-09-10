"""
Runs app.analyzer.analyze_agent_url against the ~128-entry real-world
corpus (tests/real_world_corpus.py). Marked @pytest.mark.network - not
part of the default fast run (`pytest tests/`), since it makes 100+ live
outbound requests to third-party sites. Run explicitly with:

    pytest tests/test_real_world_corpus.py -m network -v

Assertions are split deliberately:
- Categories with a stable, code-controlled expected outcome (SSRF
  vectors must be refused, malformed input must be refused, nothing may
  ever crash) are hard-asserted - these are real regressions if they fail.
- Categories that depend on a specific third party's current behavior
  (a REST API's uptime, whether a marketing site happens to have added a
  real MCP server) are checked only for "didn't crash" and reported as
  an informational breakdown - a third party changing is not a MCPify
  bug, and hard-asserting exact has_mcp values here would make this test
  flaky through no fault of the code.
"""
import asyncio
from collections import defaultdict

import pytest

from app.analyzer import analyze_agent_url
from real_world_corpus import CORPUS

pytestmark = pytest.mark.network

CONCURRENCY = 8


async def _run_one(category: str, url: str) -> dict:
    sem = _run_one.sem
    async with sem:
        try:
            result = await asyncio.wait_for(analyze_agent_url(url), timeout=25.0)
            return {"category": category, "url": url, "outcome": "OK", "has_mcp": result.get("has_mcp"), "error": None}
        except ValueError as e:
            return {"category": category, "url": url, "outcome": "REFUSED", "has_mcp": None, "error": str(e)}
        except asyncio.TimeoutError:
            return {"category": category, "url": url, "outcome": "TIMEOUT", "has_mcp": None, "error": "25s timeout"}
        except Exception as e:
            return {"category": category, "url": url, "outcome": "CRASH", "has_mcp": None, "error": f"{type(e).__name__}: {e}"}


_run_one.sem = asyncio.Semaphore(CONCURRENCY)


@pytest.fixture(scope="module")
def corpus_results():
    async def run_all():
        return await asyncio.gather(*(_run_one(cat, url) for cat, url in CORPUS))
    return asyncio.run(run_all())


def test_nothing_crashes(corpus_results):
    """The one assertion that must ALWAYS hold, regardless of what any third party does: no unhandled exception."""
    crashes = [r for r in corpus_results if r["outcome"] == "CRASH"]
    assert not crashes, "Unhandled crashes in analyze_agent_url:\n" + "\n".join(f"  {r['url']}: {r['error']}" for r in crashes)


def test_ssrf_vectors_all_refused(corpus_results):
    ssrf_results = [r for r in corpus_results if r["category"] == "ssrf"]
    assert ssrf_results, "ssrf category is empty - corpus regressed"
    not_refused = [r for r in ssrf_results if r["outcome"] != "REFUSED"]
    assert not not_refused, "SSRF vectors that were NOT refused (security regression):\n" + "\n".join(
        f"  {r['url']} -> {r['outcome']}" for r in not_refused
    )


def test_malformed_input_all_refused(corpus_results):
    malformed_results = [r for r in corpus_results if r["category"] == "malformed"]
    assert malformed_results, "malformed category is empty - corpus regressed"
    not_refused = [r for r in malformed_results if r["outcome"] != "REFUSED"]
    assert not not_refused, "Malformed input that was NOT cleanly refused:\n" + "\n".join(
        f"  {r['url']!r} -> {r['outcome']}" for r in not_refused
    )


def test_unreachable_domains_refused(corpus_results):
    unreachable_results = [r for r in corpus_results if r["category"] == "unreachable"]
    assert unreachable_results, "unreachable category is empty - corpus regressed"
    not_refused = [r for r in unreachable_results if r["outcome"] != "REFUSED"]
    assert not not_refused, "Unreachable domains that did NOT fail cleanly:\n" + "\n".join(
        f"  {r['url']} -> {r['outcome']}" for r in not_refused
    )


def test_known_stable_native_mcp_anchors(corpus_results):
    """
    catfact.ninja and mcp.deepwiki.com are chosen specifically for CI
    stability (small, single-purpose, unlikely to change transport). The
    other native_mcp entries (Slack, Sentry, Supermetrics, Explorium) are
    real but not under our control - left to the informational report,
    not hard-asserted here, so a change on their end doesn't flake CI.
    """
    by_url = {r["url"]: r for r in corpus_results}
    for anchor in ("https://catfact.ninja", "https://mcp.deepwiki.com"):
        r = by_url.get(anchor)
        assert r is not None, f"{anchor} missing from corpus"
        assert r["outcome"] == "OK" and r["has_mcp"] is True, f"{anchor} regressed: {r}"


def test_informational_breakdown(corpus_results):
    """Not a real assertion - just prints a category breakdown for human review when run with -s."""
    by_cat = defaultdict(lambda: defaultdict(int))
    for r in corpus_results:
        by_cat[r["category"]][r["outcome"]] += 1
    print("\n=== Real-world corpus breakdown ===")
    for cat, outcomes in sorted(by_cat.items()):
        print(f"  {cat:12}: {dict(outcomes)}")
    true_positives = [r["url"] for r in corpus_results if r["category"] in ("marketing", "misc") and r["has_mcp"] is True]
    if true_positives:
        print(f"  Marketing/misc sites with a real detected MCP server (verify manually, not necessarily a bug): {true_positives}")
    assert True
