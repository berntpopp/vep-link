"""Tests for the MCP error-envelope module (``vep_link.mcp.errors``).

Written test-first. These pin the deterministic contract the LLM branches on:

* the exception -> error-code map (with subclass-specificity ordering, so
  ``UnsupportedContigError`` does not collapse into ``invalid_input``),
* the envelope shape (``error`` + ``_meta``, ``fallback_tool`` ==
  ``get_capabilities``, ``capabilities_version`` + ``unsafe_for_clinical_use``),
* the success passthrough (the body's dict is returned unchanged), and
* ``internal_error`` sanitization (no leaked exception text; a correlation id
  is surfaced instead).
"""

from __future__ import annotations

from typing import Any

import pytest

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
    install_validation_error_handler,
    mcp_tool_error,
    run_mcp_tool,
)


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
    assert set(env) == {"error", "_meta"}
    error = env["error"]
    assert error["code"] == "invalid_input"
    assert error["message"] == "bad variant"
    assert error["recovery"] == "fix it"
    assert error["fallback_tool"] == "get_capabilities"
    assert error["next_commands"] == [{"tool": "resolve_variant", "arguments": {}}]

    meta = env["_meta"]
    assert meta["tool"] == "resolve_variant"
    assert meta["request_id"] == "req-123"
    assert meta["assembly"] == "GRCh38"
    assert meta["unsafe_for_clinical_use"] is True
    assert "capabilities_version" in meta


def test_mcp_tool_error_defaults_next_commands_to_empty_list() -> None:
    env = mcp_tool_error(
        code="not_found",
        message="no data",
        recovery="try resolve_variant",
        ctx=_ctx(),
    )
    assert env["error"]["next_commands"] == []


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


async def test_run_mcp_tool_returns_body_result_unchanged_on_success() -> None:
    payload = {
        "variant_id": "1-100-A-T",
        "_meta": {"tool": "resolve_variant", "request_id": "abc", "capabilities_version": "x"},
    }

    async def body() -> dict[str, Any]:
        return payload

    result = await run_mcp_tool("resolve_variant", body, _ctx())
    assert result is payload
    assert "error" not in result


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

    env = await run_mcp_tool("resolve_variant", body, _ctx())
    assert env["error"]["code"] == expected_code
    assert env["error"]["fallback_tool"] == "get_capabilities"
    assert env["error"]["recovery"]
    meta = env["_meta"]
    assert "capabilities_version" in meta
    assert meta["unsafe_for_clinical_use"] is True


async def test_unsupported_contig_maps_to_unsupported_input_not_invalid() -> None:
    # UnsupportedContigError subclasses VariantParseError -> specificity ordering
    # must classify it as unsupported_input, NOT invalid_input.
    assert issubclass(UnsupportedContigError, VariantParseError)

    async def body() -> dict[str, Any]:
        raise UnsupportedContigError("MT not supported")

    env = await run_mcp_tool("liftover_variant", body, _ctx(tool_name="liftover_variant"))
    assert env["error"]["code"] == "unsupported_input"


async def test_run_mcp_tool_envelope_carries_assembly_and_next_commands() -> None:
    async def body() -> dict[str, Any]:
        raise DataNotFoundError("none")

    ctx = _ctx(
        assembly="GRCh37",
        next_commands=[{"tool": "resolve_variant", "arguments": {"variant": "x"}}],
    )
    env = await run_mcp_tool("resolve_variant", body, ctx)
    assert env["_meta"]["assembly"] == "GRCh37"
    assert env["error"]["next_commands"] == [
        {"tool": "resolve_variant", "arguments": {"variant": "x"}}
    ]


# ---------------------------------------------------------------------------
# run_mcp_tool: internal_error sanitization
# ---------------------------------------------------------------------------


async def test_generic_exception_becomes_sanitized_internal_error() -> None:
    async def body() -> dict[str, Any]:
        raise RuntimeError("boom")

    env = await run_mcp_tool("annotate_variant", body, _ctx(tool_name="annotate_variant"))
    error = env["error"]
    assert error["code"] == "internal_error"
    # The raw exception text MUST NOT leak.
    assert "boom" not in error["message"]
    assert "boom" not in error["recovery"]
    # A correlation id IS surfaced (in the message) for support reference.
    assert "correlation_id" in error["message"]
    assert "annotate_variant" in error["message"]
    assert error["fallback_tool"] == "get_capabilities"
    assert env["_meta"]["unsafe_for_clinical_use"] is True


async def test_unknown_vep_link_error_maps_to_internal_error() -> None:
    # A VepLinkError subclass with no explicit mapping falls through to
    # internal_error and is sanitized.
    class WeirdError(VepLinkError):
        pass

    async def body() -> dict[str, Any]:
        raise WeirdError("secret detail")

    env = await run_mcp_tool("resolve_variant", body, _ctx())
    assert env["error"]["code"] == "internal_error"
    assert "secret detail" not in env["error"]["message"]
    assert "correlation_id" in env["error"]["message"]


async def test_internal_error_does_not_leak_raw_message() -> None:
    async def body() -> dict[str, Any]:
        raise RuntimeError("boom-detail")

    env = await run_mcp_tool("resolve_variant", body, _ctx())

    # The sanitized envelope hides the raw exception text everywhere.
    assert "boom-detail" not in env["error"]["message"]
    assert "boom-detail" not in env["error"]["recovery"]
    assert env["error"]["code"] == "internal_error"


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
