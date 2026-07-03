"""End-to-end MCP tool tests through FastMCP ``call_tool`` with a stubbed service.

These drive the six vep-link tools the way an MCP client does -- via
``facade.call_tool(name, arguments)`` -- and assert the contract each tool
promises: the success payload (shaped to the requested response_mode, with a
top-level ``success: true``), the ``_meta`` block stamped on every response,
the ``provenance`` block on annotation results, and the FLAT Response-Envelope
Standard v1 error frame (``success: false``, ``error_code``, ...) our error
module builds when the service faults.

Note: unlike spliceailookup (which raises ``ToolError`` for failures), our
``vep_link.mcp.errors.run_mcp_tool`` RETURNS the structured envelope as the
tool's normal result (a dict on success, a ``ToolResult`` with ``is_error=True``
on failure), so both success and error payloads are pulled out the same way via
:func:`structured` (which reads ``.structured_content`` off the
:class:`fastmcp.tools.ToolResult` FastMCP always wraps a tool result in). The
``isError`` wire flag itself is asserted directly on a couple of representative
error-path tests below.
"""

from __future__ import annotations

from typing import Any

from tests.conftest import StubService
from tests.fixtures import VEP_REGION_MISSENSE
from vep_link.exceptions import DataNotFoundError
from vep_link.mcp.resources import server_capabilities
from vep_link.services.extraction import build_annotation


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
    result = await facade.call_tool("resolve_variant", {"variant": "rs6025"})
    assert result.is_error is False
    data = structured(result)
    # Response-Envelope Standard v1: every success carries a top-level success: true.
    assert data["success"] is True
    # Returns the stub's resolve_return in the new variants[] shape.
    expected = stub_service.resolve_return["variants"][0]
    first = data["variants"][0]
    assert first["variant_id"] == expected["variant_id"]
    assert first["gene_symbol"] == expected["gene_symbol"]
    assert first["most_severe_consequence"] == "missense_variant"
    assert data["warnings"] == []
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
    # variants[] of one; each entry shaped to compact (representative_transcript).
    assert data["query"] == "1-1000-A-T"
    assert data["warnings"] == []
    first = data["variants"][0]
    assert first["variant_id"] == "1-1000-A-T"
    assert "representative_transcript" in first
    # Annotation results carry provenance + a _meta with the capabilities hash.
    assert data["provenance"]["data_source"]
    assert data["provenance"]["endpoint"].endswith("/vep/homo_sapiens/region")
    assert data["_meta"]["capabilities_version"]
    name, kwargs = stub_service.calls[-1]
    assert name == "annotate"
    assert kwargs["vep_options"] is None


async def test_annotate_variant_populates_observability_and_next_commands(
    facade, stub_service: StubService
) -> None:
    data = structured(await facade.call_tool("annotate_variant", {"variant": "1-1000-A-T"}))
    # P2: elapsed_ms is a real measurement, not a 0 stub; retrieved is a real ts.
    assert isinstance(data["_meta"]["timing"]["elapsed_ms"], int)
    assert data["_meta"]["timing"]["elapsed_ms"] >= 0
    assert data["provenance"]["retrieved"]
    assert "T" in data["provenance"]["retrieved"]  # ISO-8601 date/time separator
    # P3: next_commands steer the canonical follow-ups (recode + liftover).
    next_tools = {c["tool"] for c in data["_meta"]["next_commands"]}
    assert "recode_variant" in next_tools
    assert "liftover_variant" in next_tools


def _annotate_return_one(variant_id: str = "1-1000-A-T") -> dict[str, Any]:
    """Wrap a real fixture annotation in the service's variants[] envelope."""
    return {
        "query": variant_id,
        "assembly": "GRCh38",
        "variants": [
            build_annotation(VEP_REGION_MISSENSE[0], variant_id=variant_id, assembly="GRCh38")
        ],
        "warnings": [],
    }


