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
from vep_link.mcp.resources import build_meta, provenance
from vep_link.mcp.shaping import shape_annotation
from vep_link.mcp.tools._common import (
    new_request_id,
    spliceai_dbnsfp_note,
    validate_vep_options,
)
from vep_link.models.enums import GenomeBuild

_RESPONSE_MODE = Literal["minimal", "compact", "standard", "full"]


def _vep_region_endpoint(assembly: str) -> str:
    """Build the VEP region endpoint URL for the provenance block."""
    return f"{settings.vep_url(GenomeBuild(assembly))}/vep/homo_sapiens/region"


def register_annotate_tools(mcp: FastMCP, *, service_factory: Callable[[], Any]) -> None:
    """Register ``annotate_variant`` and ``annotate_variants_batch`` on ``mcp``."""

    @mcp.tool(
        name="annotate_variant",
        title="Annotate Variant (VEP)",
        annotations=READ_ONLY_OPEN_WORLD,
        tags={"annotate"},
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
                    "representative transcript + frequencies), standard (all "
                    "transcripts), or full (raw-ish payload)."
                ),
            ),
        ] = "compact",
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
        """Use this for the full VEP annotation of one variant: consequences, gene/transcript impact, HGVS, MANE/canonical flags, SIFT/PolyPhen/CADD, and gnomAD frequencies. Input is parsed, recoded if needed, sent to the VEP region endpoint, then shaped to response_mode (start compact; widen to standard/full only if needed). Carries a provenance block (endpoint + citation)."""

        async def call() -> dict[str, Any]:
            validate_vep_options(vep_options)
            service = service_factory()
            result = await service.annotate(variant, GenomeBuild(assembly), vep_options=vep_options)
            shaped = shape_annotation(result, response_mode)
            payload: dict[str, Any] = {
                **shaped,
                "provenance": provenance(
                    assembly=assembly, endpoint=_vep_region_endpoint(assembly)
                ),
                "_meta": build_meta(
                    tool="annotate_variant",
                    request_id=new_request_id(),
                    assembly=assembly,
                ),
            }
            note = spliceai_dbnsfp_note(vep_options)
            if note is not None:
                payload["note"] = note
            return payload

        return await run_mcp_tool(
            "annotate_variant",
            call,
            McpErrorContext(tool_name="annotate_variant", variant=variant, assembly=assembly),
        )

    @mcp.tool(
        name="annotate_variants_batch",
        title="Annotate Variants Batch (VEP)",
        annotations=READ_ONLY_OPEN_WORLD,
        tags={"annotate", "batch"},
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
        vep_options: Annotated[
            dict[str, str] | None,
            Field(
                default=None,
                description="Optional VEP flag overrides (keys must be in the allowlist).",
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Use this to annotate many variants (cap 200) in one call instead of looping annotate_variant. Returns a results list (each shaped to response_mode and tagged with its original input), a per-input errors list (parse/not-found failures that did not fail the batch), and a summary count. Identical canonical variants are de-duplicated into a single VEP request."""

        async def call() -> dict[str, Any]:
            validate_vep_options(vep_options)
            service = service_factory()
            batch = await service.annotate_batch(
                variants, GenomeBuild(assembly), vep_options=vep_options
            )
            shaped_results: list[dict[str, Any]] = []
            for item in batch["results"]:
                original_input = item.get("input")
                shaped = shape_annotation(item, response_mode)
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
            McpErrorContext(tool_name="annotate_variants_batch", assembly=assembly),
        )
