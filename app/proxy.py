import os
import uuid
import json
import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
import httpx


class ProxyMCPManager:
    """Manages proxy sessions for agents lacking native MCP endpoints."""

    def __init__(self):
        self.proxies: Dict[str, Dict[str, Any]] = {}
        self.sessions: Dict[str, asyncio.Queue] = {}

    def get_base_app_url(self, request: Optional[Any] = None) -> str:
        if request:
            try:
                proto = request.headers.get("x-forwarded-proto") or getattr(request.url, "scheme", "http")
                host = request.headers.get("x-forwarded-host") or request.headers.get("host") or getattr(request.url, "netloc", "")
                if host:
                    return f"{proto}://{host}".rstrip("/")
            except Exception:
                pass
        return os.getenv("APP_URL", "http://127.0.0.1:10000").rstrip("/")

    def create_proxy(self, target_url: str, has_mcp: bool = False) -> Dict[str, Any]:
        """Create a new proxy session or return existing active proxy."""
        target_url = target_url.strip().rstrip("/")
        if not target_url.startswith("http://") and not target_url.startswith("https://"):
            target_url = f"https://{target_url}"

        # Reuse existing proxy if present for target URL
        for proxy_id, proxy in self.proxies.items():
            if proxy["target_url"] == target_url:
                proxy["last_used"] = datetime.now(timezone.utc).isoformat()
                return proxy

        proxy_id = str(uuid.uuid4())[:8]
        app_url = self.get_base_app_url()
        proxy_url = f"{app_url}/proxy/{proxy_id}/mcp"

        now_str = datetime.now(timezone.utc).isoformat()
        proxy_data = {
            "proxy_id": proxy_id,
            "proxy_url": proxy_url,
            "target_url": target_url,
            "has_mcp": has_mcp,
            "created_at": now_str,
            "last_used": now_str,
            "status": "active"
        }
        self.proxies[proxy_id] = proxy_data
        return proxy_data

    def get_proxy(self, proxy_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve proxy configuration by proxy ID, or None if it doesn't exist."""
        proxy = self.proxies.get(proxy_id)
        if proxy:
            proxy["last_used"] = datetime.now(timezone.utc).isoformat()
        return proxy


    def list_proxies(self) -> List[Dict[str, Any]]:
        """Return list of all active proxies."""
        return list(self.proxies.values())

    async def forward_request(self, proxy_id: str, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """Forward request to target or process MCP wrapper tool calls."""
        proxy = self.get_proxy(proxy_id)
        if not proxy:
            return {
                "jsonrpc": "2.0",
                "id": request_data.get("id"),
                "error": {
                    "code": -32600,
                    "message": f"Proxy ID '{proxy_id}' not found or inactive."
                }
            }

        target_url = proxy["target_url"]
        has_mcp = proxy.get("has_mcp", False)

        # If target has native /mcp, attempt forwarding direct MCP JSON-RPC call
        if has_mcp:
            target_mcp_url = f"{target_url}/mcp"
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.post(
                        target_mcp_url,
                        json=request_data,
                        headers={"Content-Type": "application/json"}
                    )
                    content_type = resp.headers.get("content-type", "")
                    if resp.status_code < 400 and "text/html" not in content_type:
                        try:
                            return resp.json()
                        except Exception:
                            pass
            except Exception as e:
                print(f"[ProxyMCP] Forwarding to native target /mcp failed: {e}")

        # Otherwise, serve as MCPify REST wrapper server
        return await self.handle_jsonrpc_request(proxy, request_data)

    async def handle_jsonrpc_request(self, proxy: Dict[str, Any], req: Dict[str, Any]) -> Dict[str, Any]:
        """Process JSON-RPC 2.0 requests for REST API wrapper tools."""
        method = req.get("method")
        req_id = req.get("id")
        params = req.get("params", {})
        target_url = proxy["target_url"]

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {}
                    },
                    "serverInfo": {
                        "name": "MCPify-Proxy",
                        "version": "1.0.0"
                    }
                }
            }

        elif method == "notifications/initialized":
            return {}

        elif method == "ping":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {}
            }

        elif method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": [
                        {
                            "name": "call_api",
                            "description": "Forward HTTP requests to the target agent API endpoints.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "endpoint": {
                                        "type": "string",
                                        "description": "Endpoint path (e.g. /health, /api/v1/query)"
                                    },
                                    "method": {
                                        "type": "string",
                                        "enum": ["GET", "POST", "PUT", "DELETE"],
                                        "default": "GET",
                                        "description": "HTTP method to execute"
                                    },
                                    "params": {
                                        "type": "object",
                                        "description": "Query parameters dictionary"
                                    },
                                    "json_data": {
                                        "type": "object",
                                        "description": "JSON request body dictionary"
                                    }
                                },
                                "required": ["endpoint"]
                            }
                        },
                        {
                            "name": "get_info",
                            "description": "Get basic proxy configuration and target agent metadata.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {}
                            }
                        },
                        {
                            "name": "health_check",
                            "description": "Ping the target agent health endpoint to check availability.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {}
                            }
                        }
                    ]
                }
            }

        elif method == "tools/call":
            tool_name = params.get("name")
            args = params.get("arguments", {})

            if tool_name == "call_api":
                endpoint = args.get("endpoint", "")
                http_method = args.get("method", "GET").upper()
                query_params = args.get("params")
                json_payload = args.get("json_data")

                if not endpoint.startswith("/"):
                    endpoint = f"/{endpoint}"
                full_target_url = f"{target_url}{endpoint}"

                try:
                    async with httpx.AsyncClient(timeout=12.0) as client:
                        resp = await client.request(
                            method=http_method,
                            url=full_target_url,
                            params=query_params,
                            json=json_payload
                        )
                        content_type = resp.headers.get("content-type", "")
                        if "json" in content_type:
                            try:
                                body_res = resp.json()
                            except Exception:
                                body_res = resp.text
                        else:
                            body_res = resp.text

                        output = {
                            "status_code": resp.status_code,
                            "url": str(resp.url),
                            "response": body_res
                        }
                        return {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "result": {
                                "content": [
                                    {
                                        "type": "text",
                                        "text": json.dumps(output, indent=2)
                                    }
                                ]
                            }
                        }
                except Exception as e:
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": f"Error connecting to target endpoint {full_target_url}: {str(e)}"
                                }
                            ],
                            "isError": True
                        }
                    }

            elif tool_name == "get_info":
                info = {
                    "proxy_id": proxy["proxy_id"],
                    "target_url": target_url,
                    "proxy_url": proxy["proxy_url"],
                    "has_mcp": proxy.get("has_mcp", False),
                    "created_at": proxy.get("created_at"),
                    "last_used": proxy.get("last_used"),
                    "status": proxy.get("status", "active")
                }
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(info, indent=2)
                            }
                        ]
                    }
                }

            elif tool_name == "health_check":
                try:
                    health_url = f"{target_url}/health"
                    async with httpx.AsyncClient(timeout=8.0) as client:
                        resp = await client.get(health_url, follow_redirects=True)
                        res = {
                            "target_url": target_url,
                            "health_url": health_url,
                            "status_code": resp.status_code,
                            "reachable": resp.status_code < 400
                        }
                except Exception as e:
                    res = {
                        "target_url": target_url,
                        "reachable": False,
                        "error": str(e)
                    }

                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(res, indent=2)
                            }
                        ]
                    }
                }

            else:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32601,
                        "message": f"Unknown tool: '{tool_name}'"
                    }
                }

        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32601,
                    "message": f"Method '{method}' not supported by MCPify proxy."
                }
            }


proxy_manager = ProxyMCPManager()
