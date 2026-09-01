import os
import uuid
import json
import asyncio
from contextlib import asynccontextmanager
from typing import Dict, Any, Optional

from fastapi import FastAPI, Request, HTTPException, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field
import httpx
from dotenv import load_dotenv
from sse_starlette.sse import EventSourceResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi_mcp import FastApiMCP


from app.mcp_handler import router as mcp_router, mcp
from app.proxy import proxy_manager
from app.generator import generate_proxy_config

# Load environment variables
load_dotenv()

# Pre-initialize FastMCP ASGI apps (SSE transport & Streamable HTTP transport)
sse_app = mcp.sse_app()
http_app = mcp.streamable_http_app()

scheduler = AsyncIOScheduler()


async def ping_proxy_targets_job():
    """APScheduler job: Ping proxy targets every 10 minutes."""
    proxies = proxy_manager.list_proxies()
    if not proxies:
        return

    async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
        for p in proxies:
            proxy_id = p["proxy_id"]
            target_url = p["target_url"]
            health_target = f"{target_url}/health"
            try:
                resp = await client.get(health_target, follow_redirects=True)
                p["last_ping_status"] = resp.status_code
                print(f"[APScheduler] Pinged proxy {proxy_id} target ({health_target}) - Status: {resp.status_code}")
            except Exception as e:
                print(f"[APScheduler] Error pinging proxy {proxy_id} ({health_target}): {e}")


async def ping_keep_alive():
    """Ping /health every 10 minutes to prevent Render free-tier idling."""
    app_url = os.getenv("APP_URL", "http://127.0.0.1:10000").rstrip("/")
    health_url = f"{app_url}/health"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(health_url)
            print(f"[Keep-Alive] Pinged {health_url} - Status: {response.status_code}")
    except Exception as e:
        print(f"[Keep-Alive] Keep-alive ping error: {e}")


async def keep_alive_worker():
    """Background loop that periodically triggers the keep-alive ping."""
    while True:
        try:
            await asyncio.sleep(600)  # Wait 10 minutes
            await ping_keep_alive()
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[Keep-Alive] Background loop exception: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: launch keep-alive background worker
    worker_task = asyncio.create_task(keep_alive_worker())
    print("[MCPify] Keep-alive background worker initialized (interval: 10 mins).")

    # Startup: launch APScheduler for proxy targets
    try:
        scheduler.add_job(
            ping_proxy_targets_job,
            'interval',
            minutes=10,
            id='ping_proxy_targets',
            replace_existing=True
        )
        scheduler.start()
        print("[MCPify] APScheduler initialized (proxy target ping interval: 10 mins).")
    except Exception as e:
        print(f"[MCPify] APScheduler initialization notice: {e}")
    
    # Startup: enter FastMCP streamable HTTP session manager
    async with mcp.session_manager.run():
        print("[MCPify] FastMCP Streamable HTTP session manager active.")
        yield

    # Shutdown: stop scheduler & worker gracefully
    try:
        scheduler.shutdown()
    except Exception:
        pass
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass
    print("[MCPify] Keep-alive worker, scheduler, and MCP session manager stopped.")


# Initialize FastAPI application
app = FastAPI(
    title="MCPify Agent & Bridge",
    description=(
        "Analyze any AI agent URL, detect framework signatures, "
        "and generate ready-to-use Model Context Protocol (MCP) configs. "
        "Exposes dual-mode MCP endpoints and dynamic MCP proxy middleware."
    ),
    version="1.1.0",
    lifespan=lifespan
)

# CORS enabled for all origins and headers
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Request schemas
class CreateProxyRequest(BaseModel):
    url: str = Field(..., description="Target AI agent URL to proxy")


# 1. Health check & Web UI / Root endpoints
@app.get("/health", summary="Health Check")
async def health_check():
    """Health check endpoint returning service status."""
    return {"status": "ok"}


@app.get("/", summary="MCPify Web Interface", response_class=HTMLResponse)
async def root():
    """Serves the MCPify Web UI frontend."""
    html_path = os.path.join(os.path.dirname(__file__), "app", "web", "index.html")
    if os.path.exists(html_path):
        return FileResponse(html_path)
    return HTMLResponse("<h1>MCPify API Running</h1>")


