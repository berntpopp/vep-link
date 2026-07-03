"""Locks vep-link's ACTUAL MCP-wrapper response-envelope shape at the boundary.

Context: the fleet-wide GeneFoundry Response-Envelope Standard v1
(genefoundry-router's ``docs/RESPONSE-ENVELOPE-STANDARD-v1.md``) specifies a
FLAT banner on success -- ``{"success": True, "results"|"result": ...,
"_meta": {..., "unsafe_for_clinical_use": True}}`` -- and a FLAT in-band dict
on failure -- ``{"success": False, "error_code", "message", "retryable",
"recovery_action", "_meta": {"tool": ..., "unsafe_for_clinical_use": True}}``
-- never a nested ``"error": {}`` block.

vep-link's ``vep_link.mcp.errors.run_mcp_tool`` wrapper (this backend's sole
MCP error boundary, exercised by every ``@mcp.tool`` body, e.g.
``resolve_variant``) predates that standard and has NOT adopted its shape:

* On success there is NO top-level ``success`` key at all -- the wrapper
  returns the tool body's dict UNCHANGED (see ``run_mcp_tool``'s docstring:
  "the body's dict is returned UNCHANGED").
* On failure the wrapper returns a NESTED ``{"error": {"code", "message",
  "recovery", "fallback_tool", "next_commands", "retryable"[,
  "retry_after_s"]}, "_meta": {...}}`` shape, not the flat
  ``error_code``/``message``/``retryable``/``recovery_action`` keys the
  ratified standard mandates.

This is a LOCKING test only (no behavior change): it pins the envelope this
backend actually ships today, adapted from the clingen-link fleet-reference
pattern (PR #20) but asserting vep-link's real (pre-v1) shape rather than the
ratified one. Migrating this wrapper to the flat-banner v1 shape is fleet
conformance work tracked separately (see AGENTS.md's Response-Envelope
Standard v1 fleet standard) -- it is intentionally NOT done here.
"""

from __future__ import annotations

from typing import Any

from vep_link.exceptions import DataNotFoundError
from vep_link.mcp.errors import McpErrorContext, run_mcp_tool

_TOOL = "resolve_variant"


async def test_success_envelope_has_no_top_level_success_key() -> None:
    """The wrapper passes the body's dict through unchanged on success: no
    ``success: true`` banner key is added anywhere in the vep-link wrapper
    chain (this is drift vs. the ratified flat-banner v1 contract)."""

    async def call() -> dict[str, Any]:
        return {"variants": [{"variant_id": "1-100-A-T"}], "_meta": {"tool": _TOOL}}

    result = await run_mcp_tool(_TOOL, call, McpErrorContext(tool_name=_TOOL))
    assert "success" not in result
    assert result["variants"] == [{"variant_id": "1-100-A-T"}]


async def test_single_result_payload_key_is_preserved_on_success() -> None:
    """Whatever payload shape a tool body returns is passed through unchanged
    by the wrapper -- it never rewrites or wraps the body's keys."""

    async def call() -> dict[str, Any]:
        return {"result": {"id": "x"}, "_meta": {"tool": _TOOL}}

    result = await run_mcp_tool(_TOOL, call, McpErrorContext(tool_name=_TOOL))
    assert result["result"] == {"id": "x"}


async def test_error_envelope_is_nested_not_a_flat_banner() -> None:
    """DRIFT vs. the ratified flat-banner v1 standard: vep-link's error path
    returns a NESTED ``{"error": {...}}`` block, not a flat ``{"success":
    False, "error_code", "message", "retryable", "recovery_action"}`` dict.
    This pins the shape as it exists today rather than the ratified one."""

    async def call() -> dict[str, Any]:
        raise DataNotFoundError("not found")

    result = await run_mcp_tool(_TOOL, call, McpErrorContext(tool_name=_TOOL))

    # Flat-banner keys the ratified standard mandates are ABSENT today.
    assert "success" not in result
    assert "error_code" not in result
    assert "recovery_action" not in result

    # What vep-link ACTUALLY ships: a nested "error" block.
    assert "error" in result
    error = result["error"]
    assert isinstance(error["code"], str) and error["code"]
    assert isinstance(error["message"], str) and error["message"]
    assert isinstance(error["retryable"], bool)
    assert isinstance(error["recovery"], str) and error["recovery"]

    assert result["_meta"]["tool"] == _TOOL
    assert result["_meta"]["unsafe_for_clinical_use"] is True
