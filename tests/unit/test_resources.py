"""Tests for the MCP metadata layer (resources, annotations, schema).

These cover the keystone shapes that the error envelope and all six tools
import: the research-use notice, tool annotations, the output-schema relaxer,
the capabilities payload, the capabilities-version hash, and the ``build_meta``
/ ``provenance`` builders.
"""

from __future__ import annotations

import re

import pytest

from vep_link import __version__
from vep_link.config import DEFAULT_VEP_OPTIONS, VEP_OPTION_ALLOWLIST
from vep_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from vep_link.mcp.resources import (
    CAPABILITIES_VERSION,
    ENSEMBL_VEP_CITATION,
    MCP_PROTOCOL_VERSION,
    RESEARCH_USE_NOTICE,
    VARIANT_RECODER_CITATION,
    build_meta,
    provenance,
    server_capabilities,
)
from vep_link.mcp.schema import relax_output_schema

EXPECTED_TOOLS = [
    "get_capabilities",
    "resolve_variant",
    "recode_variant",
    "annotate_variant",
    "annotate_variants_batch",
    "liftover_variant",
    "check_upstream_health",
]

EXPECTED_ERROR_CODES = [
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
]


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
def test_research_use_notice_exact_text() -> None:
    assert RESEARCH_USE_NOTICE == "Research use only; not for clinical decision support."


def test_mcp_protocol_version() -> None:
    assert MCP_PROTOCOL_VERSION == "2025-06-18"


def test_citations_contain_identifiers() -> None:
    assert "PMID:27268795" in ENSEMBL_VEP_CITATION
    assert "Variant Effect Predictor" in ENSEMBL_VEP_CITATION
    assert "rest.ensembl.org" in VARIANT_RECODER_CITATION


# --------------------------------------------------------------------------- #
# annotations.READ_ONLY_OPEN_WORLD
# --------------------------------------------------------------------------- #
def test_read_only_open_world_dict() -> None:
    assert READ_ONLY_OPEN_WORLD == {
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    }


# --------------------------------------------------------------------------- #
# schema.relax_output_schema
# --------------------------------------------------------------------------- #
def test_relax_output_schema_sets_additional_properties_on_root() -> None:
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    out = relax_output_schema(schema)
    assert out["additionalProperties"] is True


def test_relax_output_schema_nested_object() -> None:
    schema = {
        "type": "object",
        "properties": {
            "inner": {
                "type": "object",
                "properties": {"x": {"type": "integer"}},
            },
        },
    }
    out = relax_output_schema(schema)
    assert out["additionalProperties"] is True
    assert out["properties"]["inner"]["additionalProperties"] is True


def test_relax_output_schema_array_of_objects() -> None:
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"y": {"type": "string"}},
                },
            },
        },
    }
    out = relax_output_schema(schema)
    assert out["properties"]["items"]["items"]["additionalProperties"] is True


def test_relax_output_schema_does_not_mutate_input() -> None:
    schema = {
        "type": "object",
        "properties": {
            "inner": {"type": "object", "properties": {"x": {"type": "integer"}}},
        },
    }
    out = relax_output_schema(schema)
    # Input untouched.
    assert "additionalProperties" not in schema
    assert "additionalProperties" not in schema["properties"]["inner"]
    # Output is a distinct object.
    assert out is not schema


def test_relax_output_schema_handles_combinators() -> None:
    schema = {
        "anyOf": [
            {"type": "object", "properties": {"a": {"type": "string"}}},
            {"type": "null"},
        ],
    }
    out = relax_output_schema(schema)
    assert out["anyOf"][0]["additionalProperties"] is True


# --------------------------------------------------------------------------- #
# server_capabilities
# --------------------------------------------------------------------------- #
def test_server_capabilities_required_keys() -> None:
    caps = server_capabilities()
    required = {
        "server",
        "server_version",
        "mcp_protocol_version",
        "research_use_only",
        "disclaimer",
        "assemblies",
        "default_assembly",
        "input_formats",
        "response_modes",
        "tools",
        "error_codes",
        "vep_default_options",
        "vep_option_allowlist",
        "batch_max",
        "citation",
        "resources",
        "notes",
    }
    assert required.issubset(caps.keys())


def test_server_capabilities_identity_fields() -> None:
    caps = server_capabilities()
    assert caps["server"] == "vep-link"
    assert caps["server_version"] == __version__
    assert caps["mcp_protocol_version"] == MCP_PROTOCOL_VERSION
    assert caps["research_use_only"] is True
    assert caps["disclaimer"] == RESEARCH_USE_NOTICE


def test_server_version_is_0_3_0() -> None:
    assert server_capabilities()["server_version"] == "0.3.0"


def test_standard_tier_doc_mentions_collapsed() -> None:
    assert "collapsed" in server_capabilities()["response_mode_tiers"]["standard"]


def test_notes_mention_v03_contract() -> None:
    blob = " ".join(server_capabilities()["notes"]).lower()
    assert "cache_status" in blob
    assert "upstream_ms" in blob
    assert "most_severe_consequence" in blob


def test_server_capabilities_assemblies_and_modes() -> None:
    caps = server_capabilities()
    assert caps["assemblies"] == ["GRCh38", "GRCh37"]
    assert caps["default_assembly"] == "GRCh38"
    assert caps["response_modes"] == ["minimal", "compact", "standard", "full"]


def test_server_capabilities_input_formats() -> None:
    caps = server_capabilities()
    assert caps["input_formats"] == [
        "coordinate (CHR-POS-REF-ALT)",
        "rsID",
        "HGVS (g./c./n./p.)",
        "SPDI",
        "CNV (chr:start-end:TYPE)",
    ]


