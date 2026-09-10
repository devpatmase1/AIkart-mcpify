"""
Give this script any agent URL and it does the whole test flow itself:
create/reuse a proxy on MCPify, list the tools that come back, and call
one of them - no need to open Claude Desktop or VS Code just to check
whether a link actually works.

Usage:
    python scripts/test_agent.py <url> [--api-key KEY] [--tool NAME] [--args '{"k":"v"}'] [--base BASE_URL]

Examples:
    python scripts/test_agent.py https://catfact.ninja
    python scripts/test_agent.py https://jsonplaceholder.typicode.com
    python scripts/test_agent.py https://my-protected-agent.com --api-key sk-xxxx
    python scripts/test_agent.py https://catfact.ninja --tool random-cat-fact --args '{"max_length": 40}'
"""
import argparse
import json
import sys

import httpx

DEFAULT_BASE = "https://aikart-mcpify.onrender.com"


def rpc(client: httpx.Client, url: str, method: str, params: dict, req_id: int) -> dict:
    resp = client.post(
        url,
        json={"jsonrpc": "2.0", "id": req_id, "method": method, "params": params},
        headers={"Accept": "application/json, text/event-stream"},
        timeout=20.0,
    )
    resp.raise_for_status()
    return resp.json()


def pick_auto_test_tool(tools: list[dict]) -> dict | None:
    """
    Picks a tool that's safe to call with no arguments, so the whole flow
    can run unattended: prefer the proxy's own read-only get_info (no
    network side effect on the target at all), otherwise the first tool
    whose schema has no required fields.
    """
    by_name = {t["name"]: t for t in tools}
    if "get_info" in by_name:
        return by_name["get_info"]
    for tool in tools:
        required = tool.get("inputSchema", {}).get("required", [])
        if not required:
            return tool
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a proxy for an agent URL and test-call one of its tools.")
    parser.add_argument("url", help="The agent/API URL to turn into an MCP link and test.")
    parser.add_argument("--api-key", default=None, help="Bearer token to forward to the target, if it needs auth.")
    parser.add_argument("--tool", default=None, help="Force a specific tool name instead of auto-picking one.")
    parser.add_argument("--args", default="{}", help="JSON object of arguments for --tool (default: {}).")
    parser.add_argument("--base", default=DEFAULT_BASE, help="MCPify instance to use (default: production).")
    args = parser.parse_args()

    try:
        tool_args = json.loads(args.args)
    except json.JSONDecodeError as e:
        print(f"--args is not valid JSON: {e}")
        return 1

    with httpx.Client() as client:
        print(f"-> Creating/reusing proxy for {args.url} ...")
        payload = {"url": args.url}
        if args.api_key:
            payload["api_key"] = args.api_key
        resp = client.post(f"{args.base}/proxy/create", json=payload, timeout=20.0)
        if resp.status_code != 200:
            print(f"FAILED to create proxy ({resp.status_code}): {resp.text[:500]}")
            return 1

        proxy_url = resp.json()["proxy_url"]
        print(f"   proxy_url: {proxy_url}\n")

        print("-> Listing available tools ...")
        tools_resp = rpc(client, proxy_url, "tools/list", {}, req_id=1)
        if "error" in tools_resp:
            print(f"FAILED: {tools_resp['error']}")
            return 1

        tools = tools_resp["result"]["tools"]
        for t in tools:
            required = t.get("inputSchema", {}).get("required", [])
            req_note = f" (requires: {', '.join(required)})" if required else ""
            print(f"   - {t['name']}: {t.get('description', '')}{req_note}")
        print()

        if args.tool:
            target_tool = next((t for t in tools if t["name"] == args.tool), None)
            if not target_tool:
                print(f"Tool '{args.tool}' not found on this agent.")
                return 1
        else:
            target_tool = pick_auto_test_tool(tools)
            if not target_tool:
                print("No zero-argument tool available to auto-test. Re-run with --tool <name> --args '{...}'.")
                return 0
            if tool_args == {}:
                print(f"-> Auto-picked '{target_tool['name']}' (needs no arguments) for the test call.\n")

        print(f"-> Calling '{target_tool['name']}' with arguments: {tool_args}")
        call_resp = rpc(
            client,
            proxy_url,
            "tools/call",
            {"name": target_tool["name"], "arguments": tool_args},
            req_id=2,
        )
        if "error" in call_resp:
            print(f"FAILED: {call_resp['error']}")
            return 1

        content = call_resp["result"].get("content", [])
        print("\n=== Result ===")
        for block in content:
            print(block.get("text", block))

    return 0


if __name__ == "__main__":
    sys.exit(main())
