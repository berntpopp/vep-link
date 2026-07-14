"""README Standard v1 drift guard: the `## Tools` table IS the tool roster.

Rule 6 of the GeneFoundry README Standard v1
(``genefoundry-router/docs/README-STANDARD-v1.md``) requires the README's
``## Tools`` table to list every registered tool -- no more, no fewer. A
hand-maintained table rots the moment a tool is added or renamed, so this test
asserts the table against the tools FastMCP *actually* registered, via the same
``facade`` fixture ``test_tool_names.py`` uses. Adding a tool without updating
the README fails CI.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

README = Path(__file__).resolve().parents[2] / "README.md"

# A table row's first cell, e.g. "| `annotate_variant` | Full VEP ... |".
_ROW_TOOL = re.compile(r"^\|\s*`([a-z0-9_]+)`\s*\|")


def _readme_tool_names() -> set[str]:
    """Tool names listed in the README's `## Tools` table."""
    names: set[str] = set()
    in_tools = False

    for line in README.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            # Enter on `## Tools`; any later H2 ends the section.
            in_tools = line[3:].strip() == "Tools"
            continue
        if not in_tools:
            continue
        match = _ROW_TOOL.match(line)
        if match:
            names.add(match.group(1))

    return names


async def test_readme_tools_table_matches_registered_tools(facade: Any) -> None:
    # The README table and the live FastMCP roster must be the same set: a tool
    # added, removed, or renamed without a README edit fails here.
    live = {tool.name for tool in await facade.list_tools()}
    documented = _readme_tool_names()

    assert documented, (
        "no tool rows parsed from the README '## Tools' table -- the section is "
        "missing, empty, or its rows no longer wrap tool names in backticks"
    )
    assert documented == live, (
        "README '## Tools' table has drifted from the registered tools.\n"
        f"  missing from README: {sorted(live - documented)}\n"
        f"  not registered:      {sorted(documented - live)}"
    )
