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

# The key set every projected transcript (representative or in the standard
# list) must carry.
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
    "cadd_phred",
    "revel",
    "am_pathogenicity",
    "am_class",
    "conservation",
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
    assert set(rep.keys()) == _REP_KEYS
    assert rep["gene_symbol"] == "GENE1"
    assert rep["cadd_phred"] == 25.1
    assert rep["transcript_id"] == "ENST00000123456"


def test_compact_representative_carries_pathogenicity_scores() -> None:
    # The default (compact) mode must already expose the headline predictors so
    # an interpreter does not have to widen to standard/full to see them.
    rep = shape_annotation(DATA, ResponseMode.COMPACT)["representative_transcript"]
    assert rep["revel"] == 0.84
    assert rep["am_pathogenicity"] == 0.92
    assert rep["am_class"] == "pathogenic"
    assert rep["conservation"] == 5.6


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


def test_standard_transcript_consequences_count() -> None:
    shaped = shape_annotation(DATA, ResponseMode.STANDARD)
    assert len(shaped["transcript_consequences"]) == 2


def test_standard_transcripts_are_projected() -> None:
    shaped = shape_annotation(DATA, ResponseMode.STANDARD)
    for tc in shaped["transcript_consequences"]:
        assert set(tc.keys()) == _REP_KEYS


def test_standard_includes_frequencies_and_position() -> None:
    shaped = shape_annotation(DATA, ResponseMode.STANDARD)
    assert shaped["frequencies"] == [{"allele": "T", "gnomade": 0.0001234, "gnomadg": 0.0002345}]
    assert shaped["seq_region_name"] == "1"
    assert shaped["start"] == 1000


def test_standard_omits_representative_and_colocated() -> None:
    shaped = shape_annotation(DATA, ResponseMode.STANDARD)
    assert "representative_transcript" not in shaped
    assert "colocated_variants" not in shaped


def test_standard_empty_transcripts() -> None:
    shaped = shape_annotation(_empty_annotation(), ResponseMode.STANDARD)
    assert shaped["transcript_consequences"] == []


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
