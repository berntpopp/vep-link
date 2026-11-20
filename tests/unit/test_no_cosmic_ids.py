"""COSMIC identifiers must never leave vep-link.

COSMIC's terms (2025-10-30) bar its data, including bare COSV/COSM/COSN ids, on
any free-to-access platform. Ensembl VEP REST still returns COSMIC co-located
variants (``allele_string: COSMIC_MUTATION``, ``var_synonyms.COSMIC``), so the
fixture below is a trimmed live ``rest.ensembl.org`` BRAF V600E (7:140753336 A>T,
GRCh38) record. No response mode may carry a ``COS[VMN]<digits>`` token.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from vep_link.mcp.shaping import shape_annotation
from vep_link.models.enums import ResponseMode
from vep_link.services.extraction import build_annotation

_COSMIC_ID = re.compile(r"COS[VMN]\d+")


def _cosmic(cosv: str, cosm: str) -> dict[str, Any]:
    return {
        "id": cosv,
        "allele_string": "COSMIC_MUTATION",
        "seq_region_name": "7",
        "start": 140753336,
        "end": 140753336,
        "strand": 1,
        "somatic": 1,
        "phenotype_or_disease": 1,
        "var_synonyms": {"COSMIC": [cosm]},
    }


BRAF_V600E: dict[str, Any] = {
    "input": "7 140753336 140753336 A/T 1",
    "seq_region_name": "7",
    "start": 140753336,
    "end": 140753336,
    "allele_string": "A/T",
    "strand": 1,
    "most_severe_consequence": "missense_variant",
    "transcript_consequences": [
        {
            "gene_symbol": "BRAF",
            "gene_id": "ENSG00000157764",
            "transcript_id": "ENST00000646891",
            "consequence_terms": ["missense_variant"],
            "impact": "MODERATE",
            "mane_select": "NM_004333.6",
            "canonical": 1,
            "hgvsp": "ENSP00000493543.1:p.Val600Glu",
            "cadd_phred": 28.1,
        }
    ],
    "colocated_variants": [
        _cosmic("COSV56056643", "COSM476"),
        _cosmic("COSV56065204", "COSM18443"),
        {
            "id": "rs113488022",
            "allele_string": "A/C/G/T",
            "seq_region_name": "7",
            "start": 140753336,
            "end": 140753336,
            "strand": 1,
            "clin_sig_allele": "T:pathogenic",
            "frequencies": {"T": {"gnomade": 3.98e-06}},
            # defensive: a COSMIC synonym bucket on a non-COSMIC entry
            "var_synonyms": {"ClinVar": ["RCV000013926"], "COSMIC": ["COSM476"]},
        },
    ],
}


@pytest.mark.parametrize("mode", list(ResponseMode))
def test_no_cosmic_id_in_any_response_mode(mode: ResponseMode) -> None:
    data = build_annotation(BRAF_V600E, variant_id="7-140753336-A-T", assembly="GRCh38")
    shaped = json.dumps(shape_annotation(data, mode))
    assert not _COSMIC_ID.search(shaped), _COSMIC_ID.findall(shaped)


def test_non_cosmic_colocated_variant_survives() -> None:
    data = build_annotation(BRAF_V600E, variant_id="7-140753336-A-T", assembly="GRCh38")
    shaped = shape_annotation(data, ResponseMode.FULL)
    assert [c["id"] for c in shaped["colocated_variants"]] == ["rs113488022"]
    assert shaped["colocated_variants"][0]["var_synonyms"] == {"ClinVar": ["RCV000013926"]}
