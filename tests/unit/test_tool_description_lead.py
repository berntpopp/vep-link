"""D1 (Tool-Schema Documentation v1.1): each tool description's first sentence says what it returns.

ponytail: copy of genefoundry-router scripts/surface.py ``lead_states_return``; re-vendor if the
router's rule changes (this repo does not vendor surface.py).
"""

from __future__ import annotations

import re
from typing import Any

_LEAD_VERBS = (  # noqa: SIM905 - kept byte-close to the router
    "get fetch retrieve list look lookup search find resolve report read predict annotate "
    "compute calculate count summarize summarise map translate convert describe show check "
    "validate classify score rank query browse discover explain compare diff normalize normalise "
    "expand evaluate assess enumerate identify locate export download match suggest recommend "
    "inspect generate render estimate"
).split()
_VERB_LEAD = re.compile(
    rf"^(?:use this (?:tool )?to )?(?:{'|'.join(_LEAD_VERBS)})(?:s|es|ies)?\b", re.IGNORECASE
)
_RETURNS = re.compile(
    r"(?<!not )(?<!n't )(?<!n’t )(?<!never )\breturn(?:s|ed|ing)?\b(?!\s*[~<\d])|→|->",  # noqa: RUF001
    re.IGNORECASE,
)
_FIELD_LIST = re.compile(r"\w+\W+\w+[^:]*:\s+[^.;:]+,")
_SENTENCE_END = re.compile(r"(?<!e\.g\.)(?<!i\.e\.)(?<=[.!?])\s+")


def lead_states_return(description: str | None) -> bool:
    lead = _SENTENCE_END.split(" ".join((description or "").split()), maxsplit=1)[0][:300]
    return bool(_VERB_LEAD.match(lead) or _RETURNS.search(lead) or _FIELD_LIST.search(lead))


def test_lead_rule_rejects_usage_only_lead() -> None:
    assert not lead_states_return("Use this when a caller needs constraint metrics for a gene.")
    assert lead_states_return("Use this when a caller needs X: returns a, b and c.")


async def test_every_tool_description_leads_with_its_return(facade: Any) -> None:
    failing = [t.name for t in await facade.list_tools() if not lead_states_return(t.description)]
    assert failing == []
