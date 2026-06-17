"""Capabilities, provenance, and ``_meta`` builders for the vep-link MCP server.

This module is the keystone the error envelope and all six tools import from:

* ``RESEARCH_USE_NOTICE`` / citation constants — the research-use and citation
  contract surfaced in every payload and resource.
* ``server_capabilities()`` — the always-readable discovery document.
* ``CAPABILITIES_VERSION`` — a stable short content hash of that document,
  echoed in every response ``_meta`` so a warm client skips re-fetching.
* ``build_meta()`` — the ``_meta`` block on every success/error envelope.
* ``provenance()`` — the per-result data-source / citation block.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from vep_link import __version__
from vep_link.config import DEFAULT_VEP_OPTIONS, VEP_OPTION_ALLOWLIST

RESEARCH_USE_NOTICE = "Research use only; not for clinical decision support."

# Current MCP protocol revision (date string). Surfaced in capabilities so
# clients can confirm protocol compatibility.
MCP_PROTOCOL_VERSION = "2025-06-18"

ENSEMBL_VEP_CITATION = (
    "McLaren W, et al. The Ensembl Variant Effect Predictor. "
    "Genome Biol. 2016;17:122. PMID:27268795."
)
VARIANT_RECODER_CITATION = "Ensembl Variant Recoder, Ensembl REST API. https://rest.ensembl.org"

# Tools in the order callers should discover them: discovery -> resolution ->
# recoding -> annotation -> batch -> liftover.
_TOOLS: list[dict[str, str]] = [
    {
        "name": "get_capabilities",
        "summary": (
            "Server/tool metadata: assemblies, input formats, VEP-option "
            "allowlist, response modes, error codes, citation contract, and the "
            "capabilities_version hash."
        ),
        "token_cost_hint": "low",
    },
    {
        "name": "resolve_variant",
        "summary": (
            "Normalize any supported input (coordinate, rsID, HGVS, SPDI, CNV) "
            "to a canonical CHR-POS-REF-ALT plus gene_symbol and "
            "most_severe_consequence."
        ),
        "token_cost_hint": "low",
    },
    {
        "name": "recode_variant",
        "summary": (
            "Variant Recoder GET/POST: return all equivalent representations "
            "(rsID, HGVS g./c./p./t., VCF string, SPDI) for one variant or a "
            "batch (cap 200)."
        ),
        "token_cost_hint": "medium",
    },
    {
        "name": "annotate_variant",
        "summary": (
            "Full VEP annotation for one variant: parse, recode-if-needed, VEP "
            "region POST, then shape to the requested response_mode."
        ),
        "token_cost_hint": "medium",
    },
    {
        "name": "annotate_variants_batch",
        "summary": (
            "Annotate up to 200 variants per call with internal chunking and a "
            "concurrency cap; returns per-input results and per-input errors so "
            "one bad variant never fails the batch."
        ),
        "token_cost_hint": "high",
    },
    {
        "name": "liftover_variant",
        "summary": (
            "Lift a coordinate/VCF variant between GRCh37 and GRCh38 via the "
            "Ensembl assembly-map endpoint (0 maps -> not_found, >1 -> "
            "ambiguous; HGVS/rsID unsupported)."
        ),
        "token_cost_hint": "low",
    },
    {
        "name": "check_upstream_health",
        "summary": (
            "Live Ensembl REST readiness per assembly (circuit-breaker snapshot "
            "via /info/ping); use before a batch or when calls start failing to "
            "see if a build is degraded and route to the healthy one."
        ),
        "token_cost_hint": "low",
    },
]

_ERROR_CODES = [
    "invalid_input",
    "unsupported_input",
    "not_found",
    "build_mismatch",
    "ambiguous",
    "rate_limited",
    "upstream_unavailable",
    "upstream_timeout",
    "output_validation_failed",
    "internal_error",
]

_INPUT_FORMATS = [
    "coordinate (CHR-POS-REF-ALT)",
    "rsID",
    "HGVS (g./c./n./p.)",
    "SPDI",
    "CNV (chr:start-end:TYPE)",
]

_RESPONSE_MODES = ["minimal", "compact", "standard", "full"]

# Codes for the top-level ``warnings[]`` channel (honest non-fatal signals that
# ride alongside a successful result; distinct from the error envelope's codes).
_WARNING_CODES = ["multiple_alts", "ref_not_validated"]

_RESOURCES = [
    "vep://capabilities",
    "vep://usage",
    "vep://reference",
    "vep://citations",
    "vep://research-use",
    "vep://health",
]

_NOTES = [
    RESEARCH_USE_NOTICE,
    (
        "Precomputed pathogenicity / conservation scores ARE served by the public "
        "Ensembl REST as dedicated toggles and are enabled by default: CADD "
        "(cadd_phred/cadd_raw), REVEL (revel), AlphaMissense (am_pathogenicity/"
        "am_class), Conservation (GERP), plus SIFT and PolyPhen. They populate "
        "only for applicable (e.g. missense/coding) variants."
    ),
    (
        "VEP plugins such as SpliceAI and dbNSFP are not run by the public "
        "Ensembl REST API; they are instance-dependent and surfaced in a note "
        "rather than silently dropped when an instance does not support them. "
        "The scores people usually want from dbNSFP (REVEL, CADD, SIFT, PolyPhen, "
        "AlphaMissense) are available via the dedicated toggles above."
    ),
    (
        "GRCh38 is served from rest.ensembl.org and GRCh37 from "
        "grch37.rest.ensembl.org; the assembly argument selects the host."
    ),
    (
        "Batch annotation is capped at 200 variants per call; larger requests "
        "must be split client-side."
    ),
    (
        "Token discipline: CADD and GERP (conservation) are genomic-position "
        "scores emitted once per variant under position_scores, not repeated per "
        "transcript; REVEL and AlphaMissense are substitution-specific and stay "
        "per transcript. Null transcript fields are dropped. The standard tier "
        "filters uninformative MODIFIER neighbour transcripts, collapses identical-"
        "effect isoforms, and caps to the most severe by default (transcripts='all' "
        "returns every isoform uncollapsed; transcripts_summary reports "
        "{shown,collapsed,total})."
    ),
    (
        "Operational telemetry (HTTP transport) is exposed at GET /metrics in "
        "Prometheus format alongside GET /health: per-tool call/error counts, a "
        "latency histogram, and per-assembly circuit-breaker state. Variant data "
        "stays in MCP; these are ops-only endpoints."
    ),
    (
        "Allele-correctness contract (v0.2): resolve_variant and annotate_variant "
        "return {query, assembly, variants[], warnings[]} -- a multi-allelic input "
        "(e.g. rs6025) expands to one entry per ALT allele instead of a silent "
        "single pick, with a multiple_alts warning. Pass the optional allele "
        "argument (an ALT base or full CHR-POS-REF-ALT) to filter to one ALT. "
        "liftover_variant validates the lifted reference base against the target "
        "assembly: on a mismatch it returns a coordinate-only lifted value plus a "
        "ref_not_validated warning. See warning_codes."
    ),
    (
        "v0.3: gene_symbol and the compact representative_transcript are anchored on "
        "most_severe_consequence (they always describe the variant's worst effect, even "
        "on contiguous-gene loci). recode_variant echoes the caller's input per result "
        "and honors the fields filter. _meta.timing now carries upstream_ms and "
        "cache_status (miss|hit|coalesced) alongside elapsed_ms."
    ),
]


def server_capabilities() -> dict[str, Any]:
    """The always-readable discovery document for the vep-link MCP server.

    Stable for a given build: contains no timestamps or live values, so its
    content hash (``CAPABILITIES_VERSION``) only changes when the contract does.
    """
    return {
        "server": "vep-link",
        "server_version": __version__,
        "mcp_protocol_version": MCP_PROTOCOL_VERSION,
        "research_use_only": True,
        "disclaimer": RESEARCH_USE_NOTICE,
        "assemblies": ["GRCh38", "GRCh37"],
        "default_assembly": "GRCh38",
        "input_formats": list(_INPUT_FORMATS),
        "response_modes": list(_RESPONSE_MODES),
        "response_mode_tiers": {
            "minimal": "variant_id + most_severe_consequence + gene_symbol + _meta",
            "compact": (
                "representative (prioritized) transcript + key fields + variant-level "
                "position_scores (CADD/GERP) + frequencies (default)"
            ),
            "standard": (
                "transcript consequences (null-stripped); auto-filters uninformative "
                "MODIFIER neighbours, collapses isoforms with an identical effect into "
                "one row + equivalent_transcript_ids, and caps to the most severe, with "
                "transcripts_summary {shown,collapsed,total}. Pass transcripts='all' "
                "for every isoform, uncollapsed"
            ),
            "full": "raw-ish VEP payload (all fields) + colocated variants/frequencies",
        },
        "tools": [dict(tool) for tool in _TOOLS],
        "error_codes": list(_ERROR_CODES),
        "warning_codes": list(_WARNING_CODES),
        "vep_default_options": dict(DEFAULT_VEP_OPTIONS),
        "vep_option_allowlist": sorted(VEP_OPTION_ALLOWLIST),
        "batch_max": 200,
        "citation": {
            "vep": ENSEMBL_VEP_CITATION,
            "variant_recoder": VARIANT_RECODER_CITATION,
        },
        "resources": list(_RESOURCES),
        "notes": list(_NOTES),
    }


def _compute_capabilities_version() -> str:
    serialized = json.dumps(server_capabilities(), sort_keys=True).encode()
    return hashlib.sha256(serialized).hexdigest()[:12]


# Stable short content hash of the capabilities document, computed once at
# import and echoed into every response ``_meta``.
CAPABILITIES_VERSION: str = _compute_capabilities_version()


def build_meta(
    *,
    tool: str,
    request_id: str,
    elapsed_ms: int = 0,
    assembly: str | None = None,
    next_commands: list[Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the ``_meta`` block carried by every success and error envelope.

    ``assembly`` is included only when provided; ``extra`` is merged last so
    callers can add fields (e.g. ``served_warm``, ``see_also``) without losing
    the canonical keys.
    """
    return {
        "tool": tool,
        "request_id": request_id,
        "timing": {"elapsed_ms": elapsed_ms},
        "capabilities_version": CAPABILITIES_VERSION,
        "unsafe_for_clinical_use": True,
        "next_commands": next_commands or [],
        **({"assembly": assembly} if assembly else {}),
        **(extra or {}),
    }


def utc_now_iso() -> str:
    """Return the current UTC wall-clock time as an ISO-8601 string.

    Used to stamp ``provenance.retrieved`` so a result records *when* it was
    fetched from Ensembl rather than carrying a misleading ``null`` placeholder.
    """
    return datetime.now(UTC).isoformat()


def provenance(
    *,
    assembly: str,
    endpoint: str,
    retrieved: str | None = None,
    source: str = "Ensembl VEP / Variant Recoder REST",
) -> dict[str, Any]:
    """Build the per-result provenance / citation block."""
    return {
        "data_source": source,
        "assembly": assembly,
        "endpoint": endpoint,
        "retrieved": retrieved,
        "recommended_citation": ENSEMBL_VEP_CITATION,
    }
