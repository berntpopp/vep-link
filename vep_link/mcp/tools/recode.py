"""recode_variant: Variant Recoder -- all equivalent representations of a variant.

Drives ``service.recode``: for each input variant (cap 200) Ensembl's Variant
Recoder returns every equivalent representation (rsID, HGVS g./c./p./t., VCF
string, SPDI). The success payload echoes the requested assembly, the per-input
``results`` list, and the canonical ``_meta`` block.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from pydantic import Field

from vep_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from vep_link.mcp.errors import McpErrorContext, run_mcp_tool
from vep_link.mcp.resources import build_meta
from vep_link.mcp.tools._common import new_request_id
from vep_link.models.enums import GenomeBuild


def register_recode_tools(mcp: FastMCP, *, service_factory: Callable[[], Any]) -> None:
    """Register ``recode_variant`` on ``mcp``."""

    @mcp.tool(
        name="recode_variant",
        title="Recode Variant Representations",
        annotations=READ_ONLY_OPEN_WORLD,
        tags={"recode"},
    )
    async def recode_variant(
        variants: Annotated[
            list[str],
            Field(
                min_length=1,
                max_length=200,
                description=(
                    "One or more variants (rsID, HGVS, coordinate, or SPDI) to "
                    "recode into every equivalent representation. Cap 200."
                ),
                examples=[["rs6025"], ["NM_000059.3:c.274G>A", "rs1799963"]],
            ),
        ],
        assembly: Annotated[
            Literal["GRCh38", "GRCh37"],
            Field(description="Reference build for recoding. GRCh38 default."),
        ] = "GRCh38",
        fields: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Optional comma-separated Variant Recoder field filter "
                    "(e.g. 'hgvsg,spdi,vcf_string'); omit for the default set."
                ),
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Use this to translate a variant (or a batch, cap 200) between identifier systems -- rsID <-> HGVS (g./c./p./t.) <-> VCF string <-> SPDI -- without a full VEP annotation. Returns one result object per input. Use the optional fields filter to trim the payload to just the representations you need."""

        async def call() -> dict[str, Any]:
            service = service_factory()
            results = await service.recode(variants, GenomeBuild(assembly), fields=fields)
            return {
                "assembly": assembly,
                "results": results,
                "_meta": build_meta(
                    tool="recode_variant",
                    request_id=new_request_id(),
                    assembly=assembly,
                ),
            }

        return await run_mcp_tool(
            "recode_variant",
            call,
            McpErrorContext(tool_name="recode_variant", assembly=assembly),
        )
