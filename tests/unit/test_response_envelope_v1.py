"""Locks vep-link's MCP-wrapper response envelope to the ratified GeneFoundry
Response-Envelope Standard v1 (flat banner; ``genefoundry-router``'s
``docs/RESPONSE-ENVELOPE-STANDARD-v1.md``) at the ``run_mcp_tool`` boundary.

Contract asserted here (ratified, OQ4 resolved 2026-06-30 -> flat banner):

* **Success**: the tool body's dict is returned with a top-level
  ``success: true`` injected; its own payload keys and ``_meta`` are otherwise
  passed through unchanged (no forced ``results``/``result`` rename -- that is
  the OQ1 payload-key question, not in scope here).
* **Failure**: a FLAT in-band frame -- ``{"success": false, "error_code",
  "message", "retryable", "recovery_action", "_meta": {...}}`` -- never a
  nested ``{"error": {...}}`` block. The frame additionally rides the wire as
  MCP-native ``CallToolResult.isError = true`` (v1 §2 REQUIRED), surfaced here
  via :class:`fastmcp.tools.ToolResult` (``is_error=True``,
  ``structured_content=<frame>``); verified against the installed
  ``fastmcp==3.4.2``.
* ``_meta.unsafe_for_clinical_use`` is ``True`` on every response, success or
  error, per-call (not a one-time capabilities flag).

Supersedes the prior locking test (PR #8, closed) that pinned the pre-v1
nested-error / no-``success``-key shape vep-link shipped before this
migration.
"""

from __future__ import annotations

from typing import Any

from fastmcp.tools import ToolResult

from vep_link.exceptions import DataNotFoundError
from vep_link.mcp.errors import McpErrorContext, run_mcp_tool

_TOOL = "resolve_variant"


async def test_success_envelope_has_top_level_success_true() -> None:
    """A successful body dict gets ``success: true`` injected at the top level."""

    async def call() -> dict[str, Any]:
        return {"variants": [{"variant_id": "1-100-A-T"}], "_meta": {"tool": _TOOL}}

    result = await run_mcp_tool(_TOOL, call, McpErrorContext(tool_name=_TOOL))
    assert isinstance(result, dict)
    assert result["success"] is True
    # The body's own payload key (a domain-specific alias, not "results") is
    # passed through unchanged -- the wrapper only injects success + _meta.
    assert result["variants"] == [{"variant_id": "1-100-A-T"}]


async def test_single_result_payload_key_is_preserved_on_success() -> None:
    """Whatever payload shape a tool body returns is passed through unchanged
    by the wrapper -- it only adds ``success`` and stamps ``_meta``."""

    async def call() -> dict[str, Any]:
        return {"result": {"id": "x"}, "_meta": {"tool": _TOOL}}

    result = await run_mcp_tool(_TOOL, call, McpErrorContext(tool_name=_TOOL))
    assert isinstance(result, dict)
    assert result["success"] is True
    assert result["result"] == {"id": "x"}


async def test_success_meta_carries_unsafe_for_clinical_use_per_call() -> None:
    async def call() -> dict[str, Any]:
        return {"result": {"id": "x"}, "_meta": {"tool": _TOOL}}

    result = await run_mcp_tool(_TOOL, call, McpErrorContext(tool_name=_TOOL))
    assert isinstance(result, dict)
    assert result["_meta"]["unsafe_for_clinical_use"] is True


async def test_error_result_is_a_tool_result_with_is_error_true() -> None:
    """The v1 standard REQUIRES MCP-native ``isError: true`` on execution errors
    (§2), in addition to the in-band flat frame. vep-link surfaces this by
    returning a :class:`fastmcp.tools.ToolResult` (verified against the
    installed fastmcp==3.4.2) rather than a bare dict on the error path."""

    async def call() -> dict[str, Any]:
        raise DataNotFoundError("not found")

    result = await run_mcp_tool(_TOOL, call, McpErrorContext(tool_name=_TOOL))
    assert isinstance(result, ToolResult)
    assert result.is_error is True


async def test_error_envelope_is_flat_not_nested() -> None:
    """DRIFT-FREE vs. the ratified flat-banner v1 standard: the structured
    error frame carried on the ``ToolResult`` is FLAT -- ``success``,
    ``error_code``, ``message``, ``retryable``, ``recovery_action`` all sit at
    the top level -- never nested under an ``error: {...}`` block."""

    async def call() -> dict[str, Any]:
        raise DataNotFoundError("not found")

    result = await run_mcp_tool(_TOOL, call, McpErrorContext(tool_name=_TOOL))
    assert isinstance(result, ToolResult)
    envelope = result.structured_content
    assert envelope is not None

    assert "error" not in envelope

    assert envelope["success"] is False
    assert envelope["error_code"] == "not_found"
    assert isinstance(envelope["message"], str) and envelope["message"]
    assert envelope["retryable"] is False
    assert isinstance(envelope["recovery_action"], str) and envelope["recovery_action"]

    assert envelope["_meta"]["tool"] == _TOOL
    assert envelope["_meta"]["unsafe_for_clinical_use"] is True


async def test_error_recovery_action_is_retry_backoff_for_retryable_codes() -> None:
    """``recovery_action`` is a closed enum a client branches on, not just the
    bare ``retryable`` bool: retryable upstream faults get ``retry_backoff``."""
    from vep_link.exceptions import EnsemblApiError

    async def call() -> dict[str, Any]:
        raise EnsemblApiError("503")

    result = await run_mcp_tool(_TOOL, call, McpErrorContext(tool_name=_TOOL))
    assert isinstance(result, ToolResult)
    envelope = result.structured_content
    assert envelope is not None
    assert envelope["retryable"] is True
    assert envelope["recovery_action"] == "retry_backoff"
