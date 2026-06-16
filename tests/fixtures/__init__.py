"""Canned Ensembl REST payloads for deterministic, network-free tests.

Shapes mirror the real Ensembl Variant Recoder and VEP ``region`` responses as
captured by the upstream ``variant-linker`` project's test helpers. Keep these in
sync with the response shapes the client and extraction code consume.
"""

from __future__ import annotations

from typing import Any

# --- Variant Recoder: GET /variant_recoder/human/{id}?vcf_string=1 ---
# Keyed by allele letter; each allele carries hgvsg + vcf_string arrays.
RECODER_GET_RS123: list[dict[str, Any]] = [
    {
        "id": "rs123",
        "A": {
            "hgvsg": ["NC_000001.11:g.1000A>T"],
            "vcf_string": ["1-1000-A-T"],
        },
        "T": {
            "hgvsg": ["NC_000001.11:g.1000A>G"],
            "vcf_string": ["1-1000-A-G"],
        },
    }
]

# --- Variant Recoder: POST /variant_recoder/homo_sapiens (body {"ids": [...]}) ---
# Array, one entry per input, each with input/id plus per-allele objects.
RECODER_POST_BATCH: list[dict[str, Any]] = [
    {
        "input": "rs123",
        "id": "rs123",
        "A": {"hgvsg": ["NC_000001.11:g.1000A>T"], "vcf_string": ["1-1000-A-T"]},
        "T": {"hgvsg": ["NC_000001.11:g.1000A>G"], "vcf_string": ["1-1000-A-G"]},
    },
    {
        "input": "NM_004006.2:c.4375C>T",
        "id": None,
        "T": {
            "hgvsg": ["NC_000023.11:g.32389644G>A"],
            "vcf_string": ["X-32389644-G-A"],
        },
    },
]

# --- VEP: POST /vep/homo_sapiens/region (body {"variants": ["1 1000 . A T . . ."]}) ---
VEP_REGION_MISSENSE: list[dict[str, Any]] = [
    {
        "input": "1 1000 . A T . . .",
        "id": "1_1000_A/T",
        "assembly_name": "GRCh38",
        "seq_region_name": "1",
        "start": 1000,
        "end": 1000,
        "allele_string": "A/T",
        "strand": 1,
        "most_severe_consequence": "missense_variant",
        "transcript_consequences": [
            {
                "gene_id": "ENSG00000123456",
                "gene_symbol": "GENE1",
                "transcript_id": "ENST00000123456",
                "biotype": "protein_coding",
                "consequence_terms": ["missense_variant"],
                "impact": "MODERATE",
                "canonical": 1,
                "mane_select": "NM_004006.2",
                "mane": ["MANE_Select"],
                "hgvsc": "ENST00000123456.7:c.100A>T",
                "hgvsp": "ENSP00000123456.3:p.Lys34Asn",
                "amino_acids": "K/N",
                "codons": "aAa/aTa",
                "protein_start": 34,
                "protein_end": 34,
                "sift_score": 0.02,
                "sift_prediction": "deleterious",
                "polyphen_score": 0.95,
                "polyphen_prediction": "probably_damaging",
                "cadd_phred": 25.1,
            },
            {
                "gene_id": "ENSG00000123456",
                "gene_symbol": "GENE1",
                "transcript_id": "ENST00000999999",
                "biotype": "protein_coding",
                "consequence_terms": ["upstream_gene_variant"],
                "impact": "MODIFIER",
                "canonical": 0,
                "cadd_phred": 25.1,
            },
        ],
        "colocated_variants": [
            {
                "id": "rs123",
                "seq_region_name": "1",
                "start": 1000,
                "allele_string": "A/T",
                "frequencies": {
                    "T": {
                        "gnomade": 0.0001234,
                        "gnomadg": 0.0002345,
                        "af": 0.0001,
                    }
                },
            }
        ],
    }
]

# A second, intergenic variant with no transcript_consequences (edge case).
VEP_REGION_INTERGENIC: list[dict[str, Any]] = [
    {
        "input": "1 2000 . C G . . .",
        "assembly_name": "GRCh38",
        "seq_region_name": "1",
        "start": 2000,
        "end": 2000,
        "allele_string": "C/G",
        "strand": 1,
        "most_severe_consequence": "intergenic_variant",
        "intergenic_consequences": [
            {"consequence_terms": ["intergenic_variant"], "impact": "MODIFIER"}
        ],
    }
]

# --- Assembly map: GET /map/human/GRCh37/{region}/GRCh38 ---
ASSEMBLY_MAP_ONE: dict[str, Any] = {
    "mappings": [
        {
            "original": {
                "seq_region_name": "1",
                "start": 1000,
                "end": 1000,
                "strand": 1,
                "assembly": "GRCh37",
            },
            "mapped": {
                "seq_region_name": "1",
                "start": 1064,
                "end": 1064,
                "strand": 1,
                "assembly": "GRCh38",
            },
        }
    ]
}

ASSEMBLY_MAP_NONE: dict[str, Any] = {"mappings": []}

ASSEMBLY_MAP_AMBIGUOUS: dict[str, Any] = {
    "mappings": [
        {
            "original": {"seq_region_name": "1", "start": 1000, "end": 1000},
            "mapped": {"seq_region_name": "1", "start": 1064, "end": 1064},
        },
        {
            "original": {"seq_region_name": "1", "start": 1000, "end": 1000},
            "mapped": {"seq_region_name": "1", "start": 9999, "end": 9999},
        },
    ]
}
