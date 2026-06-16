"""Variant input parsing and normalization (pure, no I/O).

This is the front door of the pipeline. Every raw input the user or website
supplies -- dash/colon/whitespace coordinates, CNV ranges, transcript or
genomic HGVS, SPDI, and rsIDs -- is classified here and normalized toward the
canonical forms the rest of the pipeline expects:

- ``COORDINATE`` -> ``"CHR-POS-REF-ALT"`` (fed directly to VEP region)
- ``CNV``        -> ``"CHR:START-END:TYPE"`` (fed to VEP region as a CNV line)
- ``HGVS``/``RSID`` -> cleaned input string (routed through Variant Recoder)

Classification is *order-sensitive* (first match wins): rsID, then the
unambiguous 4-token coordinate, then CNV, then an HGVS catch-all. The catch-all
means non-empty garbage is classified as HGVS and left for the upstream recoder
to reject; only empty/blank input raises here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from vep_link.exceptions import VariantParseError
from vep_link.models.enums import InputKind

# Recognised contigs at the parser level. M/MT parse as coordinates; downstream
# scope checks (e.g. liftover) decide whether they are usable.
_VALID_CHROMS = {str(i) for i in range(1, 23)} | {"X", "Y", "M", "MT"}

_RSID_RE = re.compile(r"^rs\d+$", re.IGNORECASE)
_ALLELE_RE = re.compile(r"^[ACGT]+$")
# CNV: chr:start-end:TYPE (chr already stripped of a leading "chr").
_CNV_RE = re.compile(
    r"^(?P<chrom>[0-9XYM]+):(?P<start>\d+)-(?P<end>\d+):" r"(?P<type>DEL|DUP|CNV|INS|INV|CUSTOM)$",
    re.IGNORECASE,
)
_CHR_PREFIX_RE = re.compile(r"^chr", re.IGNORECASE)

# CNV type -> the token VEP's region endpoint expects for a structural variant.
_CNV_TYPE_TO_VEP = {
    "DEL": "deletion",
    "DUP": "duplication",
    "INS": "insertion",
    "INV": "inversion",
    "CNV": "CNV",
    "CUSTOM": "CNV",
}


@dataclass(frozen=True)
class VariantInput:
    """A classified variant input.

    ``value`` carries the canonical form for the kind: ``COORDINATE`` ->
    ``"CHR-POS-REF-ALT"``; ``CNV`` -> ``"CHR:START-END:TYPE"``; ``HGVS`` and
    ``RSID`` -> the cleaned input string to hand to Variant Recoder.
    """

    kind: InputKind
    value: str


def clean_hgvs(text: str) -> str:
    """Strip website-style annotations the VEP/recoder endpoints do not want.

    ``NM_004006.2(DMD):c.4375C>T (p.Arg1459*)`` -> ``NM_004006.2:c.4375C>T``.
    Removes a trailing ``(p.…)`` protein annotation and a ``(GENE)``
    parenthetical that precedes the ``:`` change separator, then trims.
    """
    t = text.strip()
    # Drop a trailing protein annotation in parentheses, e.g. " (p.Arg1459*)".
    t = re.sub(r"\s*\(p\.[^)]*\)\s*$", "", t, flags=re.IGNORECASE)
    # Drop a gene name in parentheses between the transcript and the colon,
    # e.g. "NM_004006.2(DMD):c..." -> "NM_004006.2:c...".
    t = re.sub(r"\(([^)]*)\)(?=\s*:)", "", t)
    return t.strip()


def _coordinate_tokens(text: str) -> tuple[str, int, str, str] | None:
    """Return (chrom, pos, ref, alt) for a 4-token coordinate input, else None.

    Splits on any run of dash / colon / whitespace, strips a leading ``chr``,
    upper-cases the chrom, and validates an integer pos >= 1 and ACGT ref/alt.
    The contig is *not* validated here; the caller checks ``_VALID_CHROMS``.
    """
    tokens = re.split(r"[\s:\-]+", text.strip())
    if len(tokens) != 4:
        return None
    chrom, pos, ref, alt = tokens
    chrom = _CHR_PREFIX_RE.sub("", chrom).upper()
    if not pos.isdigit() or int(pos) < 1:
        return None
    ref_u, alt_u = ref.upper(), alt.upper()
    if not _ALLELE_RE.match(ref_u) or not _ALLELE_RE.match(alt_u):
        return None
    return chrom, int(pos), ref_u, alt_u


def parse_variant_input(text: str) -> VariantInput:
    """Classify and normalize a raw variant string.

    Resolution order (first match wins): rsID -> 4-token coordinate -> CNV ->
    HGVS catch-all. Raises :class:`VariantParseError` only for empty/blank input
    (or input that cleans to nothing); any other non-empty string is treated as
    HGVS for the upstream recoder to validate.
    """
    if text is None or not str(text).strip():
        raise VariantParseError(
            "Empty variant input. Provide CHR-POS-REF-ALT (e.g. 1-65568-A-C), "
            "a CNV (e.g. 7:100-200:DEL), HGVS (e.g. NM_000123.4:c.10A>T), or an rsID."
        )
    t = str(text).strip()

    # 1. rsID.
    if _RSID_RE.match(t):
        return VariantInput(kind=InputKind.RSID, value=t.lower())

    # 2. Coordinate (unambiguous 4-token VCF shape). A 4-token split whose contig
    # is NOT a recognised chromosome (e.g. SPDI "NC_000001.11:1000:A:T") is not
    # treated as a coordinate -- it falls through to the HGVS catch-all so the
    # recoder can resolve it.
    tokens = _coordinate_tokens(t)
    if tokens is not None:
        chrom, pos, ref, alt = tokens
        if chrom in _VALID_CHROMS:
            return VariantInput(kind=InputKind.COORDINATE, value=f"{chrom}-{pos}-{ref}-{alt}")

    # 3. CNV (after stripping a leading "chr").
    cnv_match = _CNV_RE.match(_CHR_PREFIX_RE.sub("", t))
    if cnv_match is not None:
        chrom = cnv_match.group("chrom").upper()
        start = cnv_match.group("start")
        end = cnv_match.group("end")
        cnv_type = cnv_match.group("type").upper()
        return VariantInput(kind=InputKind.CNV, value=f"{chrom}:{start}-{end}:{cnv_type}")

    # 4. HGVS catch-all (incl. g./c./n./p. and SPDI).
    cleaned = clean_hgvs(t)
    if not cleaned:
        raise VariantParseError(
            "Input cleaned to an empty string and could not be interpreted as a variant."
        )
    return VariantInput(kind=InputKind.HGVS, value=cleaned)


def coordinate_to_vep_line(coord: str) -> str:
    """Render a canonical coordinate as a VEP region line.

    ``"1-65568-A-C"`` -> ``"1 65568 . A C . . ."`` (VCF-like, space-delimited,
    placeholder ID/QUAL/FILTER/INFO). Raises :class:`VariantParseError` if the
    coordinate is not exactly four dash-delimited fields with an integer pos.
    """
    parts = coord.split("-")
    if len(parts) != 4:
        raise VariantParseError(f"Malformed coordinate (expected CHR-POS-REF-ALT): {coord}")
    chrom, pos, ref, alt = parts
    if not pos.isdigit():
        raise VariantParseError(f"Malformed coordinate (non-integer position): {coord}")
    return f"{chrom} {pos} . {ref} {alt} . . ."


def cnv_to_vep_line(cnv: str) -> str:
    """Render a canonical CNV as a VEP region structural-variant line.

    ``"7:100-200:DEL"`` -> ``"7 100 200 deletion 1"`` (chrom, start, end, mapped
    type, allele number). The trailing ``1`` is the allele count VEP expects.
    Raises :class:`VariantParseError` if the CNV string is malformed.
    """
    match = _CNV_RE.match(_CHR_PREFIX_RE.sub("", cnv.strip()))
    if match is None:
        raise VariantParseError(f"Malformed CNV (expected CHR:START-END:TYPE): {cnv}")
    chrom = match.group("chrom").upper()
    start = match.group("start")
    end = match.group("end")
    mapped_type = _CNV_TYPE_TO_VEP[match.group("type").upper()]
    return f"{chrom} {start} {end} {mapped_type} 1"


def needs_recoding(vi: VariantInput) -> bool:
    """Whether an input must go through Variant Recoder before VEP.

    HGVS and rsID inputs need recoding to canonical coordinates; coordinates and
    CNVs are already in a VEP-region-ready shape.
    """
    return vi.kind in (InputKind.HGVS, InputKind.RSID)