def test_server_capabilities_six_tools_by_name() -> None:
    caps = server_capabilities()
    tools = caps["tools"]
    assert isinstance(tools, list)
    names = [t["name"] for t in tools]
    assert names == EXPECTED_TOOLS
    for tool in tools:
        assert set(tool) >= {"name", "summary", "token_cost_hint"}
        assert tool["token_cost_hint"] in {"low", "medium", "high"}
        assert isinstance(tool["summary"], str) and tool["summary"]


def test_server_capabilities_error_codes() -> None:
    caps = server_capabilities()
    assert caps["error_codes"] == EXPECTED_ERROR_CODES


def test_server_capabilities_vep_options() -> None:
    caps = server_capabilities()
    assert caps["vep_default_options"] == DEFAULT_VEP_OPTIONS
    assert caps["vep_option_allowlist"] == sorted(VEP_OPTION_ALLOWLIST)


def test_server_capabilities_batch_max() -> None:
    assert server_capabilities()["batch_max"] == 200


def test_server_capabilities_citation() -> None:
    caps = server_capabilities()
    assert caps["citation"] == {
        "vep": ENSEMBL_VEP_CITATION,
        "variant_recoder": VARIANT_RECODER_CITATION,
    }


def test_server_capabilities_resources() -> None:
    caps = server_capabilities()
    assert caps["resources"] == [
        "vep://capabilities",
        "vep://usage",
        "vep://reference",
        "vep://citations",
        "vep://research-use",
        "vep://health",
    ]


def test_server_capabilities_notes_mention_plugins() -> None:
    caps = server_capabilities()
    blob = " ".join(caps["notes"]).lower()
    assert "spliceai" in blob
    assert "dbnsfp" in blob
    assert "instance" in blob


def test_server_capabilities_notes_mention_public_scores() -> None:
    # The precomputed predictor scores served by the public REST are documented.
    blob = " ".join(server_capabilities()["notes"]).lower()
    assert "revel" in blob
    assert "alphamissense" in blob


def test_server_capabilities_default_options_include_predictors() -> None:
    caps = server_capabilities()
    for flag in ("CADD", "REVEL", "AlphaMissense", "Conservation"):
        assert caps["vep_default_options"][flag] == "1"


# --------------------------------------------------------------------------- #
# CAPABILITIES_VERSION
# --------------------------------------------------------------------------- #
def test_capabilities_version_is_12_char_hex() -> None:
    assert isinstance(CAPABILITIES_VERSION, str)
    assert len(CAPABILITIES_VERSION) == 12
    assert re.fullmatch(r"[0-9a-f]{12}", CAPABILITIES_VERSION)


def test_capabilities_version_stable_across_calls() -> None:
    import hashlib
    import json

    def recompute() -> str:
        return hashlib.sha256(
            json.dumps(server_capabilities(), sort_keys=True).encode()
        ).hexdigest()[:12]

    assert recompute() == recompute()
    assert recompute() == CAPABILITIES_VERSION


# --------------------------------------------------------------------------- #
# build_meta
# --------------------------------------------------------------------------- #
def test_build_meta_minimal() -> None:
    meta = build_meta(tool="x", request_id="r")
    assert meta["tool"] == "x"
    assert meta["request_id"] == "r"
    assert meta["timing"] == {"elapsed_ms": 0}
    assert meta["capabilities_version"] == CAPABILITIES_VERSION
    assert meta["unsafe_for_clinical_use"] is True
    assert meta["next_commands"] == []
    assert "assembly" not in meta


def test_build_meta_with_assembly_and_timing() -> None:
    meta = build_meta(tool="annotate_variant", request_id="abc", elapsed_ms=42, assembly="GRCh38")
    assert meta["assembly"] == "GRCh38"
    assert meta["timing"]["elapsed_ms"] == 42


def test_build_meta_next_commands_and_extra() -> None:
    nc = [{"tool": "annotate_variant", "arguments": {"variant": "rs1"}}]
    meta = build_meta(
        tool="resolve_variant", request_id="r", next_commands=nc, extra={"served_warm": True}
    )
    assert meta["next_commands"] == nc
    assert meta["served_warm"] is True


def test_build_meta_no_assembly_when_none() -> None:
    meta = build_meta(tool="x", request_id="r", assembly=None)
    assert "assembly" not in meta


# --------------------------------------------------------------------------- #
# provenance
# --------------------------------------------------------------------------- #
def test_provenance_shape() -> None:
    prov = provenance(
        assembly="GRCh38",
        endpoint="https://rest.ensembl.org/vep/homo_sapiens/region",
    )
    assert prov["data_source"] == "Ensembl VEP / Variant Recoder REST"
    assert prov["assembly"] == "GRCh38"
    assert prov["endpoint"] == "https://rest.ensembl.org/vep/homo_sapiens/region"
    assert prov["retrieved"] is None
    assert "PMID:27268795" in prov["recommended_citation"]
    assert prov["recommended_citation"] == ENSEMBL_VEP_CITATION


def test_provenance_custom_source_and_retrieved() -> None:
    prov = provenance(
        assembly="GRCh37",
        endpoint="https://grch37.rest.ensembl.org/variant_recoder/human",
        retrieved="2026-06-16T00:00:00Z",
        source="Ensembl Variant Recoder REST",
    )
    assert prov["data_source"] == "Ensembl Variant Recoder REST"
    assert prov["retrieved"] == "2026-06-16T00:00:00Z"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
