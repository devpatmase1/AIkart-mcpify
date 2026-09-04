import asyncio
import re
from typing import Dict, List, Any, Optional
from urllib.parse import urlparse
import httpx
from app.proxy import proxy_manager
from app.security import is_public_url



COMMON_ENDPOINTS = [
    "/mcp",
    "/sse",
    "/health",
    "/tools",
    "/docs",
    "/openapi.json"
]


def normalize_url(url: str) -> str:
    """Ensure URL has scheme and strip trailing slash."""
    url = url.strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        url = f"https://{url}"
    return url.rstrip("/")


async def probe_endpoint(client: httpx.AsyncClient, base_url: str, path: str) -> Dict[str, Any]:
    """Probe an individual endpoint on the target agent."""
    target_url = f"{base_url}{path}"
    try:
        # No follow_redirects: the SSRF guard only validates target_url itself,
        # so a redirect response could point anywhere (including internal
        # addresses) without being re-checked.
        response = await client.get(target_url, timeout=6.0)
        is_accessible = response.status_code < 400 or response.status_code == 405
        
        # Try to safely read partial text/json content
        content_preview = ""
        try:
            content_preview = response.text[:2000]
        except Exception:
            pass

        return {
            "path": path,
            "url": str(response.url),
            "status_code": response.status_code,
            "accessible": is_accessible,
            "headers": dict(response.headers),
            "content_type": response.headers.get("content-type", ""),
            "content_preview": content_preview
        }
    except httpx.RequestError as e:
        return {
            "path": path,
            "url": target_url,
            "status_code": None,
            "accessible": False,
            "error": str(e),
            "headers": {},
            "content_type": "",
            "content_preview": ""
        }


def detect_framework_and_confidence(
    base_url: str,
    root_probe: Dict[str, Any],
    probe_results: Dict[str, Dict[str, Any]]
) -> tuple[str, float, Dict[str, Any]]:
    """
    Detects framework (FastAPI, Flask, LangChain, Express, Next.js, or Generic)
    and calculates a confidence score based on headers, OpenAPI schemas, HTML signatures, and paths.
    """
    scores: Dict[str, float] = {
        "FastAPI": 0.0,
        "Flask": 0.0,
        "LangChain": 0.0,
        "Express": 0.0,
        "Next.js": 0.0
    }
    signals: List[str] = []

    # Gather all headers and body texts
    all_probes = [root_probe] + list(probe_results.values())
    combined_headers: Dict[str, str] = {}
    combined_text = ""
    
    for probe in all_probes:
        headers = probe.get("headers", {})
        for k, v in headers.items():
            combined_headers[k.lower()] = v.lower()
        combined_text += " " + probe.get("content_preview", "").lower()

    server_header = combined_headers.get("server", "")
    x_powered_by = combined_headers.get("x-powered-by", "")

    # 1. FastAPI Signals
    docs_probe = probe_results.get("/docs", {})
    openapi_probe = probe_results.get("/openapi.json", {})

    if "uvicorn" in server_header:
        scores["FastAPI"] += 0.4
        signals.append("Server header contains 'uvicorn'")
    
    if openapi_probe.get("accessible") and openapi_probe.get("status_code") == 200:
        scores["FastAPI"] += 0.4
        signals.append("OpenAPI specification available at /openapi.json")
        if "fastapi" in openapi_probe.get("content_preview", "").lower():
            scores["FastAPI"] += 0.3
            signals.append("FastAPI signature detected in OpenAPI schema")

    if docs_probe.get("accessible") and "swagger ui" in docs_probe.get("content_preview", "").lower():
        scores["FastAPI"] += 0.3
        signals.append("Swagger UI documentation available at /docs")

    # 2. Flask Signals
    if "werkzeug" in server_header or "flask" in server_header:
        scores["Flask"] += 0.7
        signals.append("Server header contains Werkzeug/Flask")
    if "session" in combined_headers.get("set-cookie", "") and "flask" in combined_headers.get("set-cookie", ""):
        scores["Flask"] += 0.4
        signals.append("Flask session cookie detected")

    # 3. LangChain / LangServe / LangGraph Signals
    if "langserve" in combined_text or "langchain" in combined_text or "runnable" in combined_text:
        scores["LangChain"] += 0.6
        signals.append("LangChain/LangServe signatures found in response payload or endpoints")
    if "/invoke" in combined_text or "/batch" in combined_text or "/stream" in combined_text:
        scores["LangChain"] += 0.3
        signals.append("LangServe runnable routes detected")

    # 4. Express Signals
    if "express" in x_powered_by:
        scores["Express"] += 0.8
        signals.append("X-Powered-By header is Express")
    elif "express" in server_header:
        scores["Express"] += 0.6
        signals.append("Server header indicates Express")

    # 5. Next.js Signals
    if "next.js" in x_powered_by or "next" in x_powered_by:
        scores["Next.js"] += 0.8
        signals.append("X-Powered-By header is Next.js")
    if "/_next/" in combined_text or "__next" in combined_text:
        scores["Next.js"] += 0.5
        signals.append("Next.js static asset markup detected")

    # Determine best match
    best_framework = max(scores, key=scores.get)
    best_score = scores[best_framework]

    if best_score == 0.0:
        detected_framework = "Generic HTTP Agent"
        confidence_score = 0.5
    else:
        detected_framework = best_framework
        confidence_score = min(round(best_score, 2), 0.99)

    details = {
        "signals": signals,
        "framework_scores": {k: round(v, 2) for k, v in scores.items()}
    }

    return detected_framework, confidence_score, details


