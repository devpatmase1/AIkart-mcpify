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


def _build_platform_configs(endpoint: str, server_key: str) -> Dict[str, Any]:
    """
    Builds config snippets for every MCP client we support, keyed by
    platform. Each client's remote-MCP field format differs (some use
    "mcpServers", VS Code uses "servers"; some want a bare "url", others
    need the mcp-remote bridge via "command"/"args") - this is the single
    place that encodes those differences, shared by both the direct-MCP
    and proxy code paths so they never drift apart.
    """
    return {
        "claude_desktop": {
            "mcpServers": {
                server_key: {
                    "command": "npx",
                    "args": ["-y", "mcp-remote", endpoint]
                }
            }
        },
        "cursor_vscode": {
            "mcpServers": {
                server_key: {
                    "url": endpoint,
                    "type": "sse"
                }
            }
        },
        "windsurf": {
            "mcpServers": {
                server_key: {
                    "serverUrl": endpoint
                }
            }
        },
        "cline": {
            "mcpServers": {
                server_key: {
                    "url": endpoint,
                    "type": "sse",
                    "disabled": False,
                    "autoApprove": []
                }
            }
        },
        "vscode": {
            "servers": {
                server_key: {
                    "url": endpoint,
                    "type": "http"
                }
            }
        },
        "claude_code_cli": {
            "command": f"claude mcp add --transport sse {server_key} {endpoint}"
        }
    }


def generate_proxy_config(proxy_url: str, target_url: str = "") -> Dict[str, Any]:
    """Generates config snippets for every supported MCP client, using the proxy URL."""
    server_key = sanitize_server_name(target_url or proxy_url) + "_proxy"
    configs = _build_platform_configs(proxy_url, server_key)
    return {
        "server_name": server_key,
        "target_url": target_url,
        "proxy_url": proxy_url,
        **configs
    }


def generate_mcp_configurations(
    url: str,
    recommended_mcp_endpoint: str | None = None,
    framework: str | None = None,
    proxy_url: str | None = None
) -> Dict[str, Any]:
    """
    Generates ready-to-use MCP configuration snippets for every supported
    client: Claude Desktop, Cursor, Windsurf, Cline, VS Code (native MCP),
    and the Claude Code CLI.
    """
    endpoint = proxy_url or recommended_mcp_endpoint or (url.rstrip("/") + "/mcp")
    server_key = sanitize_server_name(url)
    if proxy_url:
        server_key = server_key + "_proxy"

    configs = _build_platform_configs(endpoint, server_key)

    res = {
        "server_name": server_key,
        "target_url": url,
        "remote_mcp_url": endpoint,
        "detected_framework": framework or "Unknown",
        **configs
    }
    if proxy_url:
        res["proxy_url"] = proxy_url
    return res
