"""End-to-end MCP tool tests through FastMCP ``call_tool`` with a stubbed service.

These drive the six vep-link tools the way an MCP client does -- via
``facade.call_tool(name, arguments)`` -- and assert the contract each tool
promises: the success payload (shaped to the requested response_mode), the
``_meta`` block stamped on every response, the ``provenance`` block on
annotation results, and the deterministic error envelope our error module
RETURNS (not raises) when the service faults.

Note: unlike spliceailookup (which raises ``ToolError`` for failures), our
``vep_link.mcp.errors.run_mcp_tool`` RETURNS the structured error envelope as the
tool's normal dict result, so both success and error payloads are pulled out the
same way via :func:`structured`.
"""

from __future__ import annotations

from typing import Any

from tests.conftest import StubService
from vep_link.exceptions import DataNotFoundError
from vep_link.mcp.resources import server_capabilities


def structured(result: Any) -> dict[str, Any]:
    """Extract the structured payload from a FastMCP 3.4.2 call_tool result.

    fastmcp 3.4.2's ``call_tool`` returns a result object exposing the tool's
    dict output as ``.structured_content`` (or ``.data``). Older/edge shapes
    are handled defensively (a trailing tuple element). Mirrors the
    spliceailookup ``structured`` helper.
    """
    sc = getattr(result, "structured_content", None)
    if sc is None:
        sc = getattr(result, "data", None)
    if sc is None and isinstance(result, tuple):
        sc = result[-1]
    return sc or {}


# ---------------------------------------------------------------------------
# get_capabilities
# ---------------------------------------------------------------------------


async def test_get_capabilities_returns_server_and_all_tools(facade) -> None:
    data = structured(await facade.call_tool("get_capabilities", {}))
    assert data["server"] == "vep-link"
    assert len(data["tools"]) == 7
    tool_names = {t["name"] for t in data["tools"]}
    assert tool_names == {
        "get_capabilities",
        "resolve_variant",
        "recode_variant",
        "annotate_variant",
        "annotate_variants_batch",
        "liftover_variant",
        "check_upstream_health",
    }
    assert data["research_use_only"] is True
    # Every response carries a _meta block.
    assert data["_meta"]["tool"] == "get_capabilities"
    assert data["_meta"]["capabilities_version"]


def test_capabilities_payload_matches_server_capabilities() -> None:
    # The tool body is a thin wrapper over server_capabilities() + _meta.
    caps = server_capabilities()
    assert caps["server"] == "vep-link"
    assert len(caps["tools"]) == 7


# ---------------------------------------------------------------------------
# resolve_variant
# ---------------------------------------------------------------------------


async def test_resolve_variant_success(facade, stub_service: StubService) -> None:
    data = structured(await facade.call_tool("resolve_variant", {"variant": "rs6025"}))
    # Returns the stub's resolve_return fields.
    assert data["variant_id"] == stub_service.resolve_return["variant_id"]
    assert data["gene_symbol"] == stub_service.resolve_return["gene_symbol"]
    assert data["most_severe_consequence"] == "missense_variant"
    # _meta carries the safety flag and a next_command toward annotate_variant.
    assert data["_meta"]["unsafe_for_clinical_use"] is True
    next_tools = {c["tool"] for c in data["_meta"]["next_commands"]}
    assert "annotate_variant" in next_tools
    # The service was called with the GenomeBuild enum, not the raw string.
    name, kwargs = stub_service.calls[-1]
    assert name == "resolve"
    assert kwargs["variant"] == "rs6025"
    assert getattr(kwargs["build"], "value", kwargs["build"]) == "GRCh38"


async def test_resolve_variant_respects_assembly(facade, stub_service: StubService) -> None:
    structured(
        await facade.call_tool("resolve_variant", {"variant": "rs6025", "assembly": "GRCh37"})
    )
    _, kwargs = stub_service.calls[-1]
    assert getattr(kwargs["build"], "value", kwargs["build"]) == "GRCh37"


# ---------------------------------------------------------------------------
# recode_variant
# ---------------------------------------------------------------------------


async def test_recode_variant_returns_results(facade, stub_service: StubService) -> None:
    data = structured(await facade.call_tool("recode_variant", {"variants": ["rs123"]}))
    assert data["assembly"] == "GRCh38"
    assert data["results"] == stub_service.recode_return
    assert data["_meta"]["tool"] == "recode_variant"
    name, kwargs = stub_service.calls[-1]
    assert name == "recode"
    assert kwargs["variants"] == ["rs123"]


async def test_recode_variant_passes_fields(facade, stub_service: StubService) -> None:
    structured(
        await facade.call_tool("recode_variant", {"variants": ["rs123"], "fields": "hgvsg,spdi"})
    )
    _, kwargs = stub_service.calls[-1]
    assert kwargs["fields"] == "hgvsg,spdi"


# ---------------------------------------------------------------------------
# annotate_variant
# ---------------------------------------------------------------------------


async def test_annotate_variant_compact_success(facade, stub_service: StubService) -> None:
    data = structured(await facade.call_tool("annotate_variant", {"variant": "1-1000-A-T"}))
    # Shaped to compact: identity + position + representative_transcript + freqs.
    assert data["variant_id"] == "1-1000-A-T"
    assert "representative_transcript" in data
    # Annotation results carry provenance + a _meta with the capabilities hash.
    assert data["provenance"]["data_source"]
    assert data["provenance"]["endpoint"].endswith("/vep/homo_sapiens/region")
    assert data["_meta"]["capabilities_version"]
    name, kwargs = stub_service.calls[-1]
    assert name == "annotate"
    assert kwargs["vep_options"] is None


