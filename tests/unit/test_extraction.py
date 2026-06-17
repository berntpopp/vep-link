"""Tests for vep_link.services.extraction.

These cover the pure flatten/prioritize/frequency helpers and the top-level
``build_annotation`` shaper. Fixtures mirror real Ensembl VEP ``region`` payloads
(see ``tests/fixtures/__init__.py``).
"""

from __future__ import annotations

from typing import Any

from tests.fixtures import VEP_REGION_INTERGENIC, VEP_REGION_MISSENSE
from vep_link.services.extraction import (
    build_annotation,
    extract_gnomad_frequencies,
    extract_position_scores,
    flatten_consequences,
    most_severe_transcript,
    prioritize_transcript,
)

MISSENSE = VEP_REGION_MISSENSE[0]
INTERGENIC = VEP_REGION_INTERGENIC[0]


# --- flatten_consequences ------------------------------------------------


def test_flatten_consequences_missense_row_count() -> None:
    rows = flatten_consequences(MISSENSE)
    assert len(rows) == 2


def test_flatten_consequences_row0_fields() -> None:
    rows = flatten_consequences(MISSENSE)
    row = rows[0]
    assert row["gene_symbol"] == "GENE1"
    assert row["gene_id"] == "ENSG00000123456"
    assert row["transcript_id"] == "ENST00000123456"
    assert row["biotype"] == "protein_coding"
    assert row["consequence_terms"] == ["missense_variant"]
    assert row["impact"] == "MODERATE"
    assert row["canonical"] == 1
    assert row["protein_position"] == "34"
    assert row["hgvsp"] == "ENSP00000123456.3:p.Lys34Asn"
    assert row["hgvsc"] == "ENST00000123456.7:c.100A>T"
    assert row["amino_acids"] == "K/N"
    assert row["codons"] == "aAa/aTa"
    assert row["sift_score"] == 0.02
    assert row["sift_prediction"] == "deleterious"
    assert row["polyphen_score"] == 0.95
    assert row["polyphen_prediction"] == "probably_damaging"
    assert row["mane"] == ["MANE_Select"]


def test_flatten_consequences_row0_scoring_fields() -> None:
    # Substitution-specific predictor scores served by the public REST stay on
    # the transcript row (REVEL + AlphaMissense). CADD/GERP are hoisted out (see
    # test_flatten_consequences_omits_hoisted_position_scores).
    row = flatten_consequences(MISSENSE)[0]
    assert row["revel"] == 0.84
    # AlphaMissense's nested object is flattened to two scalar columns.
    assert row["am_pathogenicity"] == 0.92
    assert row["am_class"] == "pathogenic"


def test_flatten_consequences_row1_missing_fields_default_none() -> None:
    rows = flatten_consequences(MISSENSE)
    row = rows[1]
    assert row["transcript_id"] == "ENST00000999999"
    assert row["impact"] == "MODIFIER"
    # Missing optional fields are present and None.
    assert row["hgvsp"] is None
    assert row["sift_score"] is None
    assert row["protein_position"] is None
    # Substitution-specific scoring fields absent on this transcript -> None.
    assert row["revel"] is None
    assert row["am_pathogenicity"] is None
    assert row["am_class"] is None
    # CADD/GERP are hoisted to position_scores, never on the row.
    assert "cadd_raw" not in row
    assert "conservation" not in row


def test_flatten_consequences_alphamissense_non_dict_is_none() -> None:
    # A malformed / absent alphamissense field must not raise.
    record = {
        "transcript_consequences": [
            {"transcript_id": "ENST1", "alphamissense": "unexpected"},
            {"transcript_id": "ENST2"},
        ]
    }
    rows = flatten_consequences(record)
    assert rows[0]["am_pathogenicity"] is None
    assert rows[0]["am_class"] is None
    assert rows[1]["am_pathogenicity"] is None


def test_flatten_consequences_intergenic_is_empty() -> None:
    assert flatten_consequences(INTERGENIC) == []


