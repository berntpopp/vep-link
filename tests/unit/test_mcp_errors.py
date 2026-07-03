"""Tests for the MCP error-envelope module (``vep_link.mcp.errors``).

Written test-first. These pin the deterministic contract the LLM branches on,
per the ratified GeneFoundry Response-Envelope Standard v1 (flat banner):

* the exception -> error-code map (with subclass-specificity ordering, so
  ``UnsupportedContigError`` does not collapse into ``invalid_input``),
* the FLAT envelope shape (``success``/``error_code``/``message``/
  ``retryable``/``recovery_action`` at the top level -- never nested under
  ``error: {...}`` -- plus ``_meta`` carrying ``next_commands``,
  ``capabilities_version``, and ``unsafe_for_clinical_use``),
* that a failure additionally sets the MCP-native ``isError`` wire flag via a
  :class:`fastmcp.tools.ToolResult` (``is_error=True``),
* the success passthrough (the body's dict, plus an injected ``success: true``,
  is returned unchanged otherwise), and
* ``internal_error`` sanitization (no leaked exception text; a correlation id
  is surfaced instead).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp.tools import ToolResult

from vep_link.exceptions import (
    AmbiguousMappingError,
    DataNotFoundError,
    EnsemblApiError,
    RateLimitedError,
    UnsupportedContigError,
    UpstreamInputError,
    UpstreamTimeoutError,
    VariantParseError,
    VepLinkError,
)
from vep_link.mcp.errors import (
    ERROR_CODES,
    McpErrorContext,
    _classify,
    install_validation_error_handler,
    mcp_tool_error,
    run_mcp_tool,
)


def _envelope(result: dict[str, Any] | ToolResult) -> dict[str, Any]:
    """Extract the flat structured envelope from a ``run_mcp_tool`` error result.

    Error results are a :class:`~fastmcp.tools.ToolResult` (``is_error=True``);
    this also asserts that invariant so every call site double-checks the wire
    ``isError`` flag, not just the in-band shape.
    """
    assert isinstance(result, ToolResult)
    assert result.is_error is True
    envelope = result.structured_content
    assert envelope is not None
    return envelope


def test_local_validation_recovery_does_not_blame_ensembl() -> None:
    # UpstreamInputError covers LOCALLY-validated failures too (e.g. same-assembly
    # liftover). Its recovery must not claim Ensembl rejected a call it never made.
    classified = _classify(UpstreamInputError("from_assembly and to_assembly must differ"))
    assert classified is not None
    code, recovery = classified
    assert code == "invalid_input"
    assert "Ensembl" not in recovery


def _ctx(**kwargs: Any) -> McpErrorContext:
    kwargs.setdefault("tool_name", "resolve_variant")
    return McpErrorContext(**kwargs)


# ---------------------------------------------------------------------------
# ERROR_CODES enum
# ---------------------------------------------------------------------------


def test_error_codes_has_the_ten_codes() -> None:
    assert ERROR_CODES == (
        "invalid_input",
        "unsupported_input",
        "not_found",
        "build_mismatch",
        "ambiguous",
        "rate_limited",
        "upstream_unavailable",
        "upstream_timeout",
        "output_validation_failed",
        "internal_error",
    )
    assert len(ERROR_CODES) == 10
    assert len(set(ERROR_CODES)) == 10


# ---------------------------------------------------------------------------
# mcp_tool_error shape
# ---------------------------------------------------------------------------


def test_mcp_tool_error_shape() -> None:
    env = mcp_tool_error(
        code="invalid_input",
        message="bad variant",
        recovery="fix it",
        ctx=_ctx(assembly="GRCh38", next_commands=[{"tool": "resolve_variant", "arguments": {}}]),
        request_id="req-123",
    )
    # FLAT: success/error_code/message/retryable/recovery_action all sit at the
    # top level -- never nested under an "error" block.
    assert "error" not in env
    assert env["success"] is False
    assert env["error_code"] == "invalid_input"
    assert env["message"] == "bad variant"
    assert env["recovery"] == "fix it"
    assert env["retryable"] is False
    assert env["recovery_action"] == "reformulate_input"
    assert env["fallback_tool"] == "get_capabilities"

    meta = env["_meta"]
    assert meta["tool"] == "resolve_variant"
    assert meta["request_id"] == "req-123"
    assert meta["assembly"] == "GRCh38"
    assert meta["unsafe_for_clinical_use"] is True
    assert meta["next_commands"] == [{"tool": "resolve_variant", "arguments": {}}]
    assert "capabilities_version" in meta


def test_mcp_tool_error_defaults_next_commands_to_empty_list() -> None:
    env = mcp_tool_error(
        code="not_found",
        message="no data",
        recovery="try resolve_variant",
        ctx=_ctx(),
    )
    # next_commands rides in _meta (not a nested "error" block) and defaults to [].
    assert env["_meta"]["next_commands"] == []


def test_mcp_tool_error_generates_request_id_when_absent() -> None:
    env = mcp_tool_error(
        code="not_found",
        message="no data",
        recovery="try resolve_variant",
        ctx=_ctx(),
    )
    request_id = env["_meta"]["request_id"]
    assert isinstance(request_id, str)
    assert len(request_id) == 12


# ---------------------------------------------------------------------------
# run_mcp_tool: success passthrough
# ---------------------------------------------------------------------------


async def test_run_mcp_tool_injects_success_true_and_preserves_payload_identity() -> None:
    payload = {
        "variant_id": "1-100-A-T",
        "_meta": {"tool": "resolve_variant", "request_id": "abc", "capabilities_version": "x"},
    }

    async def body() -> dict[str, Any]:
        return payload

    result = await run_mcp_tool("resolve_variant", body, _ctx())
    # Same dict object, mutated in place -- the wrapper only injects success + _meta.
    assert result is payload
    assert isinstance(result, dict)
    assert result["success"] is True
    assert "error" not in result
    assert result["_meta"]["unsafe_for_clinical_use"] is True


async def test_run_mcp_tool_stamps_elapsed_ms_on_success() -> None:
    async def body() -> dict[str, Any]:
        return {
            "_meta": {
                "tool": "resolve_variant",
                "request_id": "abc",
                "capabilities_version": "x",
                "timing": {"elapsed_ms": 0},
            }
        }

    result = await run_mcp_tool("resolve_variant", body, _ctx())
    # The 0 stub seeded by build_meta is overwritten with a real measurement.
    assert isinstance(result["_meta"]["timing"]["elapsed_ms"], int)
    assert result["_meta"]["timing"]["elapsed_ms"] >= 0


async def test_run_mcp_tool_stamps_timing_telemetry() -> None:
    from vep_link.observability import telemetry as t

    async def body() -> dict[str, Any]:
        # Simulate a warm hit that issued some upstream work this request.
        t.set_cache_status("hit")
        t.record_upstream(5.0)
        return {"_meta": {"timing": {"elapsed_ms": 0}}}

    out = await run_mcp_tool("x", body, McpErrorContext(tool_name="x"))
    timing = out["_meta"]["timing"]
    assert timing["cache_status"] == "hit"
    assert timing["upstream_ms"] == 5
    assert "elapsed_ms" in timing


async def test_run_mcp_tool_resets_telemetry_before_body() -> None:
    # A prior request's leftover must not leak: run_mcp_tool resets at the start,
    # so a body that records nothing reports the defaults (miss / 0).
    from vep_link.observability import telemetry as t

    t.set_cache_status("hit")
    t.record_upstream(999.0)

    async def body() -> dict[str, Any]:
        return {"_meta": {"timing": {"elapsed_ms": 0}}}

    out = await run_mcp_tool("x", body, McpErrorContext(tool_name="x"))
    timing = out["_meta"]["timing"]
    assert timing["cache_status"] == "miss"
    assert timing["upstream_ms"] == 0


async def test_run_mcp_tool_records_call_and_error_metrics() -> None:
    # A unique tool name keeps the process-wide singleton's counters deterministic
    # for this test (no other test records under this name).
    from vep_link.observability.metrics import METRICS

    async def ok() -> dict[str, Any]:
        return {
            "_meta": {"tool": "t", "request_id": "r", "capabilities_version": "v"},
        }

    async def fail() -> dict[str, Any]:
        raise DataNotFoundError("missing")

    await run_mcp_tool("metrics_probe_tool", ok, _ctx(tool_name="metrics_probe_tool"))
    await run_mcp_tool("metrics_probe_tool", fail, _ctx(tool_name="metrics_probe_tool"))
    text = METRICS.render_prometheus()
    assert 'vep_link_tool_calls_total{outcome="success",tool="metrics_probe_tool"} 1' in text
    assert 'vep_link_tool_calls_total{outcome="error",tool="metrics_probe_tool"} 1' in text
    assert 'vep_link_tool_errors_total{code="not_found",tool="metrics_probe_tool"} 1' in text


# ---------------------------------------------------------------------------
# run_mcp_tool: domain-exception -> code mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "expected_code"),
    [
        (VariantParseError("nope"), "invalid_input"),
        (UpstreamInputError("ensembl 400"), "invalid_input"),
        (DataNotFoundError("no mapping"), "not_found"),
        (AmbiguousMappingError("two maps"), "ambiguous"),
        (RateLimitedError("429"), "rate_limited"),
        (UpstreamTimeoutError("slow"), "upstream_timeout"),
        (EnsemblApiError("503"), "upstream_unavailable"),
    ],
)
async def test_run_mcp_tool_maps_each_domain_exception(exc: Exception, expected_code: str) -> None:
    async def body() -> dict[str, Any]:
        raise exc

    result = await run_mcp_tool("resolve_variant", body, _ctx())
    env = _envelope(result)
    assert env["success"] is False
    assert env["error_code"] == expected_code
    assert env["fallback_tool"] == "get_capabilities"
    assert env["recovery"]
    assert isinstance(env["recovery_action"], str) and env["recovery_action"]
    meta = env["_meta"]
    assert "capabilities_version" in meta
    assert meta["unsafe_for_clinical_use"] is True


async def test_unsupported_contig_maps_to_unsupported_input_not_invalid() -> None:
    # UnsupportedContigError subclasses VariantParseError -> specificity ordering
    # must classify it as unsupported_input, NOT invalid_input.
    assert issubclass(UnsupportedContigError, VariantParseError)

    async def body() -> dict[str, Any]:
        raise UnsupportedContigError("MT not supported")

    result = await run_mcp_tool("liftover_variant", body, _ctx(tool_name="liftover_variant"))
    env = _envelope(result)
    assert env["error_code"] == "unsupported_input"


async def test_run_mcp_tool_envelope_carries_assembly_and_next_commands() -> None:
    async def body() -> dict[str, Any]:
        raise DataNotFoundError("none")

    ctx = _ctx(
        assembly="GRCh37",
        next_commands=[{"tool": "resolve_variant", "arguments": {"variant": "x"}}],
    )
    result = await run_mcp_tool("resolve_variant", body, ctx)
    env = _envelope(result)
    assert env["_meta"]["assembly"] == "GRCh37"
    # next_commands rides in _meta on the flat frame, not a nested "error" block.
    assert env["_meta"]["next_commands"] == [
        {"tool": "resolve_variant", "arguments": {"variant": "x"}}
    ]


# ---------------------------------------------------------------------------
# run_mcp_tool: internal_error sanitization
# ---------------------------------------------------------------------------


async def test_generic_exception_becomes_sanitized_internal_error() -> None:
    async def body() -> dict[str, Any]:
        raise RuntimeError("boom")

    result = await run_mcp_tool("annotate_variant", body, _ctx(tool_name="annotate_variant"))
    env = _envelope(result)
    assert env["error_code"] == "internal_error"
    # The raw exception text MUST NOT leak.
    assert "boom" not in env["message"]
    assert "boom" not in env["recovery"]
    # A correlation id IS surfaced (in the message) for support reference.
    assert "correlation_id" in env["message"]
    assert "annotate_variant" in env["message"]
    assert env["fallback_tool"] == "get_capabilities"
    assert env["_meta"]["unsafe_for_clinical_use"] is True


async def test_unknown_vep_link_error_maps_to_internal_error() -> None:
    # A VepLinkError subclass with no explicit mapping falls through to
    # internal_error and is sanitized.
    class WeirdError(VepLinkError):
        pass

    async def body() -> dict[str, Any]:
        raise WeirdError("secret detail")

    result = await run_mcp_tool("resolve_variant", body, _ctx())
    env = _envelope(result)
    assert env["error_code"] == "internal_error"
    assert "secret detail" not in env["message"]
    assert "correlation_id" in env["message"]


async def test_internal_error_does_not_leak_raw_message() -> None:
    async def body() -> dict[str, Any]:
        raise RuntimeError("boom-detail")

    result = await run_mcp_tool("resolve_variant", body, _ctx())
    env = _envelope(result)

    # The sanitized envelope hides the raw exception text everywhere.
    assert "boom-detail" not in env["message"]
    assert "boom-detail" not in env["recovery"]
    assert env["error_code"] == "internal_error"


# ---------------------------------------------------------------------------
# install_validation_error_handler smoke test
# ---------------------------------------------------------------------------


def test_install_validation_error_handler_is_callable_and_safe_on_dummy() -> None:
    assert callable(install_validation_error_handler)

    class _Dummy:
        pass

    # Must not raise when passed an object that does not expose the FastMCP
    # internals (best-effort, import-safe).
    install_validation_error_handler(_Dummy())


def test_install_validation_error_handler_wraps_fastmcp_tools() -> None:
    fastmcp = pytest.importorskip("fastmcp")
    mcp = fastmcp.FastMCP("vep-link-test")

    @mcp.tool
    def echo(a: int) -> int:
        return a

    # Must not raise; idempotent on a real (stub-like) FastMCP instance.
    install_validation_error_handler(mcp)
    install_validation_error_handler(mcp)


async def test_install_validation_error_handler_returns_flat_iserror_tool_result() -> None:
    """A validation failure MUST produce the same flat v1 frame (wrapped in an
    ``is_error=True`` ``ToolResult``) as a domain exception, not a bespoke shape."""
    fastmcp = pytest.importorskip("fastmcp")
    mcp = fastmcp.FastMCP("vep-link-validation-test")

    @mcp.tool
    def echo(a: int) -> int:
        return a

    install_validation_error_handler(mcp)
    result = await mcp.call_tool("echo", {"a": "not-an-int"})
    assert isinstance(result, ToolResult)
    assert result.is_error is True
    env = result.structured_content
    assert env is not None
    assert env["success"] is False
    assert env["error_code"] == "invalid_input"
    assert env["_meta"]["unsafe_for_clinical_use"] is True
