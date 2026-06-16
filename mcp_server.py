"""Stdio MCP entrypoint for vep-link.

Builds an :class:`~vep_link.api.ensembl_client.EnsemblClient` and
:class:`~vep_link.services.vep_service.VepService` eagerly, then runs the FastMCP
facade over the default stdio transport. Importing this module is side-effect
free; the server only starts under ``__main__``.
"""

from __future__ import annotations

from vep_link.api.ensembl_client import EnsemblClient
from vep_link.config import settings
from vep_link.mcp.facade import create_vep_mcp
from vep_link.services.vep_service import VepService


def main() -> None:
    """Run the vep-link MCP facade over stdio."""
    service = VepService(EnsemblClient(settings), settings)
    create_vep_mcp(service_factory=lambda: service).run()


if __name__ == "__main__":
    main()
