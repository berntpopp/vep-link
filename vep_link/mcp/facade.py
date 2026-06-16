"""Hand-authored FastMCP facade for vep-link.

:func:`create_vep_mcp` builds the MCP server: it names the server, attaches the
discovery-oriented ``_INSTRUCTIONS`` (so a client knows the workflow before
calling anything), registers all six tools via
:func:`~vep_link.mcp.tools.register_vep_tools`, and installs the
argument-validation error handler so a bad-arguments call returns the structured
``invalid_input`` envelope rather than an opaque framework error.

``service_factory`` is a lazy callable so the HTTP host can defer to a service
built in the FastAPI lifespan rather than constructing one at import time.
``mask_error_details=True`` keeps raw exception text out of client-facing errors
(our error module already sanitizes, but this is defense in depth).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastmcp import FastMCP

from vep_link.mcp.errors import install_validation_error_handler
from vep_link.mcp.resources import RESEARCH_USE_NOTICE
from vep_link.mcp.tools import register_vep_tools

_INSTRUCTIONS = (
    "vep-link annotates human genetic variants via the Ensembl Variant Effect "
    "Predictor (VEP) and Variant Recoder REST APIs.\n"
    "- Backbone workflow: normalize the input (resolve_variant) -> recode to "
    "equivalent representations if needed (recode_variant) -> full VEP "
    "annotation (annotate_variant / annotate_variants_batch).\n"
    "- Six tools: get_capabilities (discovery), resolve_variant (any input -> "
    "canonical CHR-POS-REF-ALT), recode_variant (all equivalent IDs), "
    "annotate_variant (full VEP for one variant), annotate_variants_batch (up to "
    "200 at once), liftover_variant (GRCh37 <-> GRCh38 coordinate liftover).\n"
    "- Inputs accepted: coordinate (CHR-POS-REF-ALT), rsID, HGVS (g./c./n./p.), "
    "SPDI, and CNV.\n"
    "- Assemblies: GRCh38 (default) and GRCh37; the assembly argument selects the "
    "Ensembl REST host.\n"
    "- response_mode tiers (annotate_*): minimal -> compact (default) -> standard "
    "-> full. Start compact and widen only if needed to control token cost.\n"
    "- Every response carries a _meta block (capabilities_version hash + "
    "next_commands ready-to-call follow-ups); annotation results also carry a "
    "provenance block with the endpoint and recommended citation.\n"
    "- Errors are returned as a structured envelope with a deterministic "
    "error.code and a fallback_tool (get_capabilities) so a client can recover "
    "without scraping free text.\n"
    "- All tools are read-only, idempotent Ensembl lookups (safe to auto-call). "
    f"{RESEARCH_USE_NOTICE}"
)


def create_vep_mcp(
    *,
    service_factory: Callable[[], Any],
    health_factory: Callable[[], Any] | None = None,
) -> FastMCP:
    """Build the vep-link MCP server wired to ``service_factory``.

    ``service_factory`` is a lazy callable returning the shared ``VepService``;
    deferring construction lets the HTTP host build the service in its lifespan
    rather than at import time. ``health_factory`` (optional) returns the shared
    ``UpstreamHealth`` monitor; when omitted, a passive monitor (no background
    probe) is created so tools still stamp ``_meta.upstream`` and the
    ``vep://health`` resource works in stdio mode.
    """
    if health_factory is None:
        from vep_link.api.health import UpstreamHealth
        from vep_link.config import settings as _settings

        _default_health = UpstreamHealth(_settings)

        def health_factory() -> Any:
            return _default_health

    mcp: FastMCP = FastMCP(
        name="vep-link",
        instructions=_INSTRUCTIONS,
        mask_error_details=True,
    )
    register_vep_tools(mcp, service_factory=service_factory, health_factory=health_factory)
    _register_health_resource(mcp, health_factory=health_factory)
    install_validation_error_handler(mcp)
    return mcp


def _register_health_resource(mcp: FastMCP, *, health_factory: Callable[[], Any]) -> None:
    """Expose a readable ``vep://health`` resource (recomputed on each read)."""

    @mcp.resource("vep://health", mime_type="application/json")
    def health_resource() -> dict[str, Any]:
        """Live per-assembly Ensembl REST health (circuit-breaker snapshot)."""
        snapshot: dict[str, Any] = health_factory().snapshot()
        return snapshot