def determine_recommended_mcp_endpoint(
    base_url: str,
    probe_results: Dict[str, Dict[str, Any]]
) -> str:
    """Determine the optimal MCP endpoint URL for the agent."""
    mcp_probe = probe_results.get("/mcp")
    sse_probe = probe_results.get("/sse")
    
    # If /mcp endpoint exists or responded
    if mcp_probe and (mcp_probe.get("accessible") or mcp_probe.get("status_code") in [200, 400, 405]):
        return f"{base_url}/mcp"
    
    # If /sse endpoint exists or responded
    if sse_probe and (sse_probe.get("accessible") or sse_probe.get("status_code") in [200, 400, 405]):
        return f"{base_url}/sse"

    # Default to /mcp convention
    return f"{base_url}/mcp"


async def analyze_agent_url(url: str) -> Dict[str, Any]:
    """
    Main analysis function.
    Probes standard endpoints, inspects response signatures,
    and returns framework detection, confidence score, available endpoints,
    and recommended MCP endpoint.
    """
    normalized_url = normalize_url(url)

    is_safe, reason = await is_public_url(normalized_url)
    if not is_safe:
        raise ValueError(f"Refusing to probe target: {reason}")

    limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
    async with httpx.AsyncClient(limits=limits, timeout=8.0) as client:
        # Probe root and common endpoints concurrently
        tasks = [probe_endpoint(client, normalized_url, "")] + [
            probe_endpoint(client, normalized_url, path) for path in COMMON_ENDPOINTS
        ]
        results = await asyncio.gather(*tasks, return_exceptions=False)

    root_probe = results[0]
    endpoint_probes = {path: res for path, res in zip(COMMON_ENDPOINTS, results[1:])}

    # Available endpoints summary
    available_endpoints = []
    for path, probe in endpoint_probes.items():
        available_endpoints.append({
            "path": path,
            "status_code": probe.get("status_code"),
            "accessible": probe.get("accessible", False),
            "content_type": probe.get("content_type", "")
        })

    detected_framework, confidence_score, details = detect_framework_and_confidence(
        normalized_url, root_probe, endpoint_probes
    )

    mcp_probe = endpoint_probes.get("/mcp")
    sse_probe = endpoint_probes.get("/sse")
    has_mcp = bool(
        (mcp_probe and (mcp_probe.get("accessible") or mcp_probe.get("status_code") in [200, 400, 405])) or
        (sse_probe and (sse_probe.get("accessible") or sse_probe.get("status_code") in [200, 400, 405]))
    )

    if has_mcp:
        recommended_mcp = determine_recommended_mcp_endpoint(normalized_url, endpoint_probes)
        proxy_url = None
        proxy_id = None
    else:
        proxy = await proxy_manager.create_proxy(target_url=normalized_url, has_mcp=False)
        proxy_url = proxy["proxy_url"]
        proxy_id = proxy["proxy_id"]
        recommended_mcp = proxy_url

    return {
        "url": normalized_url,
        "detected_framework": detected_framework,
        "confidence_score": confidence_score,
        "available_endpoints": available_endpoints,
        "has_mcp": has_mcp,
        "recommended_mcp_endpoint": recommended_mcp,
        "proxy_url": proxy_url,
        "proxy_id": proxy_id,
        "details": details
    }