async def test_annotate_variant_disallowed_option_is_invalid_input(facade) -> None:
    data = structured(
        await facade.call_tool(
            "annotate_variant", {"variant": "1-1000-A-T", "vep_options": {"BOGUS": "1"}}
        )
    )
    assert data["error"]["code"] == "invalid_input"
    assert data["error"]["fallback_tool"] == "get_capabilities"


async def test_annotate_variant_spliceai_note(facade, stub_service: StubService) -> None:
    data = structured(
        await facade.call_tool(
            "annotate_variant", {"variant": "1-1000-A-T", "vep_options": {"SpliceAI": "1"}}
        )
    )
    # SpliceAI is allowlisted but instance-dependent -> surfaced in a note.
    assert "note" in data
    assert "instance" in data["note"].lower() or "Ensembl REST" in data["note"]
    # The allowed option WAS forwarded to the service.
    _, kwargs = stub_service.calls[-1]
    assert kwargs["vep_options"] == {"SpliceAI": "1"}


# ---------------------------------------------------------------------------
# annotate_variants_batch
# ---------------------------------------------------------------------------


async def test_annotate_variants_batch_returns_results_errors_summary(
    facade, stub_service: StubService
) -> None:
    stub_service.batch_return = {
        "results": [
            {
                "input": "1-1000-A-T",
                "variant_id": "1-1000-A-T",
                "assembly": "GRCh38",
                "most_severe_consequence": "missense_variant",
                "transcript_consequences": [],
            }
        ],
        "errors": [{"input": "bogus", "error_code": "invalid_input", "message": "bad"}],
        "summary": {"requested": 2, "annotated": 1, "failed": 1},
    }
    data = structured(
        await facade.call_tool("annotate_variants_batch", {"variants": ["1-1000-A-T", "bogus"]})
    )
    assert data["assembly"] == "GRCh38"
    assert len(data["results"]) == 1
    # Each shaped result preserves its original input.
    assert data["results"][0]["input"] == "1-1000-A-T"
    assert data["results"][0]["variant_id"] == "1-1000-A-T"
    assert data["errors"] == stub_service.batch_return["errors"]
    assert data["summary"]["requested"] == 2
    assert data["_meta"]["tool"] == "annotate_variants_batch"


async def test_annotate_variants_batch_disallowed_option_is_invalid_input(facade) -> None:
    data = structured(
        await facade.call_tool(
            "annotate_variants_batch",
            {"variants": ["1-1000-A-T"], "vep_options": {"BOGUS": "1"}},
        )
    )
    assert data["error"]["code"] == "invalid_input"


# ---------------------------------------------------------------------------
# liftover_variant
# ---------------------------------------------------------------------------


async def test_liftover_variant_success(facade, stub_service: StubService) -> None:
    data = structured(
        await facade.call_tool(
            "liftover_variant",
            {
                "variant": "1-1000-A-T",
                "from_assembly": "GRCh37",
                "to_assembly": "GRCh38",
            },
        )
    )
    assert data["lifted"] == stub_service.liftover_return["lifted"]
    assert data["_meta"]["tool"] == "liftover_variant"
    name, kwargs = stub_service.calls[-1]
    assert name == "liftover"
    assert getattr(kwargs["from_build"], "value", kwargs["from_build"]) == "GRCh37"
    assert getattr(kwargs["to_build"], "value", kwargs["to_build"]) == "GRCh38"


async def test_liftover_variant_same_assembly_is_invalid_input(facade) -> None:
    data = structured(
        await facade.call_tool(
            "liftover_variant",
            {
                "variant": "1-1000-A-T",
                "from_assembly": "GRCh38",
                "to_assembly": "GRCh38",
            },
        )
    )
    assert data["error"]["code"] == "invalid_input"


# ---------------------------------------------------------------------------
# Error mapping: service exceptions -> deterministic envelope
# ---------------------------------------------------------------------------


async def test_resolve_not_found_maps_to_not_found_envelope(
    facade, stub_service: StubService
) -> None:
    stub_service.resolve_error = DataNotFoundError("x")
    data = structured(await facade.call_tool("resolve_variant", {"variant": "rsbad"}))
    assert data["error"]["code"] == "not_found"
    assert data["error"]["fallback_tool"] == "get_capabilities"


# ---------------------------------------------------------------------------
# Registration: all six tools are present and callable
# ---------------------------------------------------------------------------


async def test_all_six_tools_registered(facade) -> None:
    tools = await facade.list_tools()
    names = {t.name for t in tools}
    assert {
        "get_capabilities",
        "resolve_variant",
        "recode_variant",
        "annotate_variant",
        "annotate_variants_batch",
        "liftover_variant",
    } <= names


async def test_each_tool_is_invocable_with_minimal_args(facade) -> None:
    minimal: dict[str, dict[str, Any]] = {
        "get_capabilities": {},
        "resolve_variant": {"variant": "rs6025"},
        "recode_variant": {"variants": ["rs123"]},
        "annotate_variant": {"variant": "1-1000-A-T"},
        "annotate_variants_batch": {"variants": ["1-1000-A-T"]},
        "liftover_variant": {
            "variant": "1-1000-A-T",
            "from_assembly": "GRCh37",
            "to_assembly": "GRCh38",
        },
    }
    for name, args in minimal.items():
        # Must not raise KeyError (tool registered) and must yield a dict.
        data = structured(await facade.call_tool(name, args))
        assert isinstance(data, dict)
        assert data
