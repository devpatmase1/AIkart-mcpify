from enum import Enum
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from app.analyzer import analyze_agent_url
from app.generator import generate_mcp_configurations


# ---------------------------------------------------------
# MCP Server Instance (Official Anthropic MCP SDK)
# ---------------------------------------------------------
mcp = FastMCP(
    "MCPify Agent & Bridge",
    instructions=(
        "MCPify enables seamless discovery, probing, and MCP configuration "
        "generation for any deployed AI agent or API."
    ),
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)

# ---------------------------------------------------------
# REST Router
# ---------------------------------------------------------
router = APIRouter(prefix="", tags=["MCP Tools"])


class PlatformEnum(str, Enum):
    claude_desktop = "claude_desktop"
    cursor = "cursor"
    web = "web"


# ---------------------------------------------------------
# Request Schemas
# ---------------------------------------------------------

class AnalyzeAgentRequest(BaseModel):
    url: str = Field(..., description="The deployed agent URL to analyze (e.g. https://my-agent.onrender.com)")


class GenerateConfigRequest(BaseModel):
    url: str = Field(..., description="The deployed agent URL to generate MCP configuration for")


class IntegrationGuideRequest(BaseModel):
    url: str = Field(..., description="The deployed agent URL")
    platform: PlatformEnum = Field(
        default=PlatformEnum.claude_desktop,
        description="Target platform: 'claude_desktop', 'cursor', or 'web'"
    )


# ---------------------------------------------------------
# Core Helper Logic
# ---------------------------------------------------------

async def run_analysis(url: str) -> Dict[str, Any]:
    return await analyze_agent_url(url)


async def run_generation(url: str) -> Dict[str, Any]:
    analysis = await analyze_agent_url(url)
    configs = generate_mcp_configurations(
        url=analysis["url"],
        recommended_mcp_endpoint=analysis.get("recommended_mcp_endpoint"),
        framework=analysis.get("detected_framework")
    )
    return {
        "status": "success",
        "analysis": analysis,
        "configs": configs
    }


async def run_guide(url: str, platform: str = "claude_desktop") -> Dict[str, Any]:
    analysis = await analyze_agent_url(url)
    configs = generate_mcp_configurations(
        url=analysis["url"],
        recommended_mcp_endpoint=analysis.get("recommended_mcp_endpoint"),
        framework=analysis.get("detected_framework")
    )
    
    server_name = configs["server_name"]
    mcp_endpoint = configs["remote_mcp_url"]

    guides = {
        "claude_desktop": {
            "platform": "Claude Desktop",
            "config_file_location": {
                "macOS": "~/Library/Application Support/Claude/claude_desktop_config.json",
                "Windows": "%APPDATA%\\Claude\\claude_desktop_config.json",
                "Linux": "~/.config/Claude/claude_desktop_config.json"
            },
            "steps": [
                "1. Open your Claude Desktop configuration file.",
                "2. Add the generated server config under the 'mcpServers' object.",
                "3. Restart Claude Desktop.",
                "4. Look for the hammer icon in the bottom right corner of Claude chat."
            ],
            "config_snippet": configs["claude_desktop"]
        },
        "cursor": {
            "platform": "Cursor / VS Code",
            "config_file_location": {
                "Cursor": "Cursor Settings > Features > MCP > Add New MCP Server",
                "VS Code": ".vscode/mcp.json or workspace settings"
            },
            "steps": [
                "1. Open Cursor Settings (Cmd+, or Ctrl+,).",
                "2. Navigate to Features > MCP.",
                f"3. Click 'Add New MCP Server'. Name: '{server_name}', Type: 'sse', URL: '{mcp_endpoint}'.",
                "4. Verify the green active status indicator."
            ],
            "config_snippet": configs["cursor_vscode"]
        },
        "web": {
            "platform": "Custom Web / Agentic Frameworks",
            "config_file_location": {
                "API Endpoint": mcp_endpoint
            },
            "steps": [
                f"1. Connect via HTTP/SSE to the remote MCP URL: {mcp_endpoint}",
                "2. List tools via JSON-RPC 'tools/list'",
                "3. Execute tools via 'tools/call' with arguments in JSON format."
            ],
            "config_snippet": {
                "remote_mcp_url": mcp_endpoint,
                "protocol": "Model Context Protocol (JSON-RPC over HTTP/SSE)"
            }
        }
    }

    selected_guide = guides.get(platform, guides["claude_desktop"])

    return {
        "target_url": url,
        "recommended_mcp_endpoint": mcp_endpoint,
        "platform": platform,
        "guide": selected_guide
    }


