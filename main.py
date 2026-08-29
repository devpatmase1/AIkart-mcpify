import os
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
import httpx
from dotenv import load_dotenv

from app.mcp_handler import router as mcp_router, mcp

# Load environment variables
load_dotenv()


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
    # Startup: start keep-alive background worker
    worker_task = asyncio.create_task(keep_alive_worker())
    print("[MCPify] Keep-alive background worker initialized (interval: 10 mins).")
    
    yield
    
    # Shutdown: stop worker gracefully
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass
    print("[MCPify] Keep-alive worker stopped.")


# Initialize FastAPI application
app = FastAPI(
    title="MCPify Agent & Bridge",
    description=(
        "Analyze any AI agent URL, detect framework signatures, "
        "and generate ready-to-use Model Context Protocol (MCP) configs. "
        "Exposes its own /mcp endpoint for instant MCP integration."
    ),
    version="1.0.0",
    lifespan=lifespan
)

# CORS enabled for all origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include REST endpoints (/analyze, /generate, /guide)
app.include_router(mcp_router)


@app.get("/health", summary="Health Check")
async def health_check():
    """Health check endpoint returning service status."""
    return {"status": "ok"}


@app.get("/", summary="Root Index")
async def root():
    """Landing endpoint with API discovery details."""
    return {
        "service": "MCPify",
        "description": "AI Agent URL Analyzer & MCP Config Generator",
        "version": "1.0.0",
        "endpoints": {
            "health": "/health",
            "mcp_server": "/mcp/sse",
            "analyze_agent": "/analyze",
            "generate_config": "/generate",
            "integration_guide": "/guide",
            "openapi_docs": "/docs"
        }
    }


# Convenience redirect from /mcp to /mcp/sse for clients querying /mcp
@app.get("/mcp", summary="MCP Server SSE Redirect")
async def mcp_redirect():
    """Redirects to the active MCP SSE streaming endpoint."""
    return RedirectResponse(url="/mcp/sse", status_code=307)


# Mount the official Anthropic MCP SSE app at /mcp
app.mount("/mcp", mcp.sse_app())


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
