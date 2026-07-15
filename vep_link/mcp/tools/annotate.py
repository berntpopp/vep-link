"""annotate_variant + annotate_variants_batch: full VEP annotation, shaped.

These are the workhorse tools. Each drives the parse -> recode-if-needed -> VEP
region POST -> normalize pipeline in ``VepService`` and then projects the result
to the requested :class:`~vep_link.models.enums.ResponseMode` tier via
:func:`~vep_link.mcp.shaping.shape_annotation`.

* ``annotate_variant`` annotates one variant and attaches a ``provenance`` block
  (endpoint + citation) alongside ``_meta``.
* ``annotate_variants_batch`` annotates up to 200 variants, shaping each result
  (while preserving its original ``input``) and returning the service's per-input
  ``errors`` and ``summary`` so one bad variant never fails the batch.

Both validate any caller-supplied ``vep_options`` against the allowlist
(disallowed keys -> ``invalid_input``) and surface an instance-dependence note
when a plugin flag (SpliceAI / dbNSFP) that the public Ensembl REST does not run
is requested.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from pydantic import Field

from vep_link.config import settings
from vep_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from vep_link.mcp.errors import McpErrorContext, run_mcp_tool
from vep_link.mcp.resources import build_meta, provenance, utc_now_iso
from vep_link.mcp.shaping import shape_annotation
from vep_link.mcp.tools._common import (
    ensure_upstream_available,
    new_request_id,
    next_command,
    spliceai_dbnsfp_note,
    validate_vep_options,
)
from vep_link.models.enums import GenomeBuild

_RESPONSE_MODE = Literal["minimal", "compact", "standard", "full"]
_TRANSCRIPTS = Literal["auto", "all"]


def _vep_region_endpoint(assembly: str) -> str:
    """Build the VEP region endpoint URL for the provenance block."""
    return f"{settings.vep_url(GenomeBuild(assembly))}/vep/homo_sapiens/region"


def _other_assembly(assembly: str) -> str:
    """The opposite human build (GRCh38 <-> GRCh37)."""
    return "GRCh37" if assembly == "GRCh38" else "GRCh38"


def _annotate_next_commands(
    canonical_id: str | None, assembly: str, *, truncated: bool
) -> list[dict[str, Any]]:
    """Ready-to-call follow-ups for an annotation result.

    Suggests recoding (all equivalent IDs) and lifting the canonical coordinate to
    the other build; when the standard view was truncated, leads with a
    widen-to-all re-call so the agent can pull every transcript on demand.
    """
    if not canonical_id:
        return []
    commands: list[dict[str, Any]] = []
    if truncated:
        commands.append(
            next_command(
                "annotate_variant",
                {
                    "variant": canonical_id,
                    "assembly": assembly,
                    "response_mode": "standard",
                    "transcripts": "all",
                },
            )
        )
    commands.append(
        next_command("recode_variant", {"variants": [canonical_id], "assembly": assembly})
    )
    commands.append(
        next_command(
            "liftover_variant",
            {
                "variant": canonical_id,
                "from_assembly": assembly,
                "to_assembly": _other_assembly(assembly),
            },
        )
    )
    return commands


def register_annotate_tools(
    mcp: FastMCP,
    *,
    service_factory: Callable[[], Any],
    health_factory: Callable[[], Any] | None = None,
) -> None:
    """Register ``annotate_variant`` and ``annotate_variants_batch`` on ``mcp``."""

    @mcp.tool(
        name="annotate_variant",
        title="Annotate Variant (VEP)",
        annotations=READ_ONLY_OPEN_WORLD,
        tags={"annotate"},
        output_schema=None,  # Tool-Surface Budget v1: suppress optional outputSchema
    )
    async def annotate_variant(
        variant: Annotated[
            str,
            Field(
                min_length=1,
                max_length=200,
                description=(
                    "A single variant (coordinate, rsID, HGVS, SPDI, or CNV) to "
                    "annotate with the Ensembl Variant Effect Predictor."
                ),
                examples=["1-169549811-C-A", "rs6025", "NM_000059.3:c.274G>A"],
            ),
        ],
        assembly: Annotated[
            Literal["GRCh38", "GRCh37"],
            Field(description="Reference build for annotation. GRCh38 default."),
        ] = "GRCh38",
        response_mode: Annotated[
            _RESPONSE_MODE,
            Field(
                description=(
                    "Verbosity tier: minimal (identity only), compact (default; "
                    "representative transcript + frequencies), standard (filtered "
                    "transcripts), or full (raw-ish payload). Position scores "
                    "(CADD/GERP) appear once under position_scores at all tiers "
                    "above minimal."
                ),
            ),
        ] = "compact",
        transcripts: Annotated[
            _TRANSCRIPTS,
            Field(
                description=(
                    "standard-tier only: 'auto' (default) drops uninformative "
                    "MODIFIER neighbour transcripts, collapses identical-effect "
                    "isoforms (equivalent_transcript_ids), and caps to the most "
                    "severe; 'all' returns every transcript uncollapsed. Each "
                    "variant carries its own transcripts_summary "
                    "{shown,collapsed,total} when filtered."
                ),
            ),
        ] = "auto",
        allele: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Optional ALT filter for a multi-allelic input: an ALT base "
                    "(e.g. 'A') or a full CHR-POS-REF-ALT. Omit to annotate every "
                    "ALT allele (each as an entry in variants[])."
                ),
            ),
        ] = None,
        vep_options: Annotated[
            dict[str, str] | None,
            Field(
                default=None,
                description=(
                    "Optional VEP flag overrides (keys must be in the allowlist; "
                    "call get_capabilities for the set). Plugins like SpliceAI / "
                    "dbNSFP are instance-dependent."
                ),
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Use this for the full VEP annotation of one variant: consequences, gene/transcript impact, HGVS, MANE/canonical flags, SIFT/PolyPhen, plus variant-level CADD/GERP under position_scores and per-transcript REVEL/AlphaMissense, and gnomAD frequencies. Input is parsed, recoded if needed, sent to the VEP region endpoint, then shaped to response_mode (start compact; widen to standard/full only if needed). The standard tier filters noisy neighbour transcripts by default (set transcripts='all' for every isoform). Carries a provenance block (endpoint + citation) and _meta.next_commands follow-ups."""

        health = health_factory() if health_factory else None

        async def call() -> dict[str, Any]:
            validate_vep_options(vep_options)
            ensure_upstream_available(health, assembly)
            service = service_factory()
            result = await service.annotate(
                variant, GenomeBuild(assembly), vep_options=vep_options, allele=allele
            )
            # One shaped projection per ALT allele; each keeps its own
            # transcripts_summary in-row (a single _meta cannot speak for N variants).
            shaped_variants = [
                shape_annotation(ann, response_mode, transcripts=transcripts)
                for ann in result["variants"]
            ]
            first_id = result["variants"][0]["variant_id"] if result.get("variants") else None
            truncated = any("transcripts_summary" in v for v in shaped_variants)
            payload: dict[str, Any] = {
                "query": result["query"],
                "assembly": assembly,
                "variants": shaped_variants,
                "warnings": result["warnings"],
                "provenance": provenance(
                    assembly=assembly,
                    endpoint=_vep_region_endpoint(assembly),
                    retrieved=utc_now_iso(),
                ),
                "_meta": build_meta(
                    tool="annotate_variant",
                    request_id=new_request_id(),
                    assembly=assembly,
                    next_commands=_annotate_next_commands(first_id, assembly, truncated=truncated),
                ),
            }
            note = spliceai_dbnsfp_note(vep_options)
            if note is not None:
                payload["note"] = note
            return payload

        return await run_mcp_tool(
            "annotate_variant",
            call,
            McpErrorContext(
                tool_name="annotate_variant", variant=variant, assembly=assembly, health=health
            ),
        )

    @mcp.tool(
        name="annotate_variants_batch",
        title="Annotate Variants Batch (VEP)",
        annotations=READ_ONLY_OPEN_WORLD,
        tags={"annotate", "batch"},
        output_schema=None,  # Tool-Surface Budget v1: suppress optional outputSchema
    )
    async def annotate_variants_batch(
        variants: Annotated[
            list[str],
            Field(
                min_length=1,
                max_length=200,
                description=(
                    "Up to 200 variants to annotate in one call. Internally "
                    "chunked and de-duplicated; one bad variant never fails the "
                    "batch (its error is collected per-input)."
                ),
                examples=[["1-169549811-C-A", "rs1799963"]],
            ),
        ],
        assembly: Annotated[
            Literal["GRCh38", "GRCh37"],
            Field(description="Reference build for annotation. GRCh38 default."),
        ] = "GRCh38",
        response_mode: Annotated[
            _RESPONSE_MODE,
            Field(description="Verbosity tier applied to every result (default compact)."),
        ] = "compact",
        transcripts: Annotated[
            _TRANSCRIPTS,
            Field(
                description=(
                    "standard-tier only: 'auto' (default) filters/caps each "
                    "result's transcripts; 'all' returns every transcript. Each "
                    "result carries its own transcripts_summary when truncated."
                ),
            ),
        ] = "auto",
        vep_options: Annotated[
            dict[str, str] | None,
            Field(
                default=None,
                description="Optional VEP flag overrides (keys must be in the allowlist).",
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Use this to annotate many variants (cap 200) in one call instead of looping annotate_variant. Returns a results list (each shaped to response_mode and tagged with its original input), a per-input errors list (parse/not-found failures that did not fail the batch), and a summary count. Identical canonical variants are de-duplicated into a single VEP request. Each result's standard-tier transcript list is filtered by default; per-result truncation is reported in that result's transcripts_summary."""

        health = health_factory() if health_factory else None

        async def call() -> dict[str, Any]:
            validate_vep_options(vep_options)
            ensure_upstream_available(health, assembly)
            service = service_factory()
            batch = await service.annotate_batch(
                variants, GenomeBuild(assembly), vep_options=vep_options
            )
            shaped_results: list[dict[str, Any]] = []
            for item in batch["results"]:
                original_input = item.get("input")
                shaped = shape_annotation(item, response_mode, transcripts=transcripts)
                shaped_results.append({"input": original_input, **shaped})
            payload: dict[str, Any] = {
                "assembly": assembly,
                "results": shaped_results,
                "errors": batch["errors"],
                "summary": batch["summary"],
                "_meta": build_meta(
                    tool="annotate_variants_batch",
                    request_id=new_request_id(),
                    assembly=assembly,
                ),
            }
            note = spliceai_dbnsfp_note(vep_options)
            if note is not None:
                payload["note"] = note
            return payload

        return await run_mcp_tool(
            "annotate_variants_batch",
            call,
            McpErrorContext(tool_name="annotate_variants_batch", assembly=assembly, health=health),
        )
