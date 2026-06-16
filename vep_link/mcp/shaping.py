"""Pure response-shaping projections for vep-link MCP tools (no I/O).

These functions project a normalized annotation dict (as produced by
:func:`vep_link.services.extraction.build_annotation`) into one of the four
:class:`~vep_link.models.enums.ResponseMode` verbosity tiers. The tiers trade
token cost against detail:

- **minimal**: identity + most-severe consequence + gene symbol only.
- **compact** (default): minimal fields + position + a single prioritized
  ``representative_transcript`` (projected) + gnomAD frequencies.
- **standard**: compact identity/position fields + *all* transcript
  consequences (each projected to the compact key set) + frequencies.
- **full**: the entire annotation dict, unchanged.

All functions are pure: they read from the provided dict, never mutate it, and
perform no network or disk access. Transcript prioritization is delegated to
:func:`vep_link.services.extraction.prioritize_transcript` to stay DRY.
"""

from __future__ import annotations

import copy
from typing import Any

from vep_link.models.enums import ResponseMode
from vep_link.services.extraction import prioritize_transcript

__all__ = [
    "pick_representative_transcript",
    "shape_annotation",
]

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

# Keys retained when projecting a single transcript consequence. The trailing
# block is the headline pathogenicity / conservation signals (SIFT, PolyPhen,
# CADD, REVEL, AlphaMissense, GERP) so the default compact mode already carries
# the predictor scores an interpreter needs, without widening to standard/full.
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
    "cadd_phred",
    "revel",
    "am_pathogenicity",
    "am_class",
    "conservation",
)


def pick_representative_transcript(transcripts: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the single most biologically relevant transcript, or ``None``.

    Priority order (mirrors ``variant-linker``): ``pick == 1`` > MANE
    (``mane_select`` present OR ``'MANE_Select'`` in ``mane``) >
    ``canonical == 1`` > first transcript. Returns ``None`` for an empty list.

    Delegates to :func:`vep_link.services.extraction.prioritize_transcript` to
    avoid duplicating the selection logic.
    """
    return prioritize_transcript(transcripts)


def _project_transcript(transcript: dict[str, Any]) -> dict[str, Any]:
    """Project a transcript consequence down to the compact key set.

    Missing keys are present with a ``None`` value so the projected shape is
    stable across transcripts.
    """
    return {field: transcript.get(field) for field in _TRANSCRIPT_FIELDS}


def _minimal(data: dict[str, Any]) -> dict[str, Any]:
    """Project to the ``minimal`` tier: identity + consequence + gene only."""
    return {field: data.get(field) for field in _MINIMAL_FIELDS}


def _compact(data: dict[str, Any]) -> dict[str, Any]:
    """Project to the ``compact`` tier (the default)."""
    shaped = _minimal(data)
    for field in _POSITION_FIELDS:
        shaped[field] = data.get(field)
    representative = pick_representative_transcript(data.get("transcript_consequences") or [])
    shaped["representative_transcript"] = (
        _project_transcript(representative) if representative is not None else None
    )
    shaped["frequencies"] = data.get("frequencies", [])
    return shaped


def _standard(data: dict[str, Any]) -> dict[str, Any]:
    """Project to the ``standard`` tier: all transcripts, each projected."""
    shaped = _minimal(data)
    for field in _POSITION_FIELDS:
        shaped[field] = data.get(field)
    shaped["transcript_consequences"] = [
        _project_transcript(tc) for tc in (data.get("transcript_consequences") or [])
    ]
    shaped["frequencies"] = data.get("frequencies", [])
    return shaped


def _full(data: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of the entire annotation, unchanged in content."""
    return copy.deepcopy(data)


def shape_annotation(
    data: dict[str, Any], mode: ResponseMode | str = ResponseMode.COMPACT
) -> dict[str, Any]:
    """Project a normalized annotation into the requested response tier.

    ``mode`` accepts a :class:`ResponseMode` or its raw string value
    (``"minimal"``/``"compact"``/``"standard"``/``"full"``); an unknown string
    raises :class:`ValueError`. Defaults to ``compact``.
    """
    resolved = ResponseMode(mode)
    if resolved is ResponseMode.MINIMAL:
        return _minimal(data)
    if resolved is ResponseMode.COMPACT:
        return _compact(data)
    if resolved is ResponseMode.STANDARD:
        return _standard(data)
    return _full(data)
