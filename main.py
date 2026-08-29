import os
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import httpx
from dotenv import load_dotenv

from app.mcp_handler import router as mcp_router, mcp

# Load environment variables
load_dotenv()

# Pre-initialize FastMCP ASGI apps (SSE transport & Streamable HTTP transport)
sse_app = mcp.sse_app()
http_app = mcp.streamable_http_app()


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
    
    # Startup: enter FastMCP streamable HTTP session manager
    async with mcp.session_manager.run():
        print("[MCPify] FastMCP Streamable HTTP session manager active.")
        yield

    # Shutdown: stop worker gracefully
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass
    print("[MCPify] Keep-alive worker and MCP session manager stopped.")


# Initialize FastAPI application
app = FastAPI(
    title="MCPify Agent & Bridge",
    description=(
        "Analyze any AI agent URL, detect framework signatures, "
        "and generate ready-to-use Model Context Protocol (MCP) configs. "
        "Exposes dual-mode MCP endpoints: SSE (/mcp/sse) and Streamable HTTP (/mcp)."
    ),
    version="1.0.0",
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

# 1. Health check & Root discovery endpoints
@app.get("/health", summary="Health Check")
async def health_check():
    """Health check endpoint returning service status."""
    return {"status": "ok"}


@app.get("/", summary="Root Index")
async def root():
    """Landing endpoint with API discovery details."""
    return {
        "service": "MCPify",
        "description": "AI Agent URL Analyzer & Dual-Mode MCP Server",
        "version": "1.0.0",
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
        "rest_endpoints": {
            "health": "/health",
            "analyze_agent": "/analyze",
            "generate_config": "/generate",
            "integration_guide": "/guide",
            "openapi_docs": "/docs"
        }
    }


# 2. Include REST routes (/analyze, /generate, /guide)
app.include_router(mcp_router)

# 3. Mount SSE transport at /mcp (exposes GET /mcp/sse and POST /mcp/messages)
app.mount("/mcp", sse_app)

# 4. Mount Streamable HTTP transport at root (exposes POST /mcp, GET /mcp, DELETE /mcp)
app.mount("", http_app)


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
