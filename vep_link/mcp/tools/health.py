"""check_upstream_health: live Ensembl REST readiness per assembly.

Triggers a fresh ``/info/ping`` probe of both hosts (GRCh38 and GRCh37) and
returns the circuit-breaker snapshot, so a client can decide -- before issuing a
batch -- whether a build is healthy or should be avoided / swapped. This is the
explicit, user-initiated counterpart to the always-on ``_meta.upstream`` hint.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastmcp import FastMCP

from vep_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from vep_link.mcp.errors import McpErrorContext, run_mcp_tool
from vep_link.mcp.resources import build_meta
from vep_link.mcp.tools._common import new_request_id


def register_health_tools(
    mcp: FastMCP,
    *,
    service_factory: Callable[[], Any],
    health_factory: Callable[[], Any] | None = None,
) -> None:
    """Register ``check_upstream_health`` on ``mcp``."""

    @mcp.tool(
        name="check_upstream_health",
        title="Check Ensembl Upstream Health",
        annotations=READ_ONLY_OPEN_WORLD,
        tags={"ops", "diagnostics", "health"},
        output_schema=None,  # Tool-Surface Budget v1: suppress optional outputSchema
    )
    async def check_upstream_health() -> dict[str, Any]:
        """Use this to check whether the Ensembl REST hosts are healthy before a batch, or when calls start failing. Runs a live /info/ping of both assemblies (GRCh38 rest.ensembl.org, GRCh37 grch37.rest.ensembl.org) and returns each host's status (ok | recovering | down), circuit state, reachability, latency, and last error. If one build is degraded, route to the healthy one or back off. Returns <1kB."""

        health = health_factory() if health_factory else None

        async def call() -> dict[str, Any]:
            if health is None:
                return {
                    "upstream": {},
                    "note": "Upstream health monitoring is not enabled in this transport.",
                    "_meta": build_meta(tool="check_upstream_health", request_id=new_request_id()),
                }
            await health.refresh()
            return {
                "upstream": health.snapshot(),
                "_meta": build_meta(tool="check_upstream_health", request_id=new_request_id()),
            }

        return await run_mcp_tool(
            "check_upstream_health",
            call,
            McpErrorContext(tool_name="check_upstream_health", health=health),
        )
