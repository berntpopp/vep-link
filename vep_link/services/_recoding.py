"""Private helpers for canonicalizing recoder/VEP payloads (pure, no I/O).

These functions translate the loosely-typed Ensembl Variant Recoder responses
into the canonical ``CHR-POS-REF-ALT`` forms the orchestration service needs,
and aggregate per-allele HGVS/SPDI arrays into flat lists for the ``recode``
view. They mirror the per-allele object layout the recoder uses: each entry
carries ``input``/``id``/``seq_region_name`` plus one object per alternate
allele letter, and the genomic coordinate lives in ``allele['vcf_string'][0]``.
"""

from __future__ import annotations

import re
from typing import Any

# A canonical genomic VCF string: contig-pos-ref-alt, ACGT alleles only.
_VCF_STRING_RE = re.compile(r"^[0-9XYM]+-\d+-[ACGT]+-[ACGT]+$")

# Keys on a recoder entry that are metadata, not alternate-allele objects.
_NON_ALLELE_KEYS = frozenset({"input", "id", "seq_region_name"})

# Aggregated HGVS/SPDI fields surfaced by ``recode``.
_RECODE_FIELDS: tuple[str, ...] = ("vcf_string", "hgvsg", "hgvsc", "hgvsp", "spdi")

# Always-kept identity keys, regardless of the field filter.
_RECODE_IDENTITY_KEYS: frozenset[str] = frozenset({"input", "id"})


def _allele_objects(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the per-allele objects on a recoder entry (skip metadata keys)."""
    return [
        value
        for key, value in entry.items()
        if key not in _NON_ALLELE_KEYS and isinstance(value, dict)
    ]


def canonical_vcf_strings(payload: Any) -> list[str]:
    """All distinct canonical ``CHR-POS-REF-ALT`` strings in a recoder GET reply.

    The recoder GET endpoint returns a *list*; each element carries one object
    per alternate allele. Every ``vcf_string`` matching the canonical genomic
    pattern is collected, de-duplicated, and **sorted deterministically** so a
    multi-allelic input resolves to the same ordered alt set on every call (which
    also removes batch non-determinism). Returns ``[]`` when none are present.
    """
    found: set[str] = set()
    if isinstance(payload, list):
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            for allele in _allele_objects(entry):
                for candidate in allele.get("vcf_string") or []:
                    if isinstance(candidate, str) and _VCF_STRING_RE.match(candidate):
                        found.add(candidate)
    return sorted(found)


def first_canonical_vcf_string(payload: Any) -> str | None:
    """First canonical alt of a recoder reply (deterministic); ``None`` if none.

    Delegates to :func:`canonical_vcf_strings` and returns its first element, so
    the single-alt callers (batch canonicalization) pick a stable, sorted alt.
    """
    alts = canonical_vcf_strings(payload)
    return alts[0] if alts else None


def aggregate_recode_entry(
    entry: dict[str, Any], *, input_override: str | None = None
) -> dict[str, Any]:
    """Flatten one recoder POST entry into the ``recode`` view shape.

    Aggregates each HGVS/SPDI/vcf_string array across every alternate-allele
    object into a single de-duplicated (order-preserving) list, alongside the
    entry's ``input`` and ``id``.

    ``input_override`` (the caller's original query) is echoed as ``input`` when
    given, because the Variant Recoder POST response does not reliably echo it
    (it comes back ``null`` at runtime).
    """
    aggregated: dict[str, list[str]] = {field: [] for field in _RECODE_FIELDS}
    for allele in _allele_objects(entry):
        for field in _RECODE_FIELDS:
            for value in allele.get(field) or []:
                if value not in aggregated[field]:
                    aggregated[field].append(value)

    # Emit only the fields actually present (non-empty); a key mapping to [] is
    # pure token overhead, so drop it. Mirrors the per-transcript null-stripping
    # in the shaping layer.
    echoed_input = input_override if input_override is not None else entry.get("input")
    result: dict[str, Any] = {"input": echoed_input, "id": entry.get("id")}
    result.update({field: values for field, values in aggregated.items() if values})
    return result


def project_recode_fields(result: dict[str, Any], fields: str | None) -> dict[str, Any]:
    """Trim an aggregated recode result to the caller's requested ``fields``.

    Client-side enforcement of the documented filter: Ensembl may ignore or
    partially honor the upstream ``fields`` param, so the contract is enforced
    here. ``input``/``id`` always survive. ``fields=None`` returns ``result``
    unchanged (the full set).
    """
    if fields is None:
        return result
    requested = {f.strip() for f in fields.split(",") if f.strip()}
    return {
        key: value
        for key, value in result.items()
        if key in _RECODE_IDENTITY_KEYS or key in requested
    }
