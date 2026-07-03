"""Tool-Naming Standard v1.1 compliance guard (CI lint over the LIVE tool roster).

Adapted from the fleet exemplar to satisfy rule 8 of the GeneFoundry
Tool-Naming & Normalization Standard v1.1
(``genefoundry-router/docs/TOOL-NAMING-STANDARD-v1.md``): assert every
registered tool name is unprefixed snake_case, ``<=50`` chars, does NOT
self-prefix the gateway namespace token, starts with a Tier-1 or Tier-2
approved verb (or is exempt via the ops/meta tag carve-out), and that
the live FastMCP roster equals the ``get_capabilities`` roster.

This runs against the tools FastMCP actually registered (via the ``facade``
fixture), not a hand-maintained list, so a drifting name fails CI.

VERB CANON (ratified Standard v1.1, 2026-06-30)
------------------------------------------------
Tier-1 (universal read/query, all backends):
    get, search, list, resolve, find, compare, compute, map

Tier-2 (sanctioned domain action/compute verbs, used only where a backend
actually registers such a tool):
    predict, annotate, recode, liftover, analyze, score,
    submit, export, generate, download

Operational/meta carve-out (by tag, not verb):
    Tools tagged ``ops`` or ``meta`` skip the verb rule but still must pass
    charset/length/no-self-prefix checks. This is the ratified carve-out
    (Standard v1.1, genefoundry-router/docs/TOOL-NAMING-STANDARD-v1.md) --
    it is exactly ``{ops, meta}``, matching the router's authoritative
    ``check_leaf_name`` validator used by ``doctor --strict-naming``.
    ``check_upstream_health`` (verb ``check``) relies on this carve-out and
    carries the ``ops`` tag (in addition to its domain tags ``diagnostics``
    and ``health``) so it conforms under both this test and the router.

No local verb exceptions remain; all vep tools pass via the standard canon
or the tag carve-out.
"""

from __future__ import annotations

import re
from typing import Any

from vep_link.mcp.resources import server_capabilities

# Ratified Tier-1: universal read/query canon (Standard v1.1, Rule 2).
_CANONICAL_VERBS = frozenset(
    {"get", "search", "list", "resolve", "find", "compare", "compute", "map"}
)

# Ratified Tier-2: sanctioned domain action/compute verbs (Standard v1.1).
_TIER2_VERBS = frozenset(
    {
        "predict",
        "annotate",
        "recode",
        "liftover",
        "analyze",
        "score",
        "submit",
        "export",
        "generate",
        "download",
    }
)

# Combined allowed verb set for domain tools.
_ALL_VERBS = _CANONICAL_VERBS | _TIER2_VERBS

# Tags that grant an ops/meta carve-out (Standard v1.1, ratified). This is
# EXACTLY the set the router's authoritative ``check_leaf_name`` validator
# uses -- do not widen it here; retag the tool instead (see module docstring).
_OPS_CARVEOUT_TAGS = frozenset({"ops", "meta"})

# The canonical gateway namespace token for this server (documented in README).
# Leaf tools must NOT self-prefix it; the router applies it at mount time
# (``annotate_variant`` -> ``vep_annotate_variant`` at the gateway).
_NAMESPACE = "vep"


async def _live_tool_names(facade: Any) -> list[str]:
    """Sorted names of the tools FastMCP actually registered on the facade."""
    return sorted(t.name for t in await facade.list_tools())


async def test_live_roster_equals_capabilities_roster(facade) -> None:
    # The live FastMCP roster must equal the get_capabilities/expected roster so
    # the advertised contract and the registered tools never drift.
    live = set(await _live_tool_names(facade))
    expected = {tool["name"] for tool in server_capabilities()["tools"]}
    assert live == expected, f"live roster {live} != capabilities roster {expected}"


async def test_tool_names_conform_to_standard_v1_1(facade) -> None:
    tools = await facade.list_tools()
    assert tools, "no tools registered"
    for tool in tools:
        name = tool.name
        tags = set(getattr(tool, "tags", None) or ())
        assert re.fullmatch(r"[a-z0-9_]{1,50}", name), f"{name!r} must match ^[a-z0-9_]{{1,50}}$"
        assert name == name.lower(), f"{name!r} must be lowercase snake_case"
        assert not name.startswith(f"{_NAMESPACE}_"), (
            f"{name!r} must not self-prefix the '{_NAMESPACE}' namespace token "
            "(namespacing is the gateway's job)"
        )
        # Ops/meta tag carve-out: infrastructure tools are exempt from the verb rule.
        if tags & _OPS_CARVEOUT_TAGS:
            continue
        verb = name.split("_", 1)[0]
        assert verb in _ALL_VERBS, (
            f"{name!r} starts with non-approved verb {verb!r}; "
            f"Tier-1 verbs: {sorted(_CANONICAL_VERBS)}; "
            f"Tier-2 verbs: {sorted(_TIER2_VERBS)}; "
            "or tag the tool ops/meta for the carve-out "
            "(Standard v1.1, genefoundry-router/docs/TOOL-NAMING-STANDARD-v1.md)"
        )


async def test_carveout_tools_are_tagged_and_expected(facade) -> None:
    """Drift guard for the ops/meta carve-out.

    Every tool whose verb falls outside Tier-1/Tier-2 must rely on the
    carve-out -- i.e. must carry an ``ops`` or ``meta`` tag -- and the set of
    such carved-out tools must be exactly what we expect today
    (``check_upstream_health``). This fails CI if a future tool ships a
    non-conforming verb without the carve-out tag, or if
    ``check_upstream_health`` ever loses its ``ops`` tag.
    """
    tools = await facade.list_tools()
    assert tools, "no tools registered"

    carveout_tool_names: set[str] = set()
    for tool in tools:
        verb = tool.name.split("_", 1)[0]
        if verb in _ALL_VERBS:
            continue
        tags = set(getattr(tool, "tags", None) or ())
        assert tags & _OPS_CARVEOUT_TAGS, (
            f"{tool.name!r} has non-approved verb {verb!r} and no ops/meta tag; "
            "either rename it to use a Tier-1/Tier-2 verb or tag it ops/meta"
        )
        carveout_tool_names.add(tool.name)

    assert carveout_tool_names == {"check_upstream_health"}, (
        f"carve-out roster drifted: {carveout_tool_names} != {{'check_upstream_health'}}"
    )