@app.get("/api", summary="Root Discovery Index")
async def api_info():
    """Landing endpoint with API discovery details."""
    return {
        "service": "MCPify",
        "description": "AI Agent URL Analyzer & Proxy MCP Server",
        "version": "1.1.0",
        "mcp_endpoints": {
            "streamable_http": {
                "method": "POST",
                "path": "/mcp",
                "transport": "HTTP Streamable (MCP 2024-11 standard)"
            },
            "sse_transport": {
                "method": "GET",
                "path": "/mcp/sse",
                "messages_path": "/mcp/messages",
                "transport": "Server-Sent Events (SSE)"
            }
        },
        "proxy_endpoints": {
            "create_proxy": "POST /proxy/create",
            "proxy_mcp": "GET /proxy/{proxy_id}/mcp",
            "proxy_health": "GET /proxy/{proxy_id}/health",
            "list_proxies": "GET /proxy/list"
        },
        "rest_endpoints": {
            "health": "/health",
            "analyze_agent": "/analyze",
            "generate_config": "/generate",
            "integration_guide": "/guide",
            "openapi_docs": "/docs"
        }
    }


# 2. PROXY ENDPOINTS

@app.post("/proxy/create", summary="Create Proxy MCP Endpoint")
async def create_proxy_endpoint(payload: CreateProxyRequest):
    """Creates a proxy MCP endpoint for a target URL."""
    target_url = payload.url.strip()
    if not target_url:
        raise HTTPException(status_code=400, detail="Missing required 'url' parameter.")

    normalized_url = target_url.rstrip("/")
    if not normalized_url.startswith("http://") and not normalized_url.startswith("https://"):
        normalized_url = f"https://{normalized_url}"

    has_mcp = False
    try:
        async with httpx.AsyncClient(timeout=5.0, verify=False) as client:
            resp = await client.get(f"{normalized_url}/mcp", follow_redirects=True)
            if resp.status_code in [200, 400, 405]:
                has_mcp = True
    except Exception:
        has_mcp = False

    proxy_data = proxy_manager.create_proxy(target_url=normalized_url, has_mcp=has_mcp)
    proxy_id = proxy_data["proxy_id"]
    proxy_url = proxy_data["proxy_url"]

    configs = generate_proxy_config(proxy_url, normalized_url)

    return {
        "proxy_id": proxy_id,
        "proxy_url": proxy_url,
        "target_url": proxy_data["target_url"],
        "claude_desktop_config": configs["claude_desktop"],
        "cursor_config": configs["cursor_vscode"],
        "status": proxy_data["status"]
    }


@app.get("/proxy/list", summary="List Active Proxies")
async def list_proxies_endpoint():
    """List all active proxy sessions."""
    proxies = proxy_manager.list_proxies()
    return {
        "proxies": proxies,
        "total": len(proxies)
    }


@app.get("/proxy/{proxy_id}/health", summary="Check Proxy & Target Health")
async def proxy_health_endpoint(proxy_id: str):
    """Check if proxy is active and target URL is reachable."""
    proxy = proxy_manager.get_proxy(proxy_id)
    if not proxy:
        raise HTTPException(status_code=404, detail=f"Proxy ID '{proxy_id}' not found.")

    target_url = proxy["target_url"]
    target_reachable = False
    status_code = None
    latency_ms = None

    start_time = asyncio.get_event_loop().time()
    try:
        async with httpx.AsyncClient(timeout=8.0, verify=False) as client:
            resp = await client.get(target_url, follow_redirects=True)
            status_code = resp.status_code
            target_reachable = resp.status_code < 500
            latency_ms = round((asyncio.get_event_loop().time() - start_time) * 1000, 2)
    except Exception as e:
        pass

    return {
        "proxy_id": proxy_id,
        "proxy_status": "active",
        "target_url": target_url,
        "target_reachable": target_reachable,
        "target_status_code": status_code,
        "latency_ms": latency_ms,
        "has_mcp": proxy.get("has_mcp", False),
        "created_at": proxy.get("created_at"),
        "last_used": proxy.get("last_used")
    }


