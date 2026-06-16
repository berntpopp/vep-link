"""resolve_variant: normalize any supported input to canonical CHR-POS-REF-ALT.

Drives ``service.resolve``: a coordinate is normalized locally, while an
rsID/HGVS/SPDI is recoded via Ensembl, returning ``variant_id`` plus
``gene_symbol`` and ``most_severe_consequence``. The success payload carries a
``_meta`` block whose ``next_commands`` point the caller at ``annotate_variant``
for the full annotation.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from pydantic import Field

from vep_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from vep_link.mcp.errors import McpErrorContext, run_mcp_tool
from vep_link.mcp.resources import build_meta
from vep_link.mcp.tools._common import ensure_upstream_available, new_request_id, next_command
from vep_link.models.enums import GenomeBuild


def register_resolve_tools(
    mcp: FastMCP,
    *,
    service_factory: Callable[[], Any],
    health_factory: Callable[[], Any] | None = None,
) -> None:
    """Register ``resolve_variant`` on ``mcp``."""

    @mcp.tool(
        name="resolve_variant",
        title="Resolve Variant to Coordinates",
        annotations=READ_ONLY_OPEN_WORLD,
        tags={"resolve"},
    )
    async def resolve_variant(
        variant: Annotated[
            str,
            Field(
                min_length=1,
                max_length=200,
                description=(
                    "A variant in any supported form: CHR-POS-REF-ALT, an rsID "
                    "(e.g. rs6025), genomic/transcript HGVS, or SPDI."
                ),
                examples=["rs6025", "1-169549811-C-A", "NM_000059.3:c.274G>A"],
            ),
        ],
        assembly: Annotated[
            Literal["GRCh38", "GRCh37"],
            Field(description="Reference build for resolution. GRCh38 default."),
        ] = "GRCh38",
    ) -> dict[str, Any]:
        """Use this when the caller's variant is an rsID, HGVS, SPDI, or loosely formatted, and you need the canonical CHR-POS-REF-ALT plus gene_symbol and most_severe_consequence that the annotation tools build on. Coordinates are normalized locally; rsIDs/HGVS are recoded via Ensembl. Cheap (<1kB). Then call annotate_variant for the full VEP annotation."""

        health = health_factory() if health_factory else None

        async def call() -> dict[str, Any]:
            ensure_upstream_available(health, assembly)
            service = service_factory()
            result: dict[str, Any] = await service.resolve(variant, GenomeBuild(assembly))
            result["_meta"] = build_meta(
                tool="resolve_variant",
                request_id=new_request_id(),
                assembly=assembly,
                next_commands=[
                    next_command("annotate_variant", {"variant": variant, "assembly": assembly}),
                ],
            )
            return result

        return await run_mcp_tool(
            "resolve_variant",
            call,
            McpErrorContext(
                tool_name="resolve_variant", variant=variant, assembly=assembly, health=health
            ),
        )
