"""Shared builders for the top-level ``warnings[]`` channel (pure, no I/O).

A warning is an honest, non-fatal signal to the agent: an answer *is* returned,
but something about it needs care (an ambiguous multi-allelic input, an
unverified lifted allele). Warnings never replace the structured error envelope,
which owns hard failures -- they ride alongside a successful result as a
top-level ``warnings`` list (empty when there is nothing to flag).

Lives in the services layer so both :mod:`vep_link.services.vep_service` (which
builds the result body) and the MCP tool layer (which sits above services) can
import these without a layering inversion.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "multiple_alts_warning",
    "ref_not_validated_warning",
    "requested_transcript_not_found_warning",
    "resolved_via_vep_hgvs_warning",
]


def multiple_alts_warning(variants: list[str], *, matching: str | None = None) -> dict[str, Any]:
    """Signal that a single input resolved to several ALT alleles.

    All resolved alleles are returned in ``variants[]``; the warning makes the
    ambiguity explicit so the agent does not assume a single silent pick.
    ``matching`` names the allele whose representative ``hgvsp`` is the
    requested protein change (listed first, #136).
    """
    warning: dict[str, Any] = {
        "code": "multiple_alts",
        "message": f"Input maps to {len(variants)} ALT alleles; all are returned in variants[].",
        "context": {"count": len(variants), "variants": list(variants)},
    }
    if matching is not None:
        warning["message"] += f" {matching} carries the requested protein change and is first."
        warning["context"]["matches_protein_change"] = matching
    return warning


def ref_not_validated_warning(*, expected: str, carried: str) -> dict[str, Any]:
    """Signal that a lifted REF did not match the target assembly reference base.

    Emitted by liftover when the carried REF disagrees with the actual reference
    base at the lifted locus; the alleles are then omitted (coordinate-only).
    """
    return {
        "code": "ref_not_validated",
        "message": (
            "Lifted coordinate's reference base does not match the target assembly; "
            "alleles omitted. Re-resolve in the target assembly for a usable variant."
        ),
        "context": {"expected_ref": expected, "carried_ref": carried},
    }


def resolved_via_vep_hgvs_warning() -> dict[str, Any]:
    """Signal that HGVS was resolved by VEP's HGVS parser because the recoder failed.

    Names the resolution path (#111): the coordinate is VEP's single pick, not the
    Variant Recoder's full allele set.
    """
    return {
        "code": "resolved_via_vep_hgvs",
        "message": (
            "Ensembl Variant Recoder was unavailable; the HGVS was resolved through "
            "VEP's own HGVS endpoint instead."
        ),
        "context": {"resolution_path": "vep_hgvs", "unavailable": "variant_recoder"},
    }


def requested_transcript_not_found_warning(
    *, requested: str, chosen: str | None, chosen_mane_select: str | None
) -> dict[str, Any]:
    """Signal that the HGVS-named transcript is not among VEP's returned transcripts.

    The representative is then MANE/canonical, so its consequence, c. and p. are
    NOT on the transcript the caller asked about.
    """
    return {
        "code": "requested_transcript_not_found",
        "message": (
            "The transcript named in the input is not among the transcripts VEP returned; "
            "representative_transcript is another transcript (see context). Its c./p. "
            "and consequence do not describe the requested transcript. For a RefSeq "
            'transcript, retry with vep_options={"refseq": "1"}.'
        ),
        "context": {
            "requested_transcript": requested,
            "chosen_transcript": chosen,
            "chosen_mane_select": chosen_mane_select,
        },
    }
