"""Tests for vep_link.variant.

Test-first specification of the variant front door. Detection ORDER matters
(rsID -> coordinate -> CNV -> HGVS catch-all), so the tests assert both the
classified ``kind`` and the canonical ``value`` for each shape, plus the VEP
line formatters and the ``needs_recoding`` routing flag.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from vep_link.exceptions import VariantParseError
from vep_link.models.enums import InputKind
from vep_link.variant import (
    VariantInput,
    clean_hgvs,
    cnv_to_vep_line,
    coordinate_to_vep_line,
    needs_recoding,
    parse_variant_input,
)


# --------------------------------------------------------------------------- #
# rsID
# --------------------------------------------------------------------------- #
def test_rsid_lowercased() -> None:
    vi = parse_variant_input("rs123")
    assert vi == VariantInput(kind=InputKind.RSID, value="rs123")


def test_rsid_uppercase_is_lowered() -> None:
    vi = parse_variant_input("RS6025")
    assert vi.kind is InputKind.RSID
    assert vi.value == "rs6025"


def test_rsid_with_surrounding_whitespace() -> None:
    vi = parse_variant_input("  rs6025  ")
    assert vi == VariantInput(kind=InputKind.RSID, value="rs6025")


# --------------------------------------------------------------------------- #
# Coordinate (VCF 4-token)
# --------------------------------------------------------------------------- #
def test_coordinate_dash_delimited() -> None:
    vi = parse_variant_input("1-65568-A-C")
    assert vi == VariantInput(kind=InputKind.COORDINATE, value="1-65568-A-C")


def test_coordinate_colon_delimited_with_chr_prefix() -> None:
    vi = parse_variant_input("chr1:65568:A:C")
    assert vi == VariantInput(kind=InputKind.COORDINATE, value="1-65568-A-C")


def test_coordinate_whitespace_delimited_lowercase_alleles() -> None:
    vi = parse_variant_input("1 65568 a c")
    assert vi == VariantInput(kind=InputKind.COORDINATE, value="1-65568-A-C")


def test_coordinate_x_chromosome() -> None:
    vi = parse_variant_input("X-100-A-G")
    assert vi == VariantInput(kind=InputKind.COORDINATE, value="X-100-A-G")


def test_coordinate_mt_chromosome_allowed() -> None:
    # M/MT are recognised contigs at the parser level; downstream scope checks
    # (e.g. liftover) decide whether they are usable.
    vi = parse_variant_input("MT-8993-T-G")
    assert vi == VariantInput(kind=InputKind.COORDINATE, value="MT-8993-T-G")


def test_coordinate_multi_base_alleles() -> None:
    vi = parse_variant_input("17-43044295-AG-A")
    assert vi == VariantInput(kind=InputKind.COORDINATE, value="17-43044295-AG-A")


# --------------------------------------------------------------------------- #
# CNV
# --------------------------------------------------------------------------- #
def test_cnv_del() -> None:
    vi = parse_variant_input("7:117559600-117559609:DEL")
    assert vi == VariantInput(kind=InputKind.CNV, value="7:117559600-117559609:DEL")


def test_cnv_type_uppercased() -> None:
    vi = parse_variant_input("7:100-200:dup")
    assert vi == VariantInput(kind=InputKind.CNV, value="7:100-200:DUP")


def test_cnv_chr_prefix_stripped() -> None:
    vi = parse_variant_input("chrX:100-200:DEL")
    assert vi == VariantInput(kind=InputKind.CNV, value="X:100-200:DEL")


@pytest.mark.parametrize("cnv_type", ["DEL", "DUP", "CNV", "INS", "INV", "CUSTOM"])
def test_cnv_all_supported_types(cnv_type: str) -> None:
    vi = parse_variant_input(f"7:100-200:{cnv_type}")
    assert vi.kind is InputKind.CNV
    assert vi.value == f"7:100-200:{cnv_type}"


# --------------------------------------------------------------------------- #
# HGVS (catch-all)
# --------------------------------------------------------------------------- #
def test_hgvs_transcript_coding() -> None:
    vi = parse_variant_input("NM_004006.2:c.4375C>T")
    assert vi == VariantInput(kind=InputKind.HGVS, value="NM_004006.2:c.4375C>T")


def test_hgvs_genomic() -> None:
    vi = parse_variant_input("17:g.43044295G>A")
    assert vi == VariantInput(kind=InputKind.HGVS, value="17:g.43044295G>A")


def test_hgvs_annotations_cleaned_on_parse() -> None:
    vi = parse_variant_input("NM_004006.2(DMD):c.4375C>T (p.Arg1459*)")
    assert vi == VariantInput(kind=InputKind.HGVS, value="NM_004006.2:c.4375C>T")


def test_spdi_falls_through_to_hgvs() -> None:
    # SPDI (NC_000001.11:1000:A:T) is not a recognised coordinate contig token
    # nor an rsID/CNV, so it is treated as HGVS-class input for recoding.
    vi = parse_variant_input("NC_000001.11:1000:A:T")
    assert vi.kind is InputKind.HGVS


def test_garbage_treated_as_hgvs_catch_all() -> None:
    # Documented behavior: non-empty garbage like "???" is NOT an error; it is
    # classified as HGVS (the catch-all) and left for the upstream recoder to
    # reject. Only empty/blank input raises.
    vi = parse_variant_input("???")
    assert vi.kind is InputKind.HGVS
    assert vi.value == "???"


def test_invalid_coordinate_falls_through_to_hgvs() -> None:
    # "1-abc-A-C" has a non-integer position, so it is NOT a valid coordinate.
    # Documented behavior: it falls through to the HGVS catch-all (not an error).
    vi = parse_variant_input("1-abc-A-C")
    assert vi.kind is InputKind.HGVS
    assert vi.value == "1-abc-A-C"


# --------------------------------------------------------------------------- #
# Empty / blank -> VariantParseError
# --------------------------------------------------------------------------- #
def test_empty_string_raises() -> None:
    with pytest.raises(VariantParseError):
        parse_variant_input("")


def test_whitespace_only_raises() -> None:
    with pytest.raises(VariantParseError):
        parse_variant_input("   ")


# --------------------------------------------------------------------------- #
# clean_hgvs
# --------------------------------------------------------------------------- #
def test_clean_hgvs_strips_gene_and_protein() -> None:
    assert clean_hgvs("NM_004006.2(DMD):c.4375C>T (p.Arg1459*)") == "NM_004006.2:c.4375C>T"


def test_clean_hgvs_strips_protein_only() -> None:
    assert clean_hgvs("NM_000123.4:c.10A>T (p.Lys4*)") == "NM_000123.4:c.10A>T"


def test_clean_hgvs_strips_gene_only() -> None:
    assert clean_hgvs("NM_001089.3(ABCA3):c.875A>T") == "NM_001089.3:c.875A>T"


def test_clean_hgvs_noop_when_clean() -> None:
    assert clean_hgvs("NM_004006.2:c.4375C>T") == "NM_004006.2:c.4375C>T"


def test_clean_hgvs_trims_whitespace() -> None:
    assert clean_hgvs("  NM_004006.2:c.4375C>T  ") == "NM_004006.2:c.4375C>T"


# --------------------------------------------------------------------------- #
# coordinate_to_vep_line
# --------------------------------------------------------------------------- #
def test_coordinate_to_vep_line() -> None:
    assert coordinate_to_vep_line("1-65568-A-C") == "1 65568 . A C . . ."


def test_coordinate_to_vep_line_x() -> None:
    assert coordinate_to_vep_line("X-100-A-G") == "X 100 . A G . . ."


def test_coordinate_to_vep_line_rejects_malformed() -> None:
    with pytest.raises(VariantParseError):
        coordinate_to_vep_line("1-65568-A")


# --------------------------------------------------------------------------- #
# cnv_to_vep_line
# --------------------------------------------------------------------------- #
def test_cnv_to_vep_line_del() -> None:
    assert cnv_to_vep_line("7:100-200:DEL") == "7 100 200 deletion 1"


@pytest.mark.parametrize(
    ("cnv", "expected"),
    [
        ("7:100-200:DEL", "7 100 200 deletion 1"),
        ("7:100-200:DUP", "7 100 200 duplication 1"),
        ("7:100-200:INS", "7 100 200 insertion 1"),
        ("7:100-200:INV", "7 100 200 inversion 1"),
        ("7:100-200:CNV", "7 100 200 CNV 1"),
        ("7:100-200:CUSTOM", "7 100 200 CNV 1"),
    ],
)
def test_cnv_to_vep_line_type_mapping(cnv: str, expected: str) -> None:
    assert cnv_to_vep_line(cnv) == expected


def test_cnv_to_vep_line_lowercase_type() -> None:
    assert cnv_to_vep_line("7:100-200:del") == "7 100 200 deletion 1"


def test_cnv_to_vep_line_rejects_malformed() -> None:
    with pytest.raises(VariantParseError):
        cnv_to_vep_line("not-a-cnv")


# --------------------------------------------------------------------------- #
# needs_recoding
# --------------------------------------------------------------------------- #
def test_needs_recoding_true_for_hgvs() -> None:
    assert needs_recoding(VariantInput(InputKind.HGVS, "NM_004006.2:c.4375C>T")) is True


def test_needs_recoding_true_for_rsid() -> None:
    assert needs_recoding(VariantInput(InputKind.RSID, "rs6025")) is True


def test_needs_recoding_false_for_coordinate() -> None:
    assert needs_recoding(VariantInput(InputKind.COORDINATE, "1-65568-A-C")) is False


def test_needs_recoding_false_for_cnv() -> None:
    assert needs_recoding(VariantInput(InputKind.CNV, "7:100-200:DEL")) is False


def test_needs_recoding_via_parse() -> None:
    assert needs_recoding(parse_variant_input("rs6025")) is True
    assert needs_recoding(parse_variant_input("1-65568-A-C")) is False
    assert needs_recoding(parse_variant_input("7:100-200:DEL")) is False
    assert needs_recoding(parse_variant_input("NM_004006.2:c.4375C>T")) is True


# --------------------------------------------------------------------------- #
# Frozen dataclass invariant
# --------------------------------------------------------------------------- #
def test_variant_input_is_frozen() -> None:
    vi = VariantInput(InputKind.RSID, "rs1")
    with pytest.raises(FrozenInstanceError):
        vi.value = "rs2"  # type: ignore[misc]
