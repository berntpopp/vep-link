"""MCP tool registrations for vep-link.

:func:`register_vep_tools` wires all six tools onto a ``FastMCP`` instance in the
order a caller discovers them: discovery (``get_capabilities``) -> resolution
(``resolve_variant``) -> recoding (``recode_variant``) -> annotation
(``annotate_variant`` + ``annotate_variants_batch``) -> liftover
(``liftover_variant``). Each per-domain ``register_*`` helper lives in its own
module and takes the same lazy ``service_factory`` so the HTTP host can defer
service construction to the FastAPI lifespan.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastmcp import FastMCP

from vep_link.mcp.tools.annotate import register_annotate_tools
from vep_link.mcp.tools.capabilities import register_capabilities_tools
from vep_link.mcp.tools.liftover import register_liftover_tools
from vep_link.mcp.tools.recode import register_recode_tools
from vep_link.mcp.tools.resolve import register_resolve_tools

__all__ = ["register_vep_tools"]


def register_vep_tools(mcp: FastMCP, *, service_factory: Callable[[], Any]) -> None:
    """Register all six vep-link tools on ``mcp``.

    ``service_factory`` is a lazy callable returning the shared ``VepService``;
    each tool obtains the service via ``service_factory()`` at call time.
    """
    register_capabilities_tools(mcp, service_factory=service_factory)
    register_resolve_tools(mcp, service_factory=service_factory)
    register_recode_tools(mcp, service_factory=service_factory)
    register_annotate_tools(mcp, service_factory=service_factory)
    register_liftover_tools(mcp, service_factory=service_factory)
