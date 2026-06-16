"""Pydantic models and enumerations for vep-link."""

from __future__ import annotations

from .enums import (
    ConsequenceImpact,
    GenomeBuild,
    InputKind,
    ResponseMode,
    impact_rank,
)
from .requests import (
    AnnotateRequest,
    BatchAnnotateRequest,
    LiftoverRequest,
    RecodeRequest,
    ResolveRequest,
)
from .responses import (
    GnomadFrequency,
    LiftoverResult,
    RecodingResult,
    TranscriptConsequence,
    VariantAnnotation,
)

__all__ = [
    # Enums
    "ConsequenceImpact",
    "GenomeBuild",
    "InputKind",
    "ResponseMode",
    "impact_rank",
    # Request models
    "ResolveRequest",
    "RecodeRequest",
    "AnnotateRequest",
    "BatchAnnotateRequest",
    "LiftoverRequest",
    # Response models
    "TranscriptConsequence",
    "GnomadFrequency",
    "VariantAnnotation",
    "RecodingResult",
    "LiftoverResult",
]
