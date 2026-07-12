"""Shared MCP tool annotations for vep-link.

All vep-link tools are read-only Ensembl REST lookups: they have no side effects
(``readOnlyHint``), never mutate/delete anything (``destructiveHint=false``),
return the same payload for the same arguments (``idempotentHint``), and may
return information about resources outside the server's own catalog
(``openWorldHint``). Every tool registers ``annotations=READ_ONLY_OPEN_WORLD`` so
a trusted client can safely auto-approve them.

The complete quartet is stamped explicitly -- notably ``destructiveHint=false``
-- rather than left to a client-side default, so the non-destructive guarantee
is asserted on the wire (MCP ``ToolAnnotations``).
"""

from __future__ import annotations

READ_ONLY_OPEN_WORLD = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}
