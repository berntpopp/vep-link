"""Every tool must expose the COMPLETE shared read-only safety annotation (F-20).

Recipe F: the shared ``READ_ONLY_OPEN_WORLD`` annotation previously omitted
``destructiveHint``, leaving it ``None`` on the wire. All vep-link tools are
read-only, non-destructive, idempotent, open-world Ensembl lookups, so every
registered tool must advertise the full quartet -- most importantly the explicit
``destructiveHint=false`` -- so a trusted client can safely auto-approve them.
"""

from __future__ import annotations

from vep_link.mcp.annotations import READ_ONLY_OPEN_WORLD


def test_shared_annotation_is_complete_read_only_non_destructive() -> None:
    assert READ_ONLY_OPEN_WORLD == {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }


async def test_every_registered_tool_exposes_destructive_hint_false(facade) -> None:
    tools = await facade.list_tools()
    assert tools, "no tools registered"
    for tool in tools:
        ann = tool.annotations
        assert ann is not None, f"{tool.name} has no annotations"
        assert ann.readOnlyHint is True, f"{tool.name} readOnlyHint != True"
        assert ann.destructiveHint is False, (
            f"{tool.name} must expose destructiveHint=false (was {ann.destructiveHint!r})"
        )
        assert ann.idempotentHint is True, f"{tool.name} idempotentHint != True"
        assert ann.openWorldHint is True, f"{tool.name} openWorldHint != True"
