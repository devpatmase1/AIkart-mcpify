import re
from typing import Dict, Any
from urllib.parse import urlparse


def sanitize_server_name(url: str) -> str:
    """Generate a clean server key name from the target hostname."""
    parsed = urlparse(url)
    hostname = parsed.hostname or "agent"
    clean_name = re.sub(r"[^a-zA-Z0-9_-]", "_", hostname)
    clean_name = re.sub(r"_+", "_", clean_name).strip("_")
    return clean_name.lower() or "agent_mcp"


def generate_mcp_configurations(
    url: str,
    recommended_mcp_endpoint: str | None = None,
    framework: str | None = None
) -> Dict[str, Any]:
    """
    Generates ready-to-use MCP configuration JSON structures:
    - Claude Desktop (mcpServers format with remote SSE/HTTP or mcp-remote bridge)
    - Cursor / VS Code config JSON
    - Remote MCP URL
    """
    endpoint = recommended_mcp_endpoint or (url.rstrip("/") + "/mcp")
    server_key = sanitize_server_name(url)

    # 1. Claude Desktop configuration
    claude_desktop_config = {
        "mcpServers": {
            server_key: {
                "command": "npx",
                "args": [
                    "-y",
                    "mcp-remote",
                    endpoint
                ]
            }
        }
    }

    # 2. Cursor / VS Code configuration
    cursor_vscode_config = {
        "mcpServers": {
            server_key: {
                "url": endpoint,
                "type": "sse"
            }
        }
    }

    return {
        "server_name": server_key,
        "target_url": url,
        "remote_mcp_url": endpoint,
        "detected_framework": framework or "Unknown",
        "claude_desktop": claude_desktop_config,
        "cursor_vscode": cursor_vscode_config
    }
