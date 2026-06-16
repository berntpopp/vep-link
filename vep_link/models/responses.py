"""Output shape models for vep-link.

These models describe the payloads the service returns to MCP tools. Because the
fields are derived from upstream Ensembl REST responses (VEP ``region``, Variant
Recoder, assembly ``map``), every model is tolerant: ``extra="ignore"`` drops
unknown keys and all upstream-derived fields are optional with sensible defaults.
This keeps the server resilient to Ensembl adding or omitting fields.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TranscriptConsequence(BaseModel):
    """A single VEP transcript consequence (per-transcript effect of a variant)."""

    model_config = ConfigDict(extra="ignore")

    gene_id: str | None = None
    gene_symbol: str | None = None
    transcript_id: str | None = None
    biotype: str | None = None
    consequence_terms: list[str] = Field(default_factory=list)
    impact: str | None = None
    canonical: bool | int | None = None
    mane: list[str] | None = None
    hgvsc: str | None = None
    hgvsp: str | None = None
    amino_acids: str | None = None
    codons: str | None = None
    protein_position: str | None = None
    sift_score: float | None = None
    sift_prediction: str | None = None
    polyphen_score: float | None = None
    polyphen_prediction: str | None = None
    cadd_phred: float | None = None


class GnomadFrequency(BaseModel):
    """gnomAD allele frequencies for a single alternate allele."""

    model_config = ConfigDict(extra="ignore")

    allele: str | None = None
    gnomade: float | None = None
    gnomadg: float | None = None


class VariantAnnotation(BaseModel):
    """A fully annotated variant assembled from a VEP ``region`` record."""

    model_config = ConfigDict(extra="ignore")

    variant_id: str
    assembly: str
    seq_region_name: str | None = None
    start: int | None = None
    end: int | None = None
    allele_string: str | None = None
    most_severe_consequence: str | None = None
    gene_symbol: str | None = None
    transcript_consequences: list[TranscriptConsequence] = Field(default_factory=list)
    frequencies: list[GnomadFrequency] = Field(default_factory=list)


class RecodingResult(BaseModel):
    """A single Variant Recoder result (notations available for one input)."""

    model_config = ConfigDict(extra="ignore")

    input: str
    id: str | None = None
    vcf_string: list[str] = Field(default_factory=list)
    hgvsg: list[str] = Field(default_factory=list)
    hgvsc: list[str] = Field(default_factory=list)
    hgvsp: list[str] = Field(default_factory=list)
    spdi: list[str] = Field(default_factory=list)


class LiftoverResult(BaseModel):
    """The outcome of lifting a variant between two assemblies."""

    model_config = ConfigDict(extra="ignore")

    input: str
    from_assembly: str
    to_assembly: str
    lifted: str | None = None
    mapped_region: str | None = None
