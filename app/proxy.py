import os
import uuid
import json
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
import httpx

from app.security import normalize_url

logger = logging.getLogger("mcpify")

REDIS_KEY_PREFIX = "mcpify:proxy:"
REDIS_INDEX_PREFIX = "mcpify:proxy_by_url:"


class ProxyMCPManager:
    """Manages proxy sessions for agents lacking native MCP endpoints.

    Proxy records persist to Redis (Upstash) when UPSTASH_REDIS_URL is set,
    so they survive redeploys/restarts instead of vanishing with every
    process restart. Falls back to an in-memory dict when no Redis URL is
    configured, so local dev and the test suite need no Redis at all.
    SSE sessions (self.sessions) are always in-memory - an asyncio.Queue
    is tied to one open connection in one process and can't be persisted
    meaningfully; a redeploy naturally drops open SSE connections anyway.
    """

    def __init__(self):
        self.redis_url = os.getenv("UPSTASH_REDIS_URL") or os.getenv("REDIS_URL")
        self._redis = None
        self.proxies: Dict[str, Dict[str, Any]] = {}
        self.sessions: Dict[str, asyncio.Queue] = {}

    def _get_redis(self):
        if not self.redis_url:
            return None
        if self._redis is None:
            import redis.asyncio as redis_asyncio
            self._redis = redis_asyncio.from_url(self.redis_url, decode_responses=True)
        return self._redis

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

    async def create_proxy(self, target_url: str, has_mcp: bool = False, api_key: Optional[str] = None) -> Dict[str, Any]:
        """Create a new proxy session or return existing active proxy."""
        target_url = normalize_url(target_url)

        redis = self._get_redis()
        now_str = datetime.now(timezone.utc).isoformat()

        if redis is not None:
            existing_id = await redis.get(f"{REDIS_INDEX_PREFIX}{target_url}")
            if existing_id:
                existing = await self._redis_get_proxy(redis, existing_id)
                if existing:
                    existing["last_used"] = now_str
                    if api_key:
                        existing["api_key"] = api_key
                    await self._redis_save_proxy(redis, existing)
                    return existing

            proxy_id = str(uuid.uuid4())[:8]
            proxy_url = f"{self.get_base_app_url()}/proxy/{proxy_id}/mcp"
            proxy_data = {
                "proxy_id": proxy_id,
                "proxy_url": proxy_url,
                "target_url": target_url,
                "has_mcp": has_mcp,
                "api_key": api_key,
                "created_at": now_str,
                "last_used": now_str,
                "status": "active"
            }
            await self._redis_save_proxy(redis, proxy_data)
            await redis.set(f"{REDIS_INDEX_PREFIX}{target_url}", proxy_id)
            return proxy_data

        # In-memory fallback
        for proxy_id, proxy in self.proxies.items():
            if proxy["target_url"] == target_url:
                proxy["last_used"] = now_str
                if api_key:
                    proxy["api_key"] = api_key
                return proxy

        proxy_id = str(uuid.uuid4())[:8]
        proxy_url = f"{self.get_base_app_url()}/proxy/{proxy_id}/mcp"
        proxy_data = {
            "proxy_id": proxy_id,
            "proxy_url": proxy_url,
            "target_url": target_url,
            "has_mcp": has_mcp,
            "api_key": api_key,
            "created_at": now_str,
            "last_used": now_str,
            "status": "active"
        }
        self.proxies[proxy_id] = proxy_data
        return proxy_data

    async def _redis_get_proxy(self, redis, proxy_id: str) -> Optional[Dict[str, Any]]:
        raw = await redis.get(f"{REDIS_KEY_PREFIX}{proxy_id}")
        return json.loads(raw) if raw else None

    async def _redis_save_proxy(self, redis, proxy: Dict[str, Any]) -> None:
        await redis.set(f"{REDIS_KEY_PREFIX}{proxy['proxy_id']}", json.dumps(proxy))

    async def get_proxy(self, proxy_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve proxy configuration by proxy ID, or None if it doesn't exist."""
        redis = self._get_redis()
        now_str = datetime.now(timezone.utc).isoformat()

        if redis is not None:
            proxy = await self._redis_get_proxy(redis, proxy_id)
            if proxy:
                proxy["last_used"] = now_str
                await self._redis_save_proxy(redis, proxy)
            return proxy

        proxy = self.proxies.get(proxy_id)
        if proxy:
            proxy["last_used"] = now_str
        return proxy

    async def list_proxies(self) -> List[Dict[str, Any]]:
        """Return list of all active proxies, with credentials masked."""
        redis = self._get_redis()
        proxies: List[Dict[str, Any]] = []

        if redis is not None:
            keys = [k async for k in redis.scan_iter(match=f"{REDIS_KEY_PREFIX}*")]
            if keys:
                raw_values = await redis.mget(keys)
                proxies = [json.loads(v) for v in raw_values if v]
        else:
            proxies = list(self.proxies.values())

        result = []
        for proxy in proxies:
            sanitized = {k: v for k, v in proxy.items() if k != "api_key"}
            sanitized["has_api_key"] = bool(proxy.get("api_key"))
            result.append(sanitized)
        return result

    async def record_ping_status(self, proxy_id: str, status_code: Optional[int]) -> None:
        """Record the last health-ping status against the live proxy record.

        list_proxies() returns sanitized copies (to keep api_key out of
        responses), so callers must write ping results back through this
        method rather than mutating list_proxies()'s output directly.
        """
        redis = self._get_redis()
        if redis is not None:
            proxy = await self._redis_get_proxy(redis, proxy_id)
            if proxy:
                proxy["last_ping_status"] = status_code
                await self._redis_save_proxy(redis, proxy)
            return

        proxy = self.proxies.get(proxy_id)
        if proxy:
            proxy["last_ping_status"] = status_code

    async def forward_request(self, proxy_id: str, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """Forward request to target or process MCP wrapper tool calls."""
        proxy = await self.get_proxy(proxy_id)
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
            headers = {"Content-Type": "application/json"}
            if proxy.get("api_key"):
                headers["Authorization"] = f"Bearer {proxy['api_key']}"
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.post(
                        target_mcp_url,
                        json=request_data,
                        headers=headers
                    )
                    content_type = resp.headers.get("content-type", "")
                    if resp.status_code < 400 and "text/html" not in content_type:
                        try:
                            return resp.json()
                        except Exception:
                            pass
            except Exception as e:
                logger.warning("[ProxyMCP] Forwarding to native target /mcp failed: %s", e)

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

                headers = {}
                if proxy.get("api_key"):
                    headers["Authorization"] = f"Bearer {proxy['api_key']}"

                try:
                    async with httpx.AsyncClient(timeout=12.0) as client:
                        resp = await client.request(
                            method=http_method,
                            url=full_target_url,
                            params=query_params,
                            json=json_payload,
                            headers=headers
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
                    "has_api_key": bool(proxy.get("api_key")),
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
                        # No follow_redirects: target_url was only SSRF-checked
                        # once, at proxy-creation time - a redirect here could
                        # otherwise point anywhere without being re-validated.
                        resp = await client.get(health_url)
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
