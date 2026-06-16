"""Shared MCP tool annotations for vep-link.

All six vep-link tools are read-only Ensembl REST lookups: they have no side
effects, return the same payload for the same arguments (idempotent), and may
return information about resources outside the server's own catalog
(open-world). Every tool registers ``annotations=READ_ONLY_OPEN_WORLD`` so a
trusted client can safely auto-approve them.
"""

from __future__ import annotations

READ_ONLY_OPEN_WORLD = {
    "readOnlyHint": True,
    "idempotentHint": True,
    "openWorldHint": True,
}