@app.get("/proxy/{proxy_id}/mcp", summary="Proxy MCP SSE Endpoint")
@app.get("/proxy/{proxy_id}/mcp/", summary="Proxy MCP SSE Endpoint (Trailing Slash)")
async def proxy_mcp_sse_endpoint(proxy_id: str, request: Request):
    """SSE connection endpoint for proxy MCP clients."""
    proxy = proxy_manager.get_proxy(proxy_id)

    session_id = str(uuid.uuid4())
    queue = asyncio.Queue()
    proxy_manager.sessions[session_id] = queue

    app_url = proxy_manager.get_base_app_url()
    messages_url = f"{app_url}/proxy/{proxy_id}/messages?session_id={session_id}"

    async def event_generator():
        try:
            # Send initial endpoint event per MCP SSE spec
            yield {
                "event": "endpoint",
                "data": messages_url
            }

            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=20.0)
                    yield {
                        "event": "message",
                        "data": json.dumps(msg)
                    }
                except asyncio.TimeoutError:
                    # Keep connection alive with silent ping
                    yield {
                        "event": "ping",
                        "data": ""
                    }
        finally:
            proxy_manager.sessions.pop(session_id, None)

    return EventSourceResponse(event_generator())


@app.post("/proxy/{proxy_id}/mcp", summary="Proxy MCP Streamable HTTP Endpoint")
@app.post("/proxy/{proxy_id}/mcp/", summary="Proxy MCP Streamable HTTP Endpoint (Trailing Slash)")
async def proxy_mcp_http_endpoint(
    proxy_id: str,
    payload: Dict[str, Any] = Body(...)
):
    """HTTP Streamable POST endpoint for proxy MCP clients."""
    proxy = proxy_manager.get_proxy(proxy_id)
    response_data = await proxy_manager.forward_request(proxy_id, payload)
    return response_data


@app.post("/proxy/{proxy_id}/messages", summary="Proxy MCP Message Handler")
@app.post("/proxy/{proxy_id}/messages/", summary="Proxy MCP Message Handler (Trailing Slash)")
async def proxy_mcp_messages_endpoint(
    proxy_id: str,
    session_id: str = Query(...),
    payload: Dict[str, Any] = Body(...)
):
    """Receive JSON-RPC messages from MCP clients and push responses to SSE stream."""
    proxy = proxy_manager.get_proxy(proxy_id)

    session_queue = proxy_manager.sessions.get(session_id)
    if not session_queue:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")

    response_data = await proxy_manager.forward_request(proxy_id, payload)
    if response_data:
        await session_queue.put(response_data)

    return {"status": "accepted"}



# 3. Include REST routes (/analyze, /generate, /guide)
app.include_router(mcp_router)

# 4. Expose all FastAPI /api/ & REST endpoints as MCP tools via FastApiMCP at /mcp-server
fastapi_mcp = FastApiMCP(app, name="DataHub Talk to Data")
fastapi_mcp.mount_sse(mount_path="/mcp-server/sse")
fastapi_mcp.mount_http(mount_path="/mcp-server/mcp")
fastapi_mcp.mount(mount_path="/mcp-server")

# 5. Mount SSE transport at /mcp (exposes GET /mcp/sse and POST /mcp/messages)
app.mount("/mcp", sse_app)

# 6. Frontend Catch-All Route (MUST be defined AFTER all API & MCP routes/mounts)
@app.get("/{full_path:path}", response_class=HTMLResponse, include_in_schema=False)
async def serve_frontend_catch_all(full_path: str):
    """
    Catch-all route serving frontend Web UI.
    Excludes API, MCP, Proxy, Docs, and Health endpoints from returning HTML SPA page.
    """
    clean_path = full_path.lstrip("/")
    if (
        clean_path.startswith("mcp-server") or
        clean_path.startswith("api") or
        clean_path.startswith("health") or
        clean_path.startswith("mcp") or
        clean_path.startswith("proxy") or
        clean_path.startswith("docs") or
        clean_path.startswith("openapi.json") or
        clean_path.startswith("redoc")
    ):
        raise HTTPException(status_code=404, detail="Endpoint not found.")

    html_path = os.path.join(os.path.dirname(__file__), "app", "web", "index.html")
    if os.path.exists(html_path):
        return FileResponse(html_path)
    return HTMLResponse("<h1>MCPify API Running</h1>")


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)

