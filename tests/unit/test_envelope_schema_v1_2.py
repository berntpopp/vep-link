"""Response-Envelope Standard v1.2: every data tool's success validates against the schema.

The schema is vendored from berntpopp/genefoundry-envelope, tag v1.2.0
(https://raw.githubusercontent.com/berntpopp/genefoundry-envelope/v1.2.0/envelope-v1.2.schema.json).
"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from vep_link.exceptions import DataNotFoundError

SCHEMA_PATH = Path(__file__).parents[1] / "conformance" / "envelope-v1.2.schema.json"
SCHEMA_SHA256 = "c3c47e20d7dd189aac2568fc963b5514d7a5ac2c3e5f21d126f42c96d1ad1ae3"
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

# One successful call per data tool. get_capabilities and check_upstream_health are
# ops/meta tools and outside the v1.2 rule.
CALLS: dict[str, dict[str, Any]] = {
    "resolve_variant": {"variant": "rs6025"},
    "recode_variant": {"variants": ["rs123"]},
    "annotate_variant": {"variant": "1-1000-A-T"},
    "annotate_variants_batch": {"variants": ["1-1000-A-T"]},
    "liftover_variant": {
        "variant": "1-1000-A-T",
        "from_assembly": "GRCh37",
        "to_assembly": "GRCh38",
    },
}
OPS_TOOLS = {"get_capabilities", "check_upstream_health"}


def test_vendored_schema_is_the_v1_2_0_release() -> None:
    assert sha256(SCHEMA_PATH.read_bytes()).hexdigest() == SCHEMA_SHA256


async def test_every_data_tool_is_covered(facade: Any) -> None:
    registered = {tool.name for tool in await facade.list_tools()}
    assert registered - OPS_TOOLS == set(CALLS)


@pytest.mark.parametrize("tool", sorted(CALLS))
async def test_success_validates_and_carries_provenance(facade: Any, tool: str) -> None:
    envelope = (await facade.call_tool(tool, CALLS[tool])).structured_content
    assert envelope["success"] is True
    jsonschema.validate(envelope, SCHEMA)
    meta = envelope["_meta"]
    assert meta["source"] == "ensembl"
    assert meta["data_version"] == "live"  # no probe has reported an Ensembl release here
    assert meta["retrieved_at"].endswith("Z")


@pytest.mark.parametrize("tool", sorted(OPS_TOOLS))
async def test_ops_tools_keep_their_shape(facade: Any, tool: str) -> None:
    envelope = (await facade.call_tool(tool, {})).structured_content
    assert "result" not in envelope and "retrieved_at" not in envelope["_meta"]


async def test_error_envelope_validates_too(facade: Any, stub_service: Any) -> None:
    stub_service.resolve_error = DataNotFoundError("nope")
    result = await facade.call_tool("resolve_variant", {"variant": "rs6025"})
    assert result.is_error is True
    jsonschema.validate(result.structured_content, SCHEMA)
