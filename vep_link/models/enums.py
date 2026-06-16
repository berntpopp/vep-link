"""Enumerations shared across vep-link.

These string enums are the single source of truth for assembly names, response
verbosity tiers, and parsed variant input categories. Their ``.value`` strings
are the literal tokens used in tool arguments and serialized payloads.
"""

from __future__ import annotations

from enum import Enum


class GenomeBuild(str, Enum):
    """Supported human reference assemblies."""

    GRCH38 = "GRCh38"
    GRCH37 = "GRCh37"


class ResponseMode(str, Enum):
    """Response verbosity tiers (token-cost control)."""

    MINIMAL = "minimal"
    COMPACT = "compact"
    STANDARD = "standard"
    FULL = "full"


class InputKind(str, Enum):
    """Classification of a raw variant input string."""

    COORDINATE = "coordinate"  # CHR-POS-REF-ALT (VCF 4-token)
    CNV = "cnv"  # chr:start-end:TYPE
    HGVS = "hgvs"  # g./c./n./p. notation
    RSID = "rsid"  # rs\\d+


class ConsequenceImpact(str, Enum):
    """VEP consequence impact ranks (ordinal: HIGH > MODERATE > LOW > MODIFIER)."""

    HIGH = "HIGH"
    MODERATE = "MODERATE"
    LOW = "LOW"
    MODIFIER = "MODIFIER"


_IMPACT_ORDINAL = {
    ConsequenceImpact.HIGH: 4,
    ConsequenceImpact.MODERATE: 3,
    ConsequenceImpact.LOW: 2,
    ConsequenceImpact.MODIFIER: 1,
}


def impact_rank(impact: str) -> int:
    """Return the ordinal rank for an impact string (0 if unknown)."""
    try:
        return _IMPACT_ORDINAL[ConsequenceImpact(impact)]
    except ValueError:
        return 0
