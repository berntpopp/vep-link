"""Unit tests for the pure recoder-payload helpers in ``services/_recoding``.

These pin the canonicalization + aggregation logic the orchestration service
relies on, independent of any HTTP. Payload shapes mirror real Ensembl Variant
Recoder replies (per-allele objects keyed by ALT letter).
"""

from __future__ import annotations

from vep_link.services._recoding import aggregate_recode_entry


def test_aggregate_omits_empty_fields() -> None:
    # Only fields actually present in the recoder reply are emitted; empty arrays
    # are dropped so the recode view never ships keys mapping to [].
    entry = {
        "input": "rs6025",
        "id": "rs6025",
        "A": {
            "vcf_string": ["1-169549811-C-A"],
            "hgvsg": [],
            "hgvsc": [],
            "hgvsp": [],
            "spdi": [],
        },
    }
    out = aggregate_recode_entry(entry)
    assert out["vcf_string"] == ["1-169549811-C-A"]
    assert "hgvsg" not in out
    assert "hgvsc" not in out
    assert "spdi" not in out
    assert out["input"] == "rs6025"
    assert out["id"] == "rs6025"


def test_aggregate_keeps_input_id_even_when_no_alleles() -> None:
    out = aggregate_recode_entry({"input": "x", "id": None})
    assert out == {"input": "x", "id": None}