def test_flatten_consequences_missing_key_is_empty() -> None:
    assert flatten_consequences({}) == []


def test_flatten_consequences_protein_position_range() -> None:
    record = {
        "transcript_consequences": [
            {"transcript_id": "ENST1", "protein_start": 10, "protein_end": 12}
        ]
    }
    assert flatten_consequences(record)[0]["protein_position"] == "10-12"


def test_flatten_consequences_protein_position_only_start() -> None:
    record = {"transcript_consequences": [{"transcript_id": "ENST1", "protein_start": 7}]}
    assert flatten_consequences(record)[0]["protein_position"] == "7"


def test_flatten_consequences_protein_position_only_end() -> None:
    record = {"transcript_consequences": [{"transcript_id": "ENST1", "protein_end": 9}]}
    assert flatten_consequences(record)[0]["protein_position"] == "9"


def test_flatten_preserves_ranking_fields() -> None:
    # pick/mane_select must survive flattening so the consequence-anchored
    # representative selection can rank on the full signal (not just `mane`).
    record = {
        "transcript_consequences": [
            {
                "transcript_id": "ENST1",
                "gene_symbol": "G",
                "consequence_terms": ["x"],
                "pick": 1,
                "mane_select": "NM_1.1",
                "canonical": 1,
            },
        ]
    }
    row = flatten_consequences(record)[0]
    assert row["pick"] == 1
    assert row["mane_select"] == "NM_1.1"


# --- most_severe_transcript ----------------------------------------------


def test_most_severe_transcript_picks_matching_term() -> None:
    tc = most_severe_transcript(MISSENSE)
    assert tc is not None
    assert tc["transcript_id"] == "ENST00000123456"


def test_most_severe_transcript_intergenic_is_none() -> None:
    assert most_severe_transcript(INTERGENIC) is None


def test_most_severe_transcript_fallback_first() -> None:
    record = {
        "most_severe_consequence": "not_present",
        "transcript_consequences": [
            {"transcript_id": "A", "consequence_terms": ["x"]},
            {"transcript_id": "B", "consequence_terms": ["y"]},
        ],
    }
    tc = most_severe_transcript(record)
    assert tc is not None
    assert tc["transcript_id"] == "A"


# --- prioritize_transcript -----------------------------------------------


def test_prioritize_transcript_empty_is_none() -> None:
    assert prioritize_transcript([]) is None


def test_prioritize_transcript_canonical_mane() -> None:
    rows = flatten_consequences(MISSENSE)
    chosen = prioritize_transcript(rows)
    assert chosen is not None
    assert chosen["transcript_id"] == "ENST00000123456"


def test_prioritize_transcript_pick_beats_canonical() -> None:
    transcripts: list[dict[str, Any]] = [
        {"transcript_id": "CANON", "canonical": 1},
        {"transcript_id": "PICKED", "pick": 1},
    ]
    chosen = prioritize_transcript(transcripts)
    assert chosen is not None
    assert chosen["transcript_id"] == "PICKED"


def test_prioritize_transcript_mane_beats_canonical() -> None:
    transcripts: list[dict[str, Any]] = [
        {"transcript_id": "CANON", "canonical": 1},
        {"transcript_id": "MANE", "mane_select": "NM_1"},
    ]
    chosen = prioritize_transcript(transcripts)
    assert chosen is not None
    assert chosen["transcript_id"] == "MANE"


def test_prioritize_transcript_falls_back_to_first() -> None:
    transcripts: list[dict[str, Any]] = [
        {"transcript_id": "FIRST"},
        {"transcript_id": "SECOND"},
    ]
    chosen = prioritize_transcript(transcripts)
    assert chosen is not None
    assert chosen["transcript_id"] == "FIRST"


# --- select_representative (consequence-anchored) ------------------------


