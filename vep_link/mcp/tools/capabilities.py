"""get_capabilities: the always-readable discovery tool (no service call).

Returns the static :func:`~vep_link.mcp.resources.server_capabilities` document
(assemblies, input formats, VEP-option allowlist, response-mode tiers, error
codes, citation contract, and the ``capabilities_version`` hash) plus the
canonical ``_meta`` block. It makes no upstream call and never fails, so a
confused client can always fall back to it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastmcp import FastMCP

from vep_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from vep_link.mcp.resources import build_meta, server_capabilities
from vep_link.mcp.tools._common import new_request_id


def register_capabilities_tools(
    mcp: FastMCP,
    *,
    service_factory: Callable[[], Any],
    health_factory: Callable[[], Any] | None = None,
) -> None:
    """Register ``get_capabilities`` on ``mcp`` (``service_factory`` unused here)."""

    @mcp.tool(
        name="get_capabilities",
        title="Get Server Capabilities",
        annotations=READ_ONLY_OPEN_WORLD,
        tags={"discovery"},
        output_schema=None,  # Tool-Surface Budget v1: suppress optional outputSchema
    )
    async def get_capabilities() -> dict[str, Any]:
        """Read this first in a cold session. Returns server/tool metadata: supported assemblies (GRCh38 default, GRCh37), input formats (coordinate, rsID, HGVS, SPDI, CNV), the VEP-option allowlist, the four response_mode tiers, the deterministic error codes, the citation contract, a capabilities_version hash a warm client can compare to skip re-fetching, and a live `upstream` health summary (per-assembly Ensembl REST status from the circuit breaker). No upstream call; never fails."""
        doc: dict[str, Any] = {
            **server_capabilities(),
            "_meta": build_meta(tool="get_capabilities", request_id=new_request_id()),
        }
        # Live per-assembly upstream health, kept OUTSIDE the hashed capabilities
        # document so the capabilities_version stays stable across status changes.
        if health_factory is not None:
            doc["upstream"] = health_factory().snapshot()
        return doc