async def test_annotate_variant_standard_truncation_steer(
    facade, stub_service: StubService
) -> None:
    # A real 2-transcript annotation: one is an uninformative MODIFIER neighbour.
    stub_service.annotate_return = _annotate_return_one()
    data = structured(
        await facade.call_tool(
            "annotate_variant", {"variant": "1-1000-A-T", "response_mode": "standard"}
        )
    )
    first = data["variants"][0]
    # Default (auto) standard view shows 1 of 2 and says so per-variant in-row.
    assert len(first["transcript_consequences"]) == 1
    assert first["transcripts_summary"] == {"shown": 1, "collapsed": 0, "total": 2}
    # The steer offers a ready-to-call widen-to-all follow-up in _meta.
    widen = [
        c
        for c in data["_meta"]["next_commands"]
        if c["tool"] == "annotate_variant" and c["arguments"].get("transcripts") == "all"
    ]
    assert widen, "expected a transcripts=all widen suggestion when truncated"
    # CADD/GERP hoisted once to the variant level, not repeated per transcript.
    assert first["position_scores"]["cadd_phred"] == 25.1


async def test_annotate_variant_transcripts_all_returns_every_transcript(
    facade, stub_service: StubService
) -> None:
    stub_service.annotate_return = _annotate_return_one()
    data = structured(
        await facade.call_tool(
            "annotate_variant",
            {"variant": "1-1000-A-T", "response_mode": "standard", "transcripts": "all"},
        )
    )
    first = data["variants"][0]
    assert len(first["transcript_consequences"]) == 2
    assert "transcripts_summary" not in first


async def test_annotate_variant_multi_alt_carries_warning(
    facade, stub_service: StubService
) -> None:
    # A multi-allelic service result flows through as variants[] + warnings[].
    stub_service.annotate_return = {
        "query": "rs6025",
        "assembly": "GRCh38",
        "variants": [
            build_annotation(
                VEP_REGION_MISSENSE[0], variant_id="1-169549811-C-A", assembly="GRCh38"
            ),
            build_annotation(
                VEP_REGION_MISSENSE[0], variant_id="1-169549811-C-T", assembly="GRCh38"
            ),
        ],
        "warnings": [
            {
                "code": "multiple_alts",
                "message": "Input maps to 2 ALT alleles; all are returned in variants[].",
                "context": {"count": 2, "variants": ["1-169549811-C-A", "1-169549811-C-T"]},
            }
        ],
    }
    data = structured(await facade.call_tool("annotate_variant", {"variant": "rs6025"}))
    assert [v["variant_id"] for v in data["variants"]] == ["1-169549811-C-A", "1-169549811-C-T"]
    assert data["warnings"][0]["code"] == "multiple_alts"


async def test_annotate_variant_forwards_allele(facade, stub_service: StubService) -> None:
    await facade.call_tool("annotate_variant", {"variant": "rs6025", "allele": "T"})
    _, kwargs = stub_service.calls[-1]
    assert kwargs["allele"] == "T"


async def test_annotate_variant_disallowed_option_is_invalid_input(facade) -> None:
    result = await facade.call_tool(
        "annotate_variant", {"variant": "1-1000-A-T", "vep_options": {"BOGUS": "1"}}
    )
    assert result.is_error is True
    data = structured(result)
    assert data["success"] is False
    assert data["error_code"] == "invalid_input"
    assert data["fallback_tool"] == "get_capabilities"


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
    assert data["error_code"] == "invalid_input"


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
    assert data["warnings"] == []  # clean lift carries an empty warnings channel
    assert data["_meta"]["tool"] == "liftover_variant"
    name, kwargs = stub_service.calls[-1]
    assert name == "liftover"
    assert getattr(kwargs["from_build"], "value", kwargs["from_build"]) == "GRCh37"
    assert getattr(kwargs["to_build"], "value", kwargs["to_build"]) == "GRCh38"


async def test_liftover_variant_same_assembly_is_invalid_input(facade) -> None:
    result = await facade.call_tool(
        "liftover_variant",
        {
            "variant": "1-1000-A-T",
            "from_assembly": "GRCh38",
            "to_assembly": "GRCh38",
        },
    )
    assert result.is_error is True
    data = structured(result)
    assert data["error_code"] == "invalid_input"


# ---------------------------------------------------------------------------
# Error mapping: service exceptions -> deterministic envelope
# ---------------------------------------------------------------------------


async def test_resolve_not_found_maps_to_not_found_envelope(
    facade, stub_service: StubService
) -> None:
    stub_service.resolve_error = DataNotFoundError("x")
    data = structured(await facade.call_tool("resolve_variant", {"variant": "rsbad"}))
    assert data["error_code"] == "not_found"
    assert data["fallback_tool"] == "get_capabilities"


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
