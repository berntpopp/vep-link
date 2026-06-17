"""Tool-Naming Standard v1 compliance guard (CI lint over the LIVE tool roster).

Adapted from the fleet exemplar (``mondo-link/tests/unit/test_tool_names.py``)
to satisfy rule 8 of the GeneFoundry Tool-Naming & Normalization Standard v1
(``genefoundry-router/docs/TOOL-NAMING-STANDARD-v1.md``): assert every
registered tool name is unprefixed snake_case, ``<=50`` chars, does NOT
self-prefix the gateway namespace token, starts with an approved verb, and that
the live FastMCP roster equals the ``get_capabilities`` roster.

This runs against the tools FastMCP actually registered (via the ``facade``
fixture), not a hand-maintained list, so a drifting name fails CI.

ACTION-SERVER VERB EXCEPTIONS
-----------------------------
vep-link is an ACTION / COMPUTE server (Ensembl VEP + Variant Recoder), not a
record/lookup server. Three of its leaf verbs -- ``annotate``, ``recode``,
``liftover`` -- and the readiness verb ``check`` are NOT in the v1 canonical set
{get, search, list, resolve, find, compare, compute}. They are legitimate domain
action verbs, and the Standard explicitly anticipates them: the
"Open: Standard v1.1 (pending decision)" section of
``TOOL-NAMING-STANDARD-v1.md`` states the v1 verb canon is too strict for
action/compute servers and that such verbs may be carried as explicit per-tool
exceptions, with the rule that action tools MUST NOT be mass-renamed before the
fleet-wide v1.1 decision. We therefore allow them as a small, explicit,
clearly-labelled exception set rather than renaming the tools. When v1.1 lands,
fold whichever verbs it canonises into ``_CANONICAL_VERBS`` and shrink
``_ACTION_VERB_EXCEPTIONS`` to match.
"""

from __future__ import annotations

import re
from typing import Any

from vep_link.mcp.resources import server_capabilities

# The DECIDED v1 rules (these always pass; they are not pending any decision).
_NAME_RE = re.compile(r"^[a-z0-9_]{1,50}$")
_CANONICAL_VERBS = frozenset({"get", "search", "list", "resolve", "find", "compare", "compute"})
# Pending the fleet-wide Standard v1.1 verb-canon extension (see module docstring
# and TOOL-NAMING-STANDARD-v1.md "Open: Standard v1.1"): action/compute verbs
# documented as explicit exceptions instead of being mass-renamed.
_ACTION_VERB_EXCEPTIONS = frozenset({"annotate", "recode", "liftover", "check"})
_ALLOWED_VERBS = _CANONICAL_VERBS | _ACTION_VERB_EXCEPTIONS

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


async def test_tool_names_conform_to_standard_v1(facade) -> None:
    names = await _live_tool_names(facade)
    assert names, "no tools registered"
    for name in names:
        assert _NAME_RE.match(name), f"{name!r} must match ^[a-z0-9_]{{1,50}}$"
        assert name == name.lower(), f"{name!r} must be lowercase snake_case"
        assert not name.startswith(f"{_NAMESPACE}_"), (
            f"{name!r} must not self-prefix the '{_NAMESPACE}' namespace token "
            "(namespacing is the gateway's job)"
        )
        verb = name.split("_", 1)[0]
        assert verb in _ALLOWED_VERBS, (
            f"{name!r} starts with non-approved verb {verb!r}; allowed canonical "
            f"verbs {sorted(_CANONICAL_VERBS)} plus action-server exceptions "
            f"{sorted(_ACTION_VERB_EXCEPTIONS)} (see TOOL-NAMING-STANDARD-v1.md)"
        )


async def test_action_verb_exceptions_are_actually_used(facade) -> None:
    # Guard against the exception set silently rotting: every action-verb
    # exception we carry must correspond to a real registered tool. If an action
    # tool is renamed/removed (or v1.1 canonises a verb), trim the exception set.
    names = await _live_tool_names(facade)
    used_verbs = {name.split("_", 1)[0] for name in names}
    stale = _ACTION_VERB_EXCEPTIONS - used_verbs
    assert not stale, (
        f"action-verb exceptions {sorted(stale)} match no registered tool; "
        "remove them from _ACTION_VERB_EXCEPTIONS"
    )
