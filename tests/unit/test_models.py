"""Tests for vep_link.models request and response models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tests.fixtures import VEP_REGION_MISSENSE
from vep_link.models import (
    AnnotateRequest,
    BatchAnnotateRequest,
    GnomadFrequency,
    LiftoverRequest,
    LiftoverResult,
    RecodeRequest,
    RecodingResult,
    ResolveRequest,
    TranscriptConsequence,
    VariantAnnotation,
)

# --- Request models -------------------------------------------------------


def test_resolve_request_valid() -> None:
    req = ResolveRequest(variant="rs123")
    assert req.variant == "rs123"
    assert req.assembly == "GRCh38"


def test_resolve_request_assembly_grch37() -> None:
    req = ResolveRequest(variant="1-1000-A-T", assembly="GRCh37")
    assert req.assembly == "GRCh37"


def test_resolve_request_strips_variant() -> None:
    req = ResolveRequest(variant="  rs123  ")
    assert req.variant == "rs123"


def test_resolve_request_empty_variant_raises() -> None:
    with pytest.raises(ValidationError):
        ResolveRequest(variant="   ")


def test_resolve_request_invalid_assembly_raises() -> None:
    with pytest.raises(ValidationError):
        ResolveRequest(variant="rs123", assembly="hg38")


def test_recode_request_valid() -> None:
    req = RecodeRequest(variants=["rs1", "rs2"])
    assert req.variants == ["rs1", "rs2"]
    assert req.assembly == "GRCh38"
    assert req.fields is None


def test_recode_request_coerces_single_string() -> None:
    req = RecodeRequest(variants="rs1")
    assert req.variants == ["rs1"]


def test_recode_request_with_fields() -> None:
    req = RecodeRequest(variants=["rs1"], fields="hgvsg,spdi")
    assert req.fields == "hgvsg,spdi"


def test_recode_request_empty_list_raises() -> None:
    with pytest.raises(ValidationError):
        RecodeRequest(variants=[])


def test_recode_request_blank_string_item_raises() -> None:
    with pytest.raises(ValidationError):
        RecodeRequest(variants=["rs1", "   "])


def test_recode_request_over_200_raises() -> None:
    with pytest.raises(ValidationError):
        RecodeRequest(variants=[f"rs{i}" for i in range(201)])


def test_annotate_request_valid() -> None:
    req = AnnotateRequest(variant="rs123")
    assert req.variant == "rs123"
    assert req.assembly == "GRCh38"
    assert req.response_mode == "compact"
    assert req.vep_options is None


def test_annotate_request_response_mode_and_options() -> None:
    req = AnnotateRequest(variant="rs123", response_mode="full", vep_options={"CADD": "1"})
    assert req.response_mode == "full"
    assert req.vep_options == {"CADD": "1"}


def test_annotate_request_empty_variant_raises() -> None:
    with pytest.raises(ValidationError):
        AnnotateRequest(variant="")


def test_annotate_request_invalid_response_mode_raises() -> None:
    with pytest.raises(ValidationError):
        AnnotateRequest(variant="rs123", response_mode="verbose")


def test_batch_annotate_request_valid() -> None:
    req = BatchAnnotateRequest(variants=["rs1", "rs2"])
    assert req.variants == ["rs1", "rs2"]
    assert req.assembly == "GRCh38"
    assert req.response_mode == "compact"


def test_batch_annotate_request_empty_list_raises() -> None:
    with pytest.raises(ValidationError):
        BatchAnnotateRequest(variants=[])


def test_batch_annotate_request_201_items_raises() -> None:
    with pytest.raises(ValidationError):
        BatchAnnotateRequest(variants=[f"rs{i}" for i in range(201)])


def test_batch_annotate_request_200_items_ok() -> None:
    req = BatchAnnotateRequest(variants=[f"rs{i}" for i in range(200)])
    assert len(req.variants) == 200


def test_liftover_request_valid() -> None:
    req = LiftoverRequest(variant="1-1000-A-T", from_assembly="GRCh37", to_assembly="GRCh38")
    assert req.from_assembly == "GRCh37"
    assert req.to_assembly == "GRCh38"


def test_liftover_request_same_assembly_raises() -> None:
    with pytest.raises(ValidationError):
        LiftoverRequest(variant="1-1000-A-T", from_assembly="GRCh38", to_assembly="GRCh38")


def test_liftover_request_empty_variant_raises() -> None:
    with pytest.raises(ValidationError):
        LiftoverRequest(variant="  ", from_assembly="GRCh37", to_assembly="GRCh38")


# --- Response models ------------------------------------------------------


def test_transcript_consequence_defaults_and_roundtrip() -> None:
    tc = TranscriptConsequence()
    dumped = tc.model_dump()
    assert dumped["gene_id"] is None
    assert dumped["consequence_terms"] == []
    assert dumped["canonical"] is None


def test_transcript_consequence_full_roundtrip() -> None:
    src = VEP_REGION_MISSENSE[0]["transcript_consequences"][0]
    tc = TranscriptConsequence(**src)
    dumped = tc.model_dump()
    assert dumped["gene_symbol"] == "GENE1"
    assert dumped["consequence_terms"] == ["missense_variant"]
    assert dumped["sift_score"] == 0.02
    assert dumped["canonical"] == 1


def test_gnomad_frequency_roundtrip() -> None:
    freq = GnomadFrequency(allele="T", gnomade=0.0001, gnomadg=0.0002)
    dumped = freq.model_dump()
    assert dumped == {"allele": "T", "gnomade": 0.0001, "gnomadg": 0.0002}


def test_gnomad_frequency_defaults() -> None:
    freq = GnomadFrequency()
    assert freq.allele is None
    assert freq.gnomade is None
    assert freq.gnomadg is None


def test_variant_annotation_roundtrip() -> None:
    ann = VariantAnnotation(
        variant_id="1-1000-A-T",
        assembly="GRCh38",
        seq_region_name="1",
        start=1000,
        end=1000,
        allele_string="A/T",
        most_severe_consequence="missense_variant",
        gene_symbol="GENE1",
        transcript_consequences=[TranscriptConsequence(gene_symbol="GENE1")],
        frequencies=[GnomadFrequency(allele="T", gnomade=0.0001)],
    )
    dumped = ann.model_dump()
    assert dumped["variant_id"] == "1-1000-A-T"
    assert dumped["transcript_consequences"][0]["gene_symbol"] == "GENE1"
    assert dumped["frequencies"][0]["allele"] == "T"


def test_variant_annotation_defaults() -> None:
    ann = VariantAnnotation(variant_id="1-1000-A-T", assembly="GRCh38")
    assert ann.transcript_consequences == []
    assert ann.frequencies == []
    assert ann.start is None
    assert ann.most_severe_consequence is None


def test_variant_annotation_from_fixture_subset_ignores_extra() -> None:
    record = VEP_REGION_MISSENSE[0]
    subset = {
        "variant_id": record["id"],
        "assembly": record["assembly_name"],
        "seq_region_name": record["seq_region_name"],
        "start": record["start"],
        "end": record["end"],
        "allele_string": record["allele_string"],
        "most_severe_consequence": record["most_severe_consequence"],
        # Extra keys (input, strand, colocated_variants) must be ignored.
        "input": record["input"],
        "strand": record["strand"],
        "colocated_variants": record["colocated_variants"],
    }
    ann = VariantAnnotation(**subset)
    assert ann.variant_id == "1_1000_A/T"
    assert ann.assembly == "GRCh38"
    assert ann.most_severe_consequence == "missense_variant"
    # Extra key did not leak into the dump.
    assert "input" not in ann.model_dump()


def test_recoding_result_roundtrip() -> None:
    res = RecodingResult(
        input="rs123",
        id="rs123",
        vcf_string=["1-1000-A-T"],
        hgvsg=["NC_000001.11:g.1000A>T"],
    )
    dumped = res.model_dump()
    assert dumped["input"] == "rs123"
    assert dumped["vcf_string"] == ["1-1000-A-T"]
    assert dumped["hgvsc"] == []
    assert dumped["spdi"] == []


def test_recoding_result_defaults() -> None:
    res = RecodingResult(input="rs123")
    assert res.id is None
    assert res.vcf_string == []
    assert res.hgvsg == []


def test_liftover_result_roundtrip() -> None:
    res = LiftoverResult(
        input="1-1000-A-T",
        from_assembly="GRCh37",
        to_assembly="GRCh38",
        lifted="1-1064-A-T",
        mapped_region="1:1064-1064",
    )
    dumped = res.model_dump()
    assert dumped == {
        "input": "1-1000-A-T",
        "from_assembly": "GRCh37",
        "to_assembly": "GRCh38",
        "lifted": "1-1064-A-T",
        "mapped_region": "1:1064-1064",
    }


def test_liftover_result_defaults() -> None:
    res = LiftoverResult(input="1-1000-A-T", from_assembly="GRCh37", to_assembly="GRCh38")
    assert res.lifted is None
    assert res.mapped_region is None
