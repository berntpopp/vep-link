"""Response-Envelope v1.1 fencing for the one upstream prose field vep-link returns.

Ensembl VEP REST ``colocated_variants[].clinvar_somatic_classification`` is free text
(disease names + ClinVar somatic tiers). It is wrapped as a typed ``untrusted_text``
object with digest and provenance, mirroring the fleet ``fence_untrusted_text``.
"""

from __future__ import annotations

import hashlib
import unicodedata
from datetime import UTC, datetime
from typing import Any

from vep_link.exceptions import ResponseTooLargeError
from vep_link.mcp._sanitize import FORBIDDEN_CODEPOINTS

FENCED_FIELDS: tuple[str, ...] = ("clinvar_somatic_classification",)

DEFAULT_MAX_TEXT_BYTES = 2_097_152
DEFAULT_MAX_OBJECTS = 128
DEFAULT_MAX_TOTAL_TEXT_BYTES = 8_388_608


def fence_untrusted_text(raw: str, *, source: str, record_id: str) -> dict[str, Any]:
    """NFC-normalize, drop the ratified forbidden code points, wrap with provenance."""
    normalized = unicodedata.normalize("NFC", raw)
    return {
        "kind": "untrusted_text",
        "text": "".join(c for c in normalized if ord(c) not in FORBIDDEN_CODEPOINTS),
        "provenance": {
            "source": source,
            "record_id": record_id,
            "retrieved_at": datetime.now(UTC).isoformat(),
        },
        "raw_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    }


def enforce_untrusted_text_limits(
    rows: list[dict[str, Any]],
    *,
    max_objects: int = DEFAULT_MAX_OBJECTS,
    max_text_bytes: int = DEFAULT_MAX_TEXT_BYTES,
    max_total_text_bytes: int = DEFAULT_MAX_TOTAL_TEXT_BYTES,
) -> None:
    """Fail closed (never truncate) when a response's fenced objects exceed a v1.1 ceiling."""
    sizes = [
        len(entry[field]["text"].encode("utf-8"))
        for row in rows
        for entry in row.get("colocated_variants") or []
        for field in FENCED_FIELDS
        if field in entry
    ]
    if (
        len(sizes) > max_objects
        or any(n > max_text_bytes for n in sizes)
        or sum(sizes) > max_total_text_bytes
    ):
        raise ResponseTooLargeError("untrusted_text ceiling exceeded")