def test_select_representative_anchors_on_most_severe() -> None:
    from vep_link.services.extraction import select_representative

    rows = [
        # Neighbour gene, canonical, but only a MODIFIER consequence.
        {
            "transcript_id": "T_TSC2",
            "gene_symbol": "TSC2",
            "consequence_terms": ["downstream_gene_variant"],
            "canonical": 1,
        },
        # Queried gene, carries the most-severe consequence, not canonical.
        {
            "transcript_id": "T_PKD1",
            "gene_symbol": "PKD1",
            "consequence_terms": ["stop_gained"],
        },
    ]
    chosen = select_representative(rows, "stop_gained")
    assert chosen is not None
    assert chosen["gene_symbol"] == "PKD1"


def test_select_representative_prefers_mane_within_subset() -> None:
    from vep_link.services.extraction import select_representative

    rows = [
        {"transcript_id": "A", "gene_symbol": "G", "consequence_terms": ["missense_variant"]},
        {
            "transcript_id": "B",
            "gene_symbol": "G",
            "consequence_terms": ["missense_variant"],
            "mane_select": "NM_1.1",
        },
    ]
    chosen = select_representative(rows, "missense_variant")
    assert chosen is not None
    assert chosen["transcript_id"] == "B"


def test_select_representative_falls_back_when_no_match() -> None:
    from vep_link.services.extraction import select_representative

    rows = [{"transcript_id": "A", "gene_symbol": "G", "consequence_terms": ["x"], "canonical": 1}]
    # most_severe absent from every row -> fall back to biological ranking over all.
    chosen = select_representative(rows, "not_present")
    assert chosen is not None
    assert chosen["transcript_id"] == "A"
    assert select_representative([], "x") is None


# --- extract_gnomad_frequencies ------------------------------------------


def test_extract_gnomad_frequencies_missense() -> None:
    freqs = extract_gnomad_frequencies(MISSENSE)
    assert freqs == [{"allele": "T", "gnomade": 0.0001234, "gnomadg": 0.0002345}]


def test_extract_gnomad_frequencies_intergenic_empty() -> None:
    assert extract_gnomad_frequencies(INTERGENIC) == []


def test_extract_gnomad_frequencies_missing_one_side() -> None:
    record = {
        "colocated_variants": [
            {"frequencies": {"G": {"gnomadg": 0.5}}},
        ]
    }
    assert extract_gnomad_frequencies(record) == [{"allele": "G", "gnomade": None, "gnomadg": 0.5}]


# --- build_annotation ----------------------------------------------------


def test_build_annotation_missense_shape() -> None:
    ann = build_annotation(MISSENSE, variant_id="1-1000-A-T", assembly="GRCh38")
    assert ann == {
        "variant_id": "1-1000-A-T",
        "assembly": "GRCh38",
        "input": "1 1000 . A T . . .",
        "seq_region_name": "1",
        "start": 1000,
        "end": 1000,
        "allele_string": "A/T",
        "strand": 1,
        "most_severe_consequence": "missense_variant",
        "gene_symbol": "GENE1",
        "position_scores": {"cadd_phred": 25.1, "cadd_raw": 3.214, "conservation": 5.6},
        "transcript_consequences": flatten_consequences(MISSENSE),
        "frequencies": [{"allele": "T", "gnomade": 0.0001234, "gnomadg": 0.0002345}],
        "colocated_variants": MISSENSE["colocated_variants"],
    }


def test_build_annotation_missense_gene_symbol_and_count() -> None:
    ann = build_annotation(MISSENSE, variant_id="1-1000-A-T", assembly="GRCh38")
    assert ann["gene_symbol"] == "GENE1"
    assert len(ann["transcript_consequences"]) == 2


