"""Pure VEP annotation extraction helpers (no I/O).

These functions flatten a single raw Ensembl VEP ``region`` record into a
normalized annotation dict. They mirror the field paths and transcript
prioritization logic of the upstream ``variant-linker`` project
(``dataExtractor.js`` and ``scoring.js``'s ``_findPrioritizedTranscript``) so
downstream service and response-shaping layers can rely on a stable shape.

All functions are pure: they read from the provided dict, never mutate it, and
perform no network or disk access. Missing keys are handled gracefully via
``dict.get``.
"""

from __future__ import annotations

import re
from typing import Any

from vep_link.mcp.untrusted_content import FENCED_FIELDS, fence_untrusted_text
from vep_link.models.enums import impact_rank

__all__ = [
    "flatten_consequences",
    "most_severe_transcript",
    "prioritize_transcript",
    "select_representative",
    "requested_transcript",
    "names_transcript",
    "extract_gnomad_frequencies",
    "extract_position_scores",
    "build_annotation",
]

# Transcript-consequence fields copied verbatim into each flattened row.
# The trailing block is the precomputed *substitution-specific* predictor scores
# (REVEL, AlphaMissense, SIFT, PolyPhen) -- these legitimately differ per
# transcript and stay on the row. CADD and GERP (conservation) are genomic-
# position values, identical across every transcript, so they are NOT here: they
# are hoisted once to a variant-level ``position_scores`` object (see
# :func:`extract_position_scores`). AlphaMissense arrives as a nested object and
# is flattened separately (see :func:`_extract_alphamissense`).
_PASSTHROUGH_FIELDS: tuple[str, ...] = (
    "gene_id",
    "gene_symbol",
    "transcript_id",
    "biotype",
    "consequence_terms",
    "impact",
    "canonical",
    "mane",
    "pick",
    "mane_select",
    "mane_plus_clinical",
    "hgvsc",
    "hgvsp",
    "amino_acids",
    "codons",
    "sift_score",
    "sift_prediction",
    "polyphen_score",
    "polyphen_prediction",
    "revel",
)

# Genomic-position scores hoisted to one variant-level ``position_scores`` dict
# instead of being repeated on every transcript row. CADD (cadd_phred/cadd_raw)
# scores a substitution at a locus; GERP (conservation) scores the position --
# both are constant across a variant's transcripts.
_POSITION_SCORE_FIELDS: tuple[str, ...] = ("cadd_phred", "cadd_raw", "conservation")

# Consequence buckets that may carry position scores, scanned in order. Coding
# variants populate them on ``transcript_consequences``; a purely intergenic
# variant may only have ``intergenic_consequences``.
_POSITION_SCORE_SOURCES: tuple[str, ...] = (
    "transcript_consequences",
    "intergenic_consequences",
    "regulatory_feature_consequences",
    "motif_feature_consequences",
)


def _extract_alphamissense(consequence: dict[str, Any]) -> tuple[float | None, str | None]:
    """Flatten VEP's nested ``alphamissense`` object to ``(pathogenicity, class)``.

    The ``AlphaMissense=1`` toggle returns ``{"am_pathogenicity": float,
    "am_class": str}`` (class is ``benign`` / ``pathogenic`` / ``ambiguous``).
    Returns ``(None, None)`` when the field is absent or not a mapping, so the
    flattened row keeps a stable two-column shape mirroring SIFT/PolyPhen.
    """
    am = consequence.get("alphamissense")
    if not isinstance(am, dict):
        return None, None
    pathogenicity = am.get("am_pathogenicity")
    return pathogenicity, am.get("am_class")


def _format_protein_position(consequence: dict[str, Any]) -> str | None:
    """Format ``protein_start``/``protein_end`` into a position string.

    Rules:
    - both present and equal -> ``str(start)``
    - both present and differing -> ``"start-end"``
    - only one present -> ``str`` of that one
    - neither present -> ``None``
    """
    start = consequence.get("protein_start")
    end = consequence.get("protein_end")
    if start is not None and end is not None:
        return str(start) if start == end else f"{start}-{end}"
    if start is not None:
        return str(start)
    if end is not None:
        return str(end)
    return None


def flatten_consequences(vep_record: dict[str, Any]) -> list[dict[str, Any]]:
    """Return one normalized row per ``transcript_consequences`` entry.

    Each row carries the transcript-level fields (gene/transcript identifiers,
    consequence terms, impact, HGVS notations, predictor scores, CADD) plus a
    formatted ``protein_position``. Missing optional fields are present with a
    ``None`` value. Returns ``[]`` when the record has no transcript
    consequences.
    """
    consequences = vep_record.get("transcript_consequences") or []
    rows: list[dict[str, Any]] = []
    for consequence in consequences:
        row: dict[str, Any] = {field: consequence.get(field) for field in _PASSTHROUGH_FIELDS}
        row["protein_position"] = _format_protein_position(consequence)
        row["am_pathogenicity"], row["am_class"] = _extract_alphamissense(consequence)
        rows.append(row)
    return rows


