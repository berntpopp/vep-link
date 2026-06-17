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

__all__ = ["multiple_alts_warning", "ref_not_validated_warning"]


def multiple_alts_warning(variants: list[str]) -> dict[str, Any]:
    """Signal that a single input resolved to several ALT alleles.

    All resolved alleles are returned in ``variants[]``; the warning makes the
    ambiguity explicit so the agent does not assume a single silent pick.
    """
    return {
        "code": "multiple_alts",
        "message": f"Input maps to {len(variants)} ALT alleles; all are returned in variants[].",
        "context": {"count": len(variants), "variants": list(variants)},
    }


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
