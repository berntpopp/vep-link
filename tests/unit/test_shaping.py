"""Tests for vep_link.mcp.shaping.

These cover the pure response-shaping projections that take a normalized
annotation dict (from ``vep_link.services.extraction.build_annotation``) and
project it into the four ``ResponseMode`` tiers. Fixtures mirror real Ensembl
VEP ``region`` payloads (see ``tests/fixtures/__init__.py``).
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.fixtures import VEP_REGION_MISSENSE
from vep_link.mcp.shaping import (
    pick_representative_transcript,
    shape_annotation,
)
from vep_link.models.enums import ResponseMode
from vep_link.services.extraction import build_annotation

DATA = build_annotation(VEP_REGION_MISSENSE[0], variant_id="1-1000-A-T", assembly="GRCh38")

# The maximal key set a fully-populated projected transcript can carry. CADD and
# GERP (cadd_phred/conservation) are NOT here: they are genomic-position scores
# hoisted once to a variant-level ``position_scores`` object. Null-valued keys
# are dropped, so a sparse transcript carries a subset of these.
_REP_KEYS = {
    "gene_symbol",
    "transcript_id",
    "consequence_terms",
    "impact",
    "hgvsc",
    "hgvsp",
    "protein_position",
    "sift_prediction",
    "polyphen_prediction",
    "revel",
    "am_pathogenicity",
    "am_class",
}


def _empty_annotation() -> dict[str, Any]:
    """An annotation with no transcript_consequences (edge case)."""
    return build_annotation(
        {
            "input": "1 2000 . C G . . .",
            "seq_region_name": "1",
            "start": 2000,
            "end": 2000,
            "allele_string": "C/G",
            "strand": 1,
            "most_severe_consequence": "intergenic_variant",
        },
        variant_id="1-2000-C-G",
        assembly="GRCh38",
    )


# --- pick_representative_transcript --------------------------------------


def test_pick_representative_empty_is_none() -> None:
    assert pick_representative_transcript([]) is None


def test_pick_representative_chooses_mane_canonical() -> None:
    chosen = pick_representative_transcript(DATA["transcript_consequences"])
    assert chosen is not None
    assert chosen["transcript_id"] == "ENST00000123456"


def test_pick_representative_pick_beats_canonical() -> None:
    transcripts: list[dict[str, Any]] = [
        {"transcript_id": "CANON", "canonical": 1},
        {"transcript_id": "PICKED", "pick": 1},
    ]
    chosen = pick_representative_transcript(transcripts)
    assert chosen is not None
    assert chosen["transcript_id"] == "PICKED"


def test_pick_representative_falls_back_to_first() -> None:
    transcripts: list[dict[str, Any]] = [
        {"transcript_id": "FIRST"},
        {"transcript_id": "SECOND"},
    ]
    chosen = pick_representative_transcript(transcripts)
    assert chosen is not None
    assert chosen["transcript_id"] == "FIRST"


# --- minimal -------------------------------------------------------------


def test_minimal_has_exactly_four_keys() -> None:
    shaped = shape_annotation(DATA, ResponseMode.MINIMAL)
    assert set(shaped.keys()) == {
        "variant_id",
        "assembly",
        "most_severe_consequence",
        "gene_symbol",
    }


def test_minimal_values() -> None:
    shaped = shape_annotation(DATA, ResponseMode.MINIMAL)
    assert shaped == {
        "variant_id": "1-1000-A-T",
        "assembly": "GRCh38",
        "most_severe_consequence": "missense_variant",
        "gene_symbol": "GENE1",
    }


def test_minimal_works_with_empty_transcripts() -> None:
    shaped = shape_annotation(_empty_annotation(), ResponseMode.MINIMAL)
    assert set(shaped.keys()) == {
        "variant_id",
        "assembly",
        "most_severe_consequence",
        "gene_symbol",
    }
    assert shaped["gene_symbol"] is None


# --- compact -------------------------------------------------------------


def test_compact_representative_transcript() -> None:
    shaped = shape_annotation(DATA, ResponseMode.COMPACT)
    rep = shaped["representative_transcript"]
    assert rep is not None
    # Fully-populated transcript -> carries the whole projected key set; CADD is
    # no longer per-transcript (it is hoisted to position_scores).
    assert set(rep.keys()) == _REP_KEYS
    assert rep["gene_symbol"] == "GENE1"
    assert "cadd_phred" not in rep
    assert rep["transcript_id"] == "ENST00000123456"


def test_compact_representative_carries_substitution_scores() -> None:
    # The default (compact) mode must already expose the substitution-specific
    # predictors so an interpreter need not widen to standard/full to see them.
    rep = shape_annotation(DATA, ResponseMode.COMPACT)["representative_transcript"]
    assert rep["revel"] == 0.84
    assert rep["am_pathogenicity"] == 0.92
    assert rep["am_class"] == "pathogenic"
    # GERP conservation is a genomic-position score: hoisted, not on the row.
    assert "conservation" not in rep


def test_compact_hoists_position_scores_once() -> None:
    # CADD/GERP appear exactly once, at the variant level, not per transcript.
    shaped = shape_annotation(DATA, ResponseMode.COMPACT)
    assert shaped["position_scores"] == {
        "cadd_phred": 25.1,
        "cadd_raw": 3.214,
        "conservation": 5.6,
    }


def test_compact_null_strips_representative() -> None:
    # A sparse transcript drops its null keys instead of serializing them.
    sparse = build_annotation(
        {
            "most_severe_consequence": "upstream_gene_variant",
            "transcript_consequences": [
                {
                    "gene_symbol": "G",
                    "transcript_id": "ENST_SPARSE",
                    "consequence_terms": ["upstream_gene_variant"],
                    "impact": "MODIFIER",
                }
            ],
        },
        variant_id="1-5-A-T",
        assembly="GRCh38",
    )
    rep = shape_annotation(sparse, ResponseMode.COMPACT)["representative_transcript"]
    assert set(rep.keys()) == {"gene_symbol", "transcript_id", "consequence_terms", "impact"}
    assert "hgvsc" not in rep
    assert "revel" not in rep


def test_compact_representative_carries_most_severe() -> None:
    # Contiguous-gene locus: the canonical neighbour (TSC2) carries only a
    # MODIFIER consequence; the worst effect (stop_gained) is on PKD1. The compact
    # representative_transcript must describe the variant's worst effect.
    data = {
        "variant_id": "16-2090952-G-A",
        "assembly": "GRCh38",
        "most_severe_consequence": "stop_gained",
        "gene_symbol": "PKD1",
        "seq_region_name": "16",
        "start": 2090952,
        "end": 2090952,
        "allele_string": "G/A",
        "position_scores": {},
        "frequencies": [],
        "transcript_consequences": [
            {
                "transcript_id": "T_TSC2",
                "gene_symbol": "TSC2",
                "consequence_terms": ["downstream_gene_variant"],
                "impact": "MODIFIER",
                "canonical": 1,
            },
            {
                "transcript_id": "T_PKD1",
                "gene_symbol": "PKD1",
                "consequence_terms": ["stop_gained"],
                "impact": "HIGH",
            },
        ],
    }
    shaped = shape_annotation(data, "compact")
    assert shaped["representative_transcript"]["gene_symbol"] == "PKD1"
    assert "stop_gained" in shaped["representative_transcript"]["consequence_terms"]


def test_minimal_omits_position_scores() -> None:
    assert "position_scores" not in shape_annotation(DATA, ResponseMode.MINIMAL)


def test_compact_includes_position_and_frequencies() -> None:
    shaped = shape_annotation(DATA, ResponseMode.COMPACT)
    assert shaped["seq_region_name"] == "1"
    assert shaped["start"] == 1000
    assert shaped["end"] == 1000
    assert shaped["allele_string"] == "A/T"
    assert shaped["frequencies"] == [{"allele": "T", "gnomade": 0.0001234, "gnomadg": 0.0002345}]


def test_compact_omits_full_transcript_list() -> None:
    shaped = shape_annotation(DATA, ResponseMode.COMPACT)
    assert "transcript_consequences" not in shaped
    assert "colocated_variants" not in shaped


def test_compact_includes_minimal_identity_fields() -> None:
    shaped = shape_annotation(DATA, ResponseMode.COMPACT)
    assert shaped["variant_id"] == "1-1000-A-T"
    assert shaped["assembly"] == "GRCh38"
    assert shaped["most_severe_consequence"] == "missense_variant"
    assert shaped["gene_symbol"] == "GENE1"


def test_compact_empty_transcripts_representative_is_none() -> None:
    shaped = shape_annotation(_empty_annotation(), ResponseMode.COMPACT)
    assert shaped["representative_transcript"] is None
    assert shaped["frequencies"] == []


def test_compact_is_default_mode() -> None:
    # Default (no mode argument) behaves like COMPACT.
    assert shape_annotation(DATA) == shape_annotation(DATA, ResponseMode.COMPACT)


# --- standard ------------------------------------------------------------


def test_standard_auto_drops_uninformative_modifier() -> None:
    # The fixture's 2nd transcript is an all-null MODIFIER upstream neighbor: the
    # default (auto) standard view drops it and reports 1 of 2.
    shaped = shape_annotation(DATA, ResponseMode.STANDARD)
    assert len(shaped["transcript_consequences"]) == 1
    assert shaped["transcript_consequences"][0]["transcript_id"] == "ENST00000123456"
    assert shaped["transcripts_summary"] == {"shown": 1, "collapsed": 0, "total": 2}


def test_standard_all_opt_in_keeps_every_transcript() -> None:
    # transcripts="all" disables the filter/cap: both transcripts are returned and
    # no truncation summary is emitted (shown == total).
    shaped = shape_annotation(DATA, ResponseMode.STANDARD, transcripts="all")
    assert len(shaped["transcript_consequences"]) == 2
    assert "transcripts_summary" not in shaped


def test_standard_null_strips_uninformative_row_in_all_mode() -> None:
    # Even the kept-by-opt-in MODIFIER row is null-stripped to its non-null keys.
    shaped = shape_annotation(DATA, ResponseMode.STANDARD, transcripts="all")
    neighbor = next(
        tc for tc in shaped["transcript_consequences"] if tc["transcript_id"] == "ENST00000999999"
    )
    assert set(neighbor.keys()) == {
        "gene_symbol",
        "transcript_id",
        "consequence_terms",
        "impact",
    }


def test_standard_caps_to_max_transcripts_by_severity() -> None:
    # With more informative transcripts than the cap, only the top-N by impact
    # severity are shown and the rest are summarized as truncated.
    tcs = [
        {
            "transcript_id": f"ENST{i}",
            "gene_symbol": "G",
            "consequence_terms": ["missense_variant"],
            "impact": "MODERATE",
            "hgvsc": f"c.{i}A>T",
        }
        for i in range(5)
    ]
    # One HIGH-impact transcript must survive the cap (sorted first).
    tcs[3]["impact"] = "HIGH"
    ann = build_annotation(
        {"most_severe_consequence": "missense_variant", "transcript_consequences": tcs},
        variant_id="1-9-A-T",
        assembly="GRCh38",
    )
    shaped = shape_annotation(ann, ResponseMode.STANDARD, max_transcripts=2)
    assert len(shaped["transcript_consequences"]) == 2
    assert shaped["transcript_consequences"][0]["impact"] == "HIGH"
    assert shaped["transcripts_summary"] == {"shown": 2, "collapsed": 0, "total": 5}


def test_standard_transcripts_are_projected_and_null_stripped() -> None:
    shaped = shape_annotation(DATA, ResponseMode.STANDARD, transcripts="all")
    for tc in shaped["transcript_consequences"]:
        # Every key present is in the allowed set and no key has a null value.
        assert set(tc.keys()) <= _REP_KEYS
        assert all(v is not None for v in tc.values())


def test_standard_includes_frequencies_and_position() -> None:
    shaped = shape_annotation(DATA, ResponseMode.STANDARD)
    assert shaped["frequencies"] == [{"allele": "T", "gnomade": 0.0001234, "gnomadg": 0.0002345}]
    assert shaped["seq_region_name"] == "1"
    assert shaped["start"] == 1000
    assert shaped["position_scores"]["cadd_phred"] == 25.1


def test_standard_omits_representative_and_colocated() -> None:
    shaped = shape_annotation(DATA, ResponseMode.STANDARD)
    assert "representative_transcript" not in shaped
    assert "colocated_variants" not in shaped


def test_standard_empty_transcripts() -> None:
    shaped = shape_annotation(_empty_annotation(), ResponseMode.STANDARD)
    assert shaped["transcript_consequences"] == []
    assert "transcripts_summary" not in shaped


# --- collapse_identical --------------------------------------------------


def test_collapse_identical_merges_only_byte_equal_rows() -> None:
    from vep_link.mcp.shaping import collapse_identical

    rows = [
        {
            "transcript_id": "A",
            "gene_symbol": "G",
            "consequence_terms": ["missense_variant"],
            "impact": "MODERATE",
            "hgvsc": "A:c.1G>A",
            "hgvsp": "p.G1D",
            "protein_position": "1",
            "mane_select": "NM.1",
        },
        {
            "transcript_id": "B",
            "gene_symbol": "G",
            "consequence_terms": ["missense_variant"],
            "impact": "MODERATE",
            "hgvsc": "A:c.1G>A",
            "hgvsp": "p.G1D",
            "protein_position": "1",
        },
        # Different hgvsc + protein_position -> MUST NOT merge.
        {
            "transcript_id": "C",
            "gene_symbol": "G",
            "consequence_terms": ["missense_variant"],
            "impact": "MODERATE",
            "hgvsc": "C:c.5G>A",
            "hgvsp": "p.G2D",
            "protein_position": "2",
        },
    ]
    collapsed, merged = collapse_identical(rows)
    assert len(collapsed) == 2
    rep = next(r for r in collapsed if r["hgvsc"] == "A:c.1G>A")
    assert rep["transcript_id"] == "A"  # MANE member kept as representative
    assert rep["equivalent_transcript_ids"] == ["B"]
    assert merged == 1  # one isoform folded in
    solo = next(r for r in collapsed if r["hgvsc"] == "C:c.5G>A")
    assert "equivalent_transcript_ids" not in solo


def _missense_isoforms() -> dict[str, Any]:
    base = {
        "gene_symbol": "COL4A5",
        "consequence_terms": ["missense_variant"],
        "impact": "MODERATE",
        "hgvsc": "x:c.1871G>A",
        "hgvsp": "p.Gly624Asp",
        "protein_position": "624",
        "revel": 0.91,
    }
    return {
        "variant_id": "X-1-G-A",
        "assembly": "GRCh38",
        "most_severe_consequence": "missense_variant",
        "gene_symbol": "COL4A5",
        "seq_region_name": "X",
        "start": 1,
        "end": 1,
        "allele_string": "G/A",
        "position_scores": {},
        "frequencies": [],
        "transcript_consequences": [
            {**base, "transcript_id": "ENST_A", "mane_select": "NM.1"},
            {**base, "transcript_id": "ENST_B"},
            {**base, "transcript_id": "ENST_C"},
            {
                "gene_symbol": "COL4A5",
                "transcript_id": "ENST_D",
                "consequence_terms": ["missense_variant"],
                "impact": "MODERATE",
                "hgvsc": "y:c.1829G>A",
                "hgvsp": "p.Gly610Asp",
                "protein_position": "610",
                "revel": 0.91,
            },
        ],
    }


def test_standard_collapses_identical_and_counts() -> None:
    shaped = shape_annotation(_missense_isoforms(), "standard")
    rows = shaped["transcript_consequences"]
    # A/B/C collapse to one; D stays separate -> 2 shown.
    assert len(rows) == 2
    rep = next(r for r in rows if r["hgvsp"] == "p.Gly624Asp")
    assert rep["transcript_id"] == "ENST_A"
    assert sorted(rep["equivalent_transcript_ids"]) == ["ENST_B", "ENST_C"]
    assert shaped["transcripts_summary"] == {"shown": 2, "collapsed": 2, "total": 4}


def test_standard_all_bypasses_collapse() -> None:
    shaped = shape_annotation(_missense_isoforms(), "standard", transcripts="all")
    assert len(shaped["transcript_consequences"]) == 4
    assert all(
        "equivalent_transcript_ids" not in r for r in shaped["transcript_consequences"]
    )


# --- full ----------------------------------------------------------------


def test_full_equals_data() -> None:
    shaped = shape_annotation(DATA, ResponseMode.FULL)
    assert shaped == DATA


def test_full_includes_colocated_and_full_transcripts() -> None:
    shaped = shape_annotation(DATA, ResponseMode.FULL)
    assert shaped["colocated_variants"] == DATA["colocated_variants"]
    assert shaped["transcript_consequences"] == DATA["transcript_consequences"]


def test_full_does_not_mutate_input() -> None:
    shaped = shape_annotation(DATA, ResponseMode.FULL)
    # Mutating the returned dict must not affect the source.
    shaped["variant_id"] = "MUTATED"
    assert DATA["variant_id"] == "1-1000-A-T"


# --- string-mode coercion ------------------------------------------------


def test_string_mode_compact_matches_enum() -> None:
    assert shape_annotation(DATA, "compact") == shape_annotation(DATA, ResponseMode.COMPACT)


def test_string_mode_minimal_matches_enum() -> None:
    assert shape_annotation(DATA, "minimal") == shape_annotation(DATA, ResponseMode.MINIMAL)


def test_string_mode_standard_matches_enum() -> None:
    assert shape_annotation(DATA, "standard") == shape_annotation(DATA, ResponseMode.STANDARD)


def test_string_mode_full_matches_enum() -> None:
    assert shape_annotation(DATA, "full") == shape_annotation(DATA, ResponseMode.FULL)


def test_invalid_string_mode_raises() -> None:
    with pytest.raises(ValueError):
        shape_annotation(DATA, "bogus")
