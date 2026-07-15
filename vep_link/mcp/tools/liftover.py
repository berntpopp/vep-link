"""liftover_variant: lift a coordinate variant between GRCh37 and GRCh38.

Drives ``service.liftover`` via the Ensembl assembly-map endpoint. Only
``CHR-POS-REF-ALT`` coordinates are liftable (HGVS/rsID raise
``unsupported_input``); zero mappings -> ``not_found`` and more than one ->
``ambiguous``. A same-build request is rejected up front as ``invalid_input``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from pydantic import Field

from vep_link.exceptions import UpstreamInputError
from vep_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from vep_link.mcp.errors import McpErrorContext, run_mcp_tool
from vep_link.mcp.resources import build_meta
from vep_link.mcp.tools._common import ensure_upstream_available, new_request_id
from vep_link.models.enums import GenomeBuild


def register_liftover_tools(
    mcp: FastMCP,
    *,
    service_factory: Callable[[], Any],
    health_factory: Callable[[], Any] | None = None,
) -> None:
    """Register ``liftover_variant`` on ``mcp``."""

    @mcp.tool(
        name="liftover_variant",
        title="Liftover Variant Between Assemblies",
        annotations=READ_ONLY_OPEN_WORLD,
        tags={"liftover"},
        output_schema=None,  # Tool-Surface Budget v1: suppress optional outputSchema
    )
    async def liftover_variant(
        variant: Annotated[
            str,
            Field(
                min_length=1,
                max_length=200,
                description=(
                    "A genomic coordinate (CHR-POS-REF-ALT) to lift between "
                    "assemblies. HGVS/rsID are not liftable -- resolve them first."
                ),
                examples=["1-169549811-C-A"],
            ),
        ],
        from_assembly: Annotated[
            Literal["GRCh38", "GRCh37"],
            Field(
                description="Source assembly of the input coordinate.",
                examples=["GRCh37"],
            ),
        ],
        to_assembly: Annotated[
            Literal["GRCh38", "GRCh37"],
            Field(
                description="Target assembly to lift the coordinate to (must differ from from_assembly).",
                examples=["GRCh38"],
            ),
        ],
    ) -> dict[str, Any]:
        """Use this to map a genomic coordinate (CHR-POS-REF-ALT) from one human assembly to the other (GRCh37 <-> GRCh38) via the Ensembl assembly-map endpoint. The two assemblies must differ. A unique mapping returns the lifted coordinate; zero mappings -> not_found, multiple -> ambiguous. HGVS/rsID inputs are unsupported (resolve_variant them first)."""

        health = health_factory() if health_factory else None

        async def call() -> dict[str, Any]:
            if from_assembly == to_assembly:
                raise UpstreamInputError("from_assembly and to_assembly must differ")
            # The assembly-map endpoint is served by the from_assembly host.
            ensure_upstream_available(health, from_assembly)
            service = service_factory()
            result: dict[str, Any] = await service.liftover(
                variant, GenomeBuild(from_assembly), GenomeBuild(to_assembly)
            )
            result["_meta"] = build_meta(
                tool="liftover_variant",
                request_id=new_request_id(),
                assembly=from_assembly,
            )
            return result

        return await run_mcp_tool(
            "liftover_variant",
            call,
            McpErrorContext(
                tool_name="liftover_variant", variant=variant, assembly=from_assembly, health=health
            ),
        )
