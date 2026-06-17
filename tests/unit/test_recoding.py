"""Unit tests for the pure recoder-payload helpers in ``services/_recoding``.

These pin the canonicalization + aggregation logic the orchestration service
relies on, independent of any HTTP. Payload shapes mirror real Ensembl Variant
Recoder replies (per-allele objects keyed by ALT letter).
"""

from __future__ import annotations

from vep_link.services._recoding import (
    aggregate_recode_entry,
    canonical_vcf_strings,
    first_canonical_vcf_string,
)


def test_canonical_vcf_strings_returns_sorted_distinct_alts() -> None:
    # A multi-allelic recoder reply yields every distinct canonical alt, sorted
    # deterministically (so a multi-allelic input resolves identically each call).
    payload = [
        {
            "input": "rs6025",
            "id": "rs6025",
            "T": {"vcf_string": ["1-169549811-C-T"]},
            "A": {"vcf_string": ["1-169549811-C-A"]},
        }
    ]
    assert canonical_vcf_strings(payload) == ["1-169549811-C-A", "1-169549811-C-T"]


def test_canonical_vcf_strings_dedups_and_ignores_non_canonical() -> None:
    payload = [
        {
            "A": {"vcf_string": ["1-100-C-A", "1-100-C-A", "not-a-vcf"]},
            "B": {"vcf_string": ["1-100-C-A"]},
        }
    ]
    assert canonical_vcf_strings(payload) == ["1-100-C-A"]


def test_canonical_vcf_strings_empty_when_none() -> None:
    assert canonical_vcf_strings([{"input": "x", "A": {"hgvsg": []}}]) == []
    assert canonical_vcf_strings("not-a-list") == []


def test_first_canonical_delegates_to_sorted_first() -> None:
    payload = [{"T": {"vcf_string": ["1-100-C-T"]}, "A": {"vcf_string": ["1-100-C-A"]}}]
    # Deterministic: the alphabetically-first canonical, not dict-iteration order.
    assert first_canonical_vcf_string(payload) == "1-100-C-A"


def test_first_canonical_none_when_empty() -> None:
    assert first_canonical_vcf_string([]) is None


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


def test_aggregate_recode_entry_uses_input_override() -> None:
    # The recoder POST entry's own `input` comes back null at runtime, so the
    # caller's original query is echoed via input_override instead.
    entry = {"input": None, "id": "rs1", "A": {"hgvsg": ["NC:g.1A>T"]}}
    result = aggregate_recode_entry(entry, input_override="NM_1.1:c.1A>T")
    assert result["input"] == "NM_1.1:c.1A>T"
    assert result["id"] == "rs1"
    assert result["hgvsg"] == ["NC:g.1A>T"]
