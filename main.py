import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi_mcp import FastApiMCP
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import httpx
from dotenv import load_dotenv

from app.mcp_handler import router as mcp_router

# Load environment variables
load_dotenv()

# Initialize Keep-Alive Scheduler
scheduler = AsyncIOScheduler()


async def ping_keep_alive():
    """APScheduler task: ping /health every 10 minutes to prevent Render free-tier idling."""
    app_url = os.getenv("APP_URL", "http://127.0.0.1:10000").rstrip("/")
    health_url = f"{app_url}/health"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(health_url)
            print(f"[Keep-Alive] Pinged {health_url} - Status: {response.status_code}")
    except Exception as e:
        print(f"[Keep-Alive] Keep-alive ping error: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: start keep-alive scheduler
    scheduler.add_job(ping_keep_alive, "interval", minutes=10, id="keep_alive_job")
    scheduler.start()
    print("[MCPify] Keep-alive scheduler initialized (interval: 10 mins).")
    
    yield
    
    # Shutdown: stop scheduler gracefully
    scheduler.shutdown(wait=False)
    print("[MCPify] Keep-alive scheduler stopped.")


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

# Include MCP & analysis router
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
            "mcp_server": "/mcp",
            "analyze_agent": "/analyze",
            "generate_config": "/generate",
            "integration_guide": "/guide",
            "openapi_docs": "/docs"
        }
    }


# Initialize and mount FastApiMCP to expose /mcp endpoint
mcp = FastApiMCP(app)
mcp.mount()


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
