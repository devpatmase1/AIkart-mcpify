from enum import Enum
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, HttpUrl

from app.analyzer import analyze_agent_url
from app.generator import generate_mcp_configurations


router = APIRouter(prefix="", tags=["MCP Tools"])


class PlatformEnum(str, Enum):
    claude_desktop = "claude_desktop"
    cursor = "cursor"
    web = "web"


# ---------------------------------------------------------
# Request / Response Schemas
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
# Tool Implementations & Endpoints
# ---------------------------------------------------------

@router.post("/analyze", operation_id="analyze_agent", summary="Analyze AI Agent URL")
@router.get("/analyze", summary="Analyze AI Agent URL (GET)")
async def analyze_agent(
    payload: Optional[AnalyzeAgentRequest] = None,
    url: Optional[str] = Query(None, description="The agent URL if using GET")
) -> Dict[str, Any]:
    """
    Analyze any deployed AI agent URL.
    Probes common endpoints (/mcp, /sse, /health, /tools, /docs, /openapi.json),
    detects framework (FastAPI, Flask, LangChain, Express, Next.js),
    calculates confidence score, and determines the recommended MCP endpoint.
    """
    target_url = payload.url if payload else url
    if not target_url:
        raise HTTPException(status_code=400, detail="Missing required 'url' parameter.")
    
    try:
        result = await analyze_agent_url(target_url)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to analyze agent URL: {str(e)}")


@router.post("/generate", operation_id="generate_mcp_config", summary="Generate MCP Configurations")
@router.get("/generate", summary="Generate MCP Configurations (GET)")
async def generate_mcp_config(
    payload: Optional[GenerateConfigRequest] = None,
    url: Optional[str] = Query(None, description="The agent URL if using GET")
) -> Dict[str, Any]:
    """
    Generate Model Context Protocol (MCP) configuration snippets for Claude Desktop,
    Cursor/VS Code, and remote MCP URLs based on the analyzed agent endpoint.
    """
    target_url = payload.url if payload else url
    if not target_url:
        raise HTTPException(status_code=400, detail="Missing required 'url' parameter.")

    try:
        analysis = await analyze_agent_url(target_url)
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate MCP configs: {str(e)}")


@router.post("/guide", operation_id="get_integration_guide", summary="Get MCP Integration Guide")
@router.get("/guide", summary="Get MCP Integration Guide (GET)")
async def get_integration_guide(
    payload: Optional[IntegrationGuideRequest] = None,
    url: Optional[str] = Query(None, description="The agent URL if using GET"),
    platform: PlatformEnum = Query(PlatformEnum.claude_desktop, description="Target platform")
) -> Dict[str, Any]:
    """
    Get step-by-step instructions to integrate the target agent into Claude Desktop,
    Cursor, or custom web clients using MCP.
    """
    target_url = payload.url if payload else url
    target_platform = payload.platform if payload else platform

    if not target_url:
        raise HTTPException(status_code=400, detail="Missing required 'url' parameter.")

    analysis = await analyze_agent_url(target_url)
    configs = generate_mcp_configurations(
        url=analysis["url"],
        recommended_mcp_endpoint=analysis.get("recommended_mcp_endpoint"),
        framework=analysis.get("detected_framework")
    )
    
    server_name = configs["server_name"]
    mcp_endpoint = configs["remote_mcp_url"]

    guides = {
        PlatformEnum.claude_desktop: {
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
        PlatformEnum.cursor: {
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
        PlatformEnum.web: {
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

    selected_guide = guides.get(target_platform, guides[PlatformEnum.claude_desktop])

    return {
        "target_url": target_url,
        "recommended_mcp_endpoint": mcp_endpoint,
        "platform": target_platform.value,
        "guide": selected_guide
    }
