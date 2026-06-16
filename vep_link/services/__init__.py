"""Service layer for vep-link.

Holds pure extraction/shaping helpers and (added by other workers) the
``VepService`` orchestration. Re-exports the extraction functions for
convenient access. Keep additions here minimal and import-safe so this module
can be extended without circular-import surprises.
"""

from __future__ import annotations

from .extraction import (
    build_annotation,
    extract_gnomad_frequencies,
    flatten_consequences,
    most_severe_transcript,
    prioritize_transcript,
)
from .vep_service import VepService

__all__ = [
    "build_annotation",
    "extract_gnomad_frequencies",
    "flatten_consequences",
    "most_severe_transcript",
    "prioritize_transcript",
    "VepService",
]
