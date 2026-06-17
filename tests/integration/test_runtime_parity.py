"""Runtime-parity guard: the DEPLOYED MCP server must match committed source.

Black-box testing of a *stale* deploy produced misleading symptoms (CADD per
transcript, ``elapsed_ms: 0``) that looked like source bugs but were just an
old running instance. This test hits the **live HTTP MCP server** (not the
in-process facade), so only a real redeploy makes it pass -- a stale deploy can
never again silently mask shipped improvements.

Excluded from default CI: ``integration`` + ``allow_network``, skipped unless
``VEP_LINK_RUN_INTEGRATION`` is set. Target the server with ``VEP_LINK_MCP_URL``
(default ``http://127.0.0.1:8021/mcp`` -- the docker-compose host port).
"""

from __future__ import annotations

import os
from typing import Any

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.allow_network,
    pytest.mark.skipif(
        not os.getenv("VEP_LINK_RUN_INTEGRATION"),
        reason="set VEP_LINK_RUN_INTEGRATION=1 to run live deploy-parity tests",
    ),
]

_MCP_URL = os.getenv("VEP_LINK_MCP_URL", "http://127.0.0.1:8021/mcp")


def _structured(result: Any) -> dict[str, Any]:
    """Pull the structured dict out of a fastmcp call_tool result."""
    sc = getattr(result, "structured_content", None)
    if sc is None:
        sc = getattr(result, "data", None)
    return sc or {}


async def test_live_annotate_reflects_committed_shape() -> None:
    from fastmcp import Client

    async with Client(_MCP_URL) as client:
        result = await client.call_tool(
            "annotate_variant",
            {
                "variant": "NM_033380.3:c.1871G>A",  # COL4A5 missense (CADD/REVEL populate)
                "assembly": "GRCh38",
                "response_mode": "standard",
            },
        )
    data = _structured(result)

    # v0.2 contract: the variants[] / warnings[] envelope is live.
    assert data.get("variants"), "expected a non-empty variants[] (stale pre-0.2 deploy?)"
    assert isinstance(data.get("warnings"), list)
    first = data["variants"][0]

    # v0.1 token-efficiency: CADD/GERP hoisted ONCE to position_scores, never per row.
    assert first.get("position_scores"), (
        "position_scores missing -> stale pre-token-efficiency deploy"
    )
    for tc in first.get("transcript_consequences", []):
        assert "cadd_phred" not in tc, "CADD duplicated per transcript -> stale deploy"
        assert "conservation" not in tc, "GERP duplicated per transcript -> stale deploy"

    # v0.2 observability: elapsed_ms is a real measurement, not a 0 stub.
    assert data["_meta"]["timing"]["elapsed_ms"] > 0, (
        "elapsed_ms == 0 -> stale pre-observability deploy"
    )

    # v0.3 observability: _meta.timing carries upstream_ms + a three-state
    # cache_status. Guards against a stale deploy masking the new contract.
    timing = data["_meta"]["timing"]
    assert "upstream_ms" in timing, "upstream_ms missing -> stale pre-0.3 deploy"
    assert timing["cache_status"] in {"miss", "hit", "coalesced"}, (
        "cache_status missing/invalid -> stale pre-0.3 deploy"
    )


async def test_live_meta_timing_has_v03_keys() -> None:
    # A dedicated, focused guard for the v0.3 _meta.timing contract (separate from
    # the broader shape check above so a regression points straight at telemetry).
    from fastmcp import Client

    async with Client(_MCP_URL) as client:
        result = await client.call_tool(
            "annotate_variant",
            {"variant": "NM_033380.3:c.1871G>A", "assembly": "GRCh38"},
        )
    timing = _structured(result)["_meta"]["timing"]
    assert "upstream_ms" in timing
    assert timing["cache_status"] in {"miss", "hit", "coalesced"}
