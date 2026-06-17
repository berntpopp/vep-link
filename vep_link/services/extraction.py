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

from typing import Any

__all__ = [
    "flatten_consequences",
    "most_severe_transcript",
    "prioritize_transcript",
    "select_representative",
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


def select_representative(
    transcripts: list[dict[str, Any]], most_severe: str | None
) -> dict[str, Any] | None:
    """Representative transcript anchored on the most-severe consequence.

    Filters to transcripts whose ``consequence_terms`` include ``most_severe``,
    then applies the biological ranking within that subset. Falls back to ranking
    over all transcripts when the subset is empty (or ``most_severe`` is None), so
    the returned gene always carries the reported worst consequence when one
    exists. Returns ``None`` for an empty list.
    """
    if not transcripts:
        return None
    if most_severe is not None:
        subset = [tc for tc in transcripts if most_severe in (tc.get("consequence_terms") or [])]
        if subset:
            return _rank_biological(subset)
    return _rank_biological(transcripts)


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


def build_annotation(
    vep_record: dict[str, Any], *, variant_id: str, assembly: str
) -> dict[str, Any]:
    """Shape a raw VEP record into the normalized annotation dict.

    The returned shape is the contract consumed by the service and
    response-shaping layers. ``gene_symbol`` is taken from the consequence-anchored
    representative transcript (see :func:`select_representative`), so it always
    names a gene on which ``most_severe_consequence`` occurs -- even on a
    contiguous-gene locus where the canonical transcript is a neighbour gene.
    """
    transcript_rows = flatten_consequences(vep_record)
    representative = select_representative(
        transcript_rows, vep_record.get("most_severe_consequence")
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
        "position_scores": extract_position_scores(vep_record),
        "transcript_consequences": transcript_rows,
        "frequencies": extract_gnomad_frequencies(vep_record),
        "colocated_variants": vep_record.get("colocated_variants", []),
    }