def test_build_annotation_gene_symbol_matches_most_severe() -> None:
    # Contiguous-gene locus: the canonical neighbour (TSC2) carries only a
    # MODIFIER consequence; the worst effect (stop_gained) is on PKD1. The single
    # top-level gene_symbol must name the gene the worst consequence occurs on.
    record = {
        "most_severe_consequence": "stop_gained",
        "transcript_consequences": [
            {
                "transcript_id": "T_TSC2",
                "gene_symbol": "TSC2",
                "consequence_terms": ["downstream_gene_variant"],
                "canonical": 1,
            },
            {
                "transcript_id": "T_PKD1",
                "gene_symbol": "PKD1",
                "consequence_terms": ["stop_gained"],
            },
        ],
    }
    ann = build_annotation(record, variant_id="16-2090952-G-A", assembly="GRCh38")
    assert ann["gene_symbol"] == "PKD1"
    assert ann["most_severe_consequence"] == "stop_gained"


def test_build_annotation_intergenic_empty_fields() -> None:
    ann = build_annotation(INTERGENIC, variant_id="1-2000-C-G", assembly="GRCh38")
    assert ann["gene_symbol"] is None
    assert ann["transcript_consequences"] == []
    assert ann["frequencies"] == []
    assert ann["colocated_variants"] == []
    assert ann["position_scores"] == {}
    assert ann["most_severe_consequence"] == "intergenic_variant"


def test_build_annotation_keys_exact() -> None:
    ann = build_annotation(MISSENSE, variant_id="x", assembly="GRCh38")
    assert set(ann.keys()) == {
        "variant_id",
        "assembly",
        "input",
        "seq_region_name",
        "start",
        "end",
        "allele_string",
        "strand",
        "most_severe_consequence",
        "gene_symbol",
        "position_scores",
        "transcript_consequences",
        "frequencies",
        "colocated_variants",
    }


# --- extract_position_scores (genomic-position scores, hoisted once) ------


def test_extract_position_scores_missense() -> None:
    # CADD (phred/raw) and GERP conservation are genomic-position values:
    # identical across transcripts, so they are hoisted to one variant-level dict.
    assert extract_position_scores(MISSENSE) == {
        "cadd_phred": 25.1,
        "cadd_raw": 3.214,
        "conservation": 5.6,
    }


def test_extract_position_scores_intergenic_is_empty() -> None:
    assert extract_position_scores(INTERGENIC) == {}


def test_extract_position_scores_omits_null_keys() -> None:
    # Only non-null scores are carried (null-stripped); first non-null wins.
    record = {
        "transcript_consequences": [
            {"transcript_id": "A"},
            {"transcript_id": "B", "cadd_phred": 12.0, "conservation": 2.1},
        ]
    }
    assert extract_position_scores(record) == {"cadd_phred": 12.0, "conservation": 2.1}


def test_extract_position_scores_reads_intergenic_consequences() -> None:
    # GERP conservation can live on intergenic_consequences when there are no
    # transcript rows (e.g. a deep-intergenic variant).
    record = {"intergenic_consequences": [{"conservation": 3.3}]}
    assert extract_position_scores(record) == {"conservation": 3.3}


# --- CADD/GERP are no longer duplicated on every transcript row ----------


def test_flatten_consequences_omits_hoisted_position_scores() -> None:
    # cadd_phred / cadd_raw / conservation are position-level: hoisted out of the
    # per-transcript rows so they are not repeated on every transcript.
    row = flatten_consequences(MISSENSE)[0]
    assert "cadd_phred" not in row
    assert "cadd_raw" not in row
    assert "conservation" not in row
    # Substitution-specific scores STAY per-transcript.
    assert row["revel"] == 0.84
    assert row["am_pathogenicity"] == 0.92
    assert row["am_class"] == "pathogenic"


def test_build_annotation_hoists_position_scores() -> None:
    ann = build_annotation(MISSENSE, variant_id="1-1000-A-T", assembly="GRCh38")
    assert ann["position_scores"] == {
        "cadd_phred": 25.1,
        "cadd_raw": 3.214,
        "conservation": 5.6,
    }
    for row in ann["transcript_consequences"]:
        assert "cadd_phred" not in row
        assert "conservation" not in row


def test_build_annotation_intergenic_position_scores_empty() -> None:
    ann = build_annotation(INTERGENIC, variant_id="1-2000-C-G", assembly="GRCh38")
    assert ann["position_scores"] == {}
