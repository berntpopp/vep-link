"""Pure response-shaping projections for vep-link MCP tools (no I/O).

These functions project a normalized annotation dict (as produced by
:func:`vep_link.services.extraction.build_annotation`) into one of the four
:class:`~vep_link.models.enums.ResponseMode` verbosity tiers. The tiers trade
token cost against detail:

- **minimal**: identity + most-severe consequence + gene symbol only.
- **compact** (default): minimal fields + position + variant-level
  ``position_scores`` (CADD/GERP, hoisted once) + a single prioritized
  ``representative_transcript`` (projected, null-stripped) + gnomAD frequencies.
- **standard**: compact identity/position/score fields + transcript
  consequences. By default (``transcripts="auto"``) uninformative all-null
  MODIFIER neighbours are dropped and the list is capped to the top
  ``max_transcripts`` by impact severity, with a ``transcripts_summary``
  reporting ``shown``/``total``; ``transcripts="all"`` returns every transcript.
- **full**: the entire annotation dict, unchanged.

Token discipline (per Anthropic response-shaping guidance): genomic-position
scores are emitted once, not per transcript; null-valued transcript keys are
dropped; and the noisy long tail of uninformative neighbour transcripts is
truncated with an explicit, agent-readable steer rather than dumped.

All functions are pure: they read from the provided dict, never mutate it, and
perform no network or disk access. Transcript selection is delegated to
:func:`vep_link.services.extraction.select_representative` (consequence-anchored)
to stay DRY.
"""

from __future__ import annotations

import copy
from typing import Any

from vep_link.models.enums import ResponseMode, impact_rank
from vep_link.services.extraction import select_representative

__all__ = [
    "DEFAULT_MAX_TRANSCRIPTS",
    "pick_representative_transcript",
    "shape_annotation",
]

# Default cap on the number of transcript rows the ``standard`` auto view emits.
# A backstop against the long tail of isoforms on multi-transcript genes; the
# caller can lift it via ``max_transcripts`` or bypass it with ``transcripts="all"``.
DEFAULT_MAX_TRANSCRIPTS = 10

# Identity fields carried by every tier above ``minimal``-only callers.
_MINIMAL_FIELDS: tuple[str, ...] = (
    "variant_id",
    "assembly",
    "most_severe_consequence",
    "gene_symbol",
)

# Position fields added at ``compact`` and ``standard``.
_POSITION_FIELDS: tuple[str, ...] = (
    "seq_region_name",
    "start",
    "end",
    "allele_string",
)

# Keys retained when projecting a single transcript consequence: identity +
# consequence + HGVS + the *substitution-specific* predictor scores (SIFT,
# PolyPhen, REVEL, AlphaMissense). CADD and GERP are genomic-position scores and
# are hoisted to a variant-level ``position_scores`` object, so they are NOT
# projected per transcript. Null-valued keys are dropped (see
# :func:`_project_transcript`).
_TRANSCRIPT_FIELDS: tuple[str, ...] = (
    "gene_symbol",
    "transcript_id",
    "consequence_terms",
    "impact",
    "hgvsc",
    "hgvsp",
    "protein_position",
    "sift_prediction",
    "polyphen_prediction",
    "revel",
    "am_pathogenicity",
    "am_class",
)

# Substitution-level signal fields. A MODIFIER transcript with ALL of these null
# is an uninformative neighbour (e.g. up/downstream_gene_variant on a flanking
# gene) and is dropped from the ``standard`` auto view.
_SIGNAL_FIELDS: tuple[str, ...] = (
    "hgvsc",
    "hgvsp",
    "protein_position",
    "sift_prediction",
    "polyphen_prediction",
    "revel",
    "am_pathogenicity",
    "am_class",
)

# Impact tiers that make a transcript informative regardless of predictor scores.
_INFORMATIVE_IMPACTS: frozenset[str] = frozenset({"HIGH", "MODERATE", "LOW"})


def pick_representative_transcript(
    transcripts: list[dict[str, Any]], most_severe: str | None = None
) -> dict[str, Any] | None:
    """Return the single most relevant transcript, or ``None``.

    Consequence-anchored: when ``most_severe`` is given, the pick is filtered to
    transcripts carrying that consequence before applying the biological ranking
    (``pick == 1`` > MANE > ``canonical == 1`` > first); with ``most_severe`` None
    it ranks over all. Delegates to
    :func:`vep_link.services.extraction.select_representative` so the compact tier
    and ``build_annotation`` share one selection path.
    """
    return select_representative(transcripts, most_severe)


def _project_transcript(transcript: dict[str, Any]) -> dict[str, Any]:
    """Project a transcript consequence to the compact key set, dropping nulls.

    Only keys in :data:`_TRANSCRIPT_FIELDS` with a non-``None`` value are kept, so
    an uninformative row does not pay the token cost of serializing absent fields.
    """
    return {
        field: value for field in _TRANSCRIPT_FIELDS if (value := transcript.get(field)) is not None
    }