def most_severe_transcript(vep_record: dict[str, Any]) -> dict[str, Any] | None:
    """Return the transcript matching ``most_severe_consequence``.

    Picks the first transcript whose ``consequence_terms`` contains the
    record-level ``most_severe_consequence``. Falls back to the first transcript
    if none match, and returns ``None`` when there are no transcript
    consequences.
    """
    consequences: list[dict[str, Any]] = vep_record.get("transcript_consequences") or []
    if not consequences:
        return None
    most_severe = vep_record.get("most_severe_consequence")
    if most_severe is not None:
        for consequence in consequences:
            terms = consequence.get("consequence_terms") or []
            if most_severe in terms:
                return consequence
    return consequences[0]


def _rank_biological(transcripts: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Biological-priority pick over ``transcripts``.

    Order (matching ``variant-linker``): ``pick == 1`` > MANE (``mane_select``
    present OR ``'MANE_Select'`` in ``mane``) > ``canonical == 1`` > first.
    Returns ``None`` for an empty list.
    """
    if not transcripts:
        return None

    for tc in transcripts:
        if tc.get("pick") == 1:
            return tc

    for tc in transcripts:
        if tc.get("mane_select") is not None or "MANE_Select" in (tc.get("mane") or []):
            return tc

    for tc in transcripts:
        if tc.get("canonical") == 1:
            return tc

    return transcripts[0]


def prioritize_transcript(transcripts: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Select the most biologically relevant transcript.

    Priority order (matching ``variant-linker``):
    ``pick == 1`` > MANE (``mane_select`` present OR ``'MANE_Select'`` in
    ``mane``) > ``canonical == 1`` > first transcript. Returns ``None`` for an
    empty list. Thin wrapper over :func:`_rank_biological`.
    """
    return _rank_biological(transcripts)


# A transcript carrying only these terms does not overlap the variant (a flanking gene).
_FLANKING_TERMS = frozenset({"upstream_gene_variant", "downstream_gene_variant"})
_REQUESTED_TRANSCRIPT_RE = re.compile(
    r"^\s*((?:NM|NR|XM|XR)_\d+(?:\.\d+)?|ENST\d+(?:\.\d+)?)(?:\([^)]*\))?:"
)


def requested_transcript(query: str) -> str | None:
    """The transcript accession a transcript-level HGVS input names, else ``None``."""
    match = _REQUESTED_TRANSCRIPT_RE.match(query)
    return match.group(1) if match else None


def _unversioned(accession: Any) -> str | None:
    return accession.split(".", 1)[0] if isinstance(accession, str) else None


def names_transcript(tc: dict[str, Any], requested: str) -> bool:
    """Whether ``tc`` is ``requested`` (Ensembl id or MANE RefSeq, version-insensitive)."""
    ids = (tc.get("transcript_id"), tc.get("mane_select"), tc.get("mane_plus_clinical"))
    return _unversioned(requested) in {_unversioned(i) for i in ids}


def _worst(transcripts: list[dict[str, Any]], most_severe: str | None) -> dict[str, Any]:
    """The first member carrying ``most_severe``, else the highest-impact member."""
    for tc in transcripts:
        if most_severe is not None and most_severe in (tc.get("consequence_terms") or []):
            return tc
    return max(transcripts, key=lambda tc: impact_rank(tc.get("impact") or ""))


def select_representative(
    transcripts: list[dict[str, Any]], most_severe: str | None, requested: str | None = None
) -> dict[str, Any] | None:
    """The transcript a caller should read the variant on (#133).

    The ``requested`` transcript (matched on its Ensembl id or its MANE RefSeq
    accession, version-insensitive) wins. Otherwise, over transcripts that overlap
    the variant (flanking up/downstream genes dropped): ``pick == 1`` > MANE
    Select > MANE Plus Clinical > ``canonical == 1`` > any, and within the first
    non-empty tier the one carrying ``most_severe`` (else the highest impact). The
    most severe consequence alone never picks an NMD/alternate isoform over the
    clinical transcript. Returns ``None`` for an empty list.
    """
    if not transcripts:
        return None
    if requested is not None:
        for tc in transcripts:
            if names_transcript(tc, requested):
                return tc
    pool = [
        tc for tc in transcripts if not set(tc.get("consequence_terms") or []) <= _FLANKING_TERMS
    ] or transcripts
    tiers = (
        lambda tc: tc.get("pick") == 1,
        lambda tc: tc.get("mane_select") is not None or "MANE_Select" in (tc.get("mane") or []),
        lambda tc: tc.get("mane_plus_clinical") is not None,
        lambda tc: tc.get("canonical") == 1,
    )
    for in_tier in tiers:
        members = [tc for tc in pool if in_tier(tc)]
        if members:
            return _worst(members, most_severe)
    return _worst(pool, most_severe)


def extract_gnomad_frequencies(vep_record: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull gnomAD exome/genome frequencies from colocated variants.

    Reads ``colocated_variants[].frequencies[allele].{gnomade,gnomadg}`` and
    returns a list of ``{"allele", "gnomade", "gnomadg"}`` dicts. Either
    frequency may be ``None`` when absent. Returns ``[]`` when no colocated
    frequencies exist.
    """
    results: list[dict[str, Any]] = []
    for colocated in vep_record.get("colocated_variants") or []:
        frequencies = colocated.get("frequencies") or {}
        for allele, values in frequencies.items():
            results.append(
                {
                    "allele": allele,
                    "gnomade": values.get("gnomade"),
                    "gnomadg": values.get("gnomadg"),
                }
            )
    return results


def extract_position_scores(vep_record: dict[str, Any]) -> dict[str, Any]:
    """Hoist the genomic-position scores (CADD, GERP) to one variant-level dict.

    CADD (``cadd_phred``/``cadd_raw``) and GERP (``conservation``) are constant
    across a variant's transcripts, so they are returned once here instead of
    being duplicated on every transcript row. Scans the consequence buckets in
    :data:`_POSITION_SCORE_SOURCES` and takes the first non-null value per score.
    Only non-null scores are included, so the result is ``{}`` for a variant
    without precomputed scores (e.g. a non-coding/intergenic locus). Pure.
    """
    scores: dict[str, Any] = {}
    for source in _POSITION_SCORE_SOURCES:
        for consequence in vep_record.get(source) or []:
            for field in _POSITION_SCORE_FIELDS:
                if field not in scores and consequence.get(field) is not None:
                    scores[field] = consequence[field]
            if len(scores) == len(_POSITION_SCORE_FIELDS):
                return scores
    return scores


# COSMIC terms bar its ids (COSV/COSM/COSN) on free platforms; the public VEP
# REST has no parameter to exclude them, so they are dropped here.
_COSMIC_ID_RE = re.compile(r"^COS[VMN]\d+$")


def _clean_colocated(colocated: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop COSMIC entries/synonym buckets; fence upstream free-text fields."""
    kept: list[dict[str, Any]] = []
    for entry in colocated:
        if _COSMIC_ID_RE.match(str(entry.get("id", ""))):
            continue
        synonyms = entry.get("var_synonyms")
        if isinstance(synonyms, dict) and "COSMIC" in synonyms:
            entry = {**entry, "var_synonyms": {k: v for k, v in synonyms.items() if k != "COSMIC"}}
        for field in FENCED_FIELDS:
            if isinstance(entry.get(field), str):
                record_id = f"{entry.get('id')}#{field}"
                fenced = fence_untrusted_text(
                    entry[field], source="ensembl_vep", record_id=record_id
                )
                entry = {**entry, field: fenced}
        kept.append(entry)
    return kept


def build_annotation(
    vep_record: dict[str, Any], *, variant_id: str, assembly: str, query: str = ""
) -> dict[str, Any]:
    """Shape a raw VEP record into the normalized annotation dict.

    The returned shape is the contract consumed by the service and
    response-shaping layers. ``gene_symbol`` is taken from the representative
    transcript (see :func:`select_representative`): the transcript ``query`` names,
    else MANE/canonical among transcripts overlapping the variant, so a flanking
    neighbour gene never names the variant.
    """
    vep_record = {
        **vep_record,
        "colocated_variants": _clean_colocated(vep_record.get("colocated_variants") or []),
    }
    transcript_rows = flatten_consequences(vep_record)
    requested = requested_transcript(query)
    representative = select_representative(
        transcript_rows, vep_record.get("most_severe_consequence"), requested
    )
    gene_symbol = representative.get("gene_symbol") if representative is not None else None

    return {
        "variant_id": variant_id,
        "assembly": assembly,
        "input": vep_record.get("input"),
        "seq_region_name": vep_record.get("seq_region_name"),
        "start": vep_record.get("start"),
        "end": vep_record.get("end"),
        "allele_string": vep_record.get("allele_string"),
        "strand": vep_record.get("strand"),
        "most_severe_consequence": vep_record.get("most_severe_consequence"),
        "gene_symbol": gene_symbol,
        "requested_transcript": requested,
        # None when no transcript was requested; False = answered on another transcript.
        "requested_transcript_matched": (
            None
            if requested is None
            else representative is not None and names_transcript(representative, requested)
        ),
        "position_scores": extract_position_scores(vep_record),
        "transcript_consequences": transcript_rows,
        "frequencies": extract_gnomad_frequencies(vep_record),
        "colocated_variants": vep_record.get("colocated_variants", []),
    }