# ---------------------------------------------------------
# MCP Server Tools (Invoked by Claude / Cursor via MCP)
# ---------------------------------------------------------

@mcp.tool(name="analyze_agent", description="Probe an AI agent URL, detect framework signatures, and find MCP endpoints.")
async def mcp_tool_analyze_agent(url: str) -> Dict[str, Any]:
    """Probes common endpoints (/mcp, /sse, /health, /tools, /docs, /openapi.json) and detects framework."""
    return await run_analysis(url)


@mcp.tool(name="generate_mcp_config", description="Generate ready-to-use MCP configuration JSON for Claude Desktop, Cursor, and VS Code.")
async def mcp_tool_generate_mcp_config(url: str) -> Dict[str, Any]:
    """Generates JSON configuration snippets for Claude Desktop, Cursor, and VS Code."""
    return await run_generation(url)


@mcp.tool(name="get_integration_guide", description="Get step-by-step setup guides for Claude Desktop, Cursor, or Web MCP clients.")
async def mcp_tool_get_integration_guide(url: str, platform: str = "claude_desktop") -> Dict[str, Any]:
    """Returns platform-specific setup instructions and configuration paths."""
    return await run_guide(url, platform)


# ---------------------------------------------------------
# REST HTTP Endpoints
# ---------------------------------------------------------

@router.post("/analyze", operation_id="analyze_agent", summary="Analyze AI Agent URL")
@router.get("/analyze", summary="Analyze AI Agent URL (GET)")
async def analyze_agent_endpoint(
    payload: Optional[AnalyzeAgentRequest] = None,
    url: Optional[str] = Query(None, description="The agent URL if using GET")
) -> Dict[str, Any]:
    target_url = payload.url if payload else url
    if not target_url:
        raise HTTPException(status_code=400, detail="Missing required 'url' parameter.")
    
    try:
        return await run_analysis(target_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to analyze agent URL: {str(e)}")


@router.post("/generate", operation_id="generate_mcp_config", summary="Generate MCP Configurations")
@router.get("/generate", summary="Generate MCP Configurations (GET)")
async def generate_mcp_config_endpoint(
    payload: Optional[GenerateConfigRequest] = None,
    url: Optional[str] = Query(None, description="The agent URL if using GET")
) -> Dict[str, Any]:
    target_url = payload.url if payload else url
    if not target_url:
        raise HTTPException(status_code=400, detail="Missing required 'url' parameter.")

    try:
        return await run_generation(target_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate MCP configs: {str(e)}")


@router.post("/guide", operation_id="get_integration_guide", summary="Get MCP Integration Guide")
@router.get("/guide", summary="Get MCP Integration Guide (GET)")
async def get_integration_guide_endpoint(
    payload: Optional[IntegrationGuideRequest] = None,
    url: Optional[str] = Query(None, description="The agent URL if using GET"),
    platform: PlatformEnum = Query(PlatformEnum.claude_desktop, description="Target platform")
) -> Dict[str, Any]:
    target_url = payload.url if payload else url
    target_platform = payload.platform.value if payload else platform.value

    if not target_url:
        raise HTTPException(status_code=400, detail="Missing required 'url' parameter.")

    try:
        return await run_guide(target_url, target_platform)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get integration guide: {str(e)}")