def _is_informative(transcript: dict[str, Any]) -> bool:
    """Whether a transcript carries downstream-useful signal.

    Informative when its impact is HIGH/MODERATE/LOW, or when any
    substitution-level signal field (HGVS, SIFT/PolyPhen, REVEL, AlphaMissense)
    is present. A MODIFIER row with none of those is a noisy neighbour.
    """
    if transcript.get("impact") in _INFORMATIVE_IMPACTS:
        return True
    return any(transcript.get(field) is not None for field in _SIGNAL_FIELDS)


def _minimal(data: dict[str, Any]) -> dict[str, Any]:
    """Project to the ``minimal`` tier: identity + consequence + gene only."""
    return {field: data.get(field) for field in _MINIMAL_FIELDS}


def _identity_with_position(data: dict[str, Any]) -> dict[str, Any]:
    """Identity + position fields + variant-level ``position_scores`` (if any).

    Shared base for ``compact`` and ``standard``. ``position_scores`` is emitted
    only when non-empty so a non-coding/intergenic variant pays nothing for it.
    """
    shaped = _minimal(data)
    for field in _POSITION_FIELDS:
        shaped[field] = data.get(field)
    scores = data.get("position_scores") or {}
    if scores:
        shaped["position_scores"] = dict(scores)
    return shaped


def _compact(data: dict[str, Any]) -> dict[str, Any]:
    """Project to the ``compact`` tier (the default)."""
    shaped = _identity_with_position(data)
    representative = pick_representative_transcript(
        data.get("transcript_consequences") or [], data.get("most_severe_consequence")
    )
    shaped["representative_transcript"] = (
        _project_transcript(representative) if representative is not None else None
    )
    shaped["frequencies"] = data.get("frequencies", [])
    return shaped


def _select_transcripts(
    data: dict[str, Any], *, transcripts: str, max_transcripts: int
) -> tuple[list[dict[str, Any]], int, int]:
    """Choose + project the transcript rows for the ``standard`` tier.

    Returns ``(projected_rows, shown, total)`` where ``total`` is the number of
    transcripts the variant actually has. With ``transcripts="all"`` every
    transcript is kept in upstream order; otherwise uninformative neighbours are
    dropped (falling back to the full set if that would empty the list), the
    remainder is ordered most-severe first, and the top ``max_transcripts`` kept.
    """
    all_tcs = data.get("transcript_consequences") or []
    total = len(all_tcs)
    if transcripts == "all":
        chosen = list(all_tcs)
    else:
        informative = [tc for tc in all_tcs if _is_informative(tc)]
        pool = informative or list(all_tcs)
        pool = sorted(pool, key=lambda tc: impact_rank(tc.get("impact") or ""), reverse=True)
        chosen = pool[:max_transcripts]
    rows = [_project_transcript(tc) for tc in chosen]
    return rows, len(chosen), total


def _standard(data: dict[str, Any], *, transcripts: str, max_transcripts: int) -> dict[str, Any]:
    """Project to the ``standard`` tier: filtered/capped, null-stripped transcripts."""
    shaped = _identity_with_position(data)
    rows, shown, total = _select_transcripts(
        data, transcripts=transcripts, max_transcripts=max_transcripts
    )
    shaped["transcript_consequences"] = rows
    if shown < total:
        # Steer the agent: it is seeing a filtered/capped view, not everything.
        shaped["transcripts_summary"] = {"shown": shown, "total": total}
    shaped["frequencies"] = data.get("frequencies", [])
    return shaped


def _full(data: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of the entire annotation, unchanged in content."""
    return copy.deepcopy(data)


def shape_annotation(
    data: dict[str, Any],
    mode: ResponseMode | str = ResponseMode.COMPACT,
    *,
    transcripts: str = "auto",
    max_transcripts: int = DEFAULT_MAX_TRANSCRIPTS,
) -> dict[str, Any]:
    """Project a normalized annotation into the requested response tier.

    ``mode`` accepts a :class:`ResponseMode` or its raw string value
    (``"minimal"``/``"compact"``/``"standard"``/``"full"``); an unknown string
    raises :class:`ValueError`. Defaults to ``compact``.

    ``transcripts`` and ``max_transcripts`` apply only to the ``standard`` tier:
    ``"auto"`` (default) filters uninformative neighbours and caps the list;
    ``"all"`` returns every transcript uncapped.
    """
    resolved = ResponseMode(mode)
    if resolved is ResponseMode.MINIMAL:
        return _minimal(data)
    if resolved is ResponseMode.COMPACT:
        return _compact(data)
    if resolved is ResponseMode.STANDARD:
        return _standard(data, transcripts=transcripts, max_transcripts=max_transcripts)
    return _full(data)
