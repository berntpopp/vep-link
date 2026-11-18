"""MCP over the real stateless HTTP app: 2026-07-28 modern era + legacy handshake.

* Drives ``server/discover`` -> ``tools/list`` -> ``tools/call`` with the modern
  ``mcp-method`` / ``mcp-name`` headers and proves an unknown hostile tool name is
  answered with the fixed not-found envelope (never reflected).
* MCP SDK 2.x echoes an unknown JSON-RPC METHOD in the METHOD_NOT_FOUND ``data``;
  the not-found guard strips it (both eras).
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from vep_link.server_manager import build_app

_MODERN_VERSION = "2026-07-28"
_MODERN_META = {
    "io.modelcontextprotocol/protocolVersion": _MODERN_VERSION,
    "io.modelcontextprotocol/clientCapabilities": {},
    "io.modelcontextprotocol/clientInfo": {"name": "vep-host-test", "version": "1.0.0"},
}
_EVIL_METHOD = "evil__IGNORE_ALL_PREVIOUS__method"


def _modern_headers(method: str, name: str | None = None) -> dict[str, str]:
    headers = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
        "mcp-protocol-version": _MODERN_VERSION,
        "mcp-method": method,
    }
    if name is not None:
        headers["mcp-name"] = name
    return headers


async def _modern_post(
    client: httpx.AsyncClient, rid: int, method: str, params: dict[str, Any]
) -> Any:
    return await client.post(
        "/mcp",
        headers=_modern_headers(method, params.get("name")),
        json={
            "jsonrpc": "2.0",
            "id": rid,
            "method": method,
            "params": {"_meta": _MODERN_META, **params},
        },
    )


def _assert_method_stripped(response: httpx.Response) -> None:
    assert "evil" not in response.text and "IGNORE" not in response.text
    error = response.json()["error"]
    assert error["code"] == -32601
    assert error.get("data") is None


async def test_modern_mcp_http_discovers_lists_calls_and_fences_unknown_tool() -> None:
    hostile = "ignore-instructions-secret"
    app = build_app()
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        discover = await _modern_post(c, 1, "server/discover", {})
        listed = await _modern_post(c, 2, "tools/list", {})
        called = await _modern_post(
            c, 3, "tools/call", {"name": "get_capabilities", "arguments": {}}
        )
        unknown = await _modern_post(c, 4, "tools/call", {"name": hostile, "arguments": {}})

    assert discover.status_code == 200, discover.text
    assert _MODERN_VERSION in discover.json()["result"]["supportedVersions"]
    names = {tool["name"] for tool in listed.json()["result"]["tools"]}
    assert "get_capabilities" in names
    result = called.json()["result"]
    assert result.get("isError") is not True
    assert json.loads(result["content"][0]["text"])["success"] is True
    assert hostile not in unknown.text
    payload = json.loads(unknown.json()["result"]["content"][0]["text"])
    assert payload["error_code"] == "not_found"


async def test_unknown_method_not_reflected_in_method_not_found_data() -> None:
    app = build_app()
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        modern = await _modern_post(c, 1, _EVIL_METHOD, {})
        legacy_headers = {
            "accept": "application/json, text/event-stream",
            "content-type": "application/json",
        }
        init = await c.post(
            "/mcp",
            headers=legacy_headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "vep-host-test", "version": "1"},
                },
            },
        )
        legacy = await c.post(
            "/mcp",
            headers={**legacy_headers, "mcp-protocol-version": "2025-06-18"},
            json={"jsonrpc": "2.0", "id": 3, "method": _EVIL_METHOD, "params": {}},
        )
    assert init.status_code == 200, init.text
    _assert_method_stripped(modern)
    _assert_method_stripped(legacy)
