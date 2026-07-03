"""Tests for the upstream-health surface on the MCP tool layer.

Covers the always-on ``_meta.upstream`` hint, fail-fast on an open circuit, the
enriched retryable error envelope, the ``check_upstream_health`` tool, the
``get_capabilities.upstream`` summary, and the ``vep://health`` resource.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from tests.conftest import StubService
from tests.unit.test_tools import structured
from vep_link.api.health import UpstreamHealth
from vep_link.config import Settings
from vep_link.exceptions import EnsemblApiError
from vep_link.mcp.facade import create_vep_mcp

GRCH38_PING = "https://rest.ensembl.org/info/ping"
GRCH37_PING = "https://grch37.rest.ensembl.org/info/ping"


def _facade_with_health(stub: StubService, health: UpstreamHealth):
    return create_vep_mcp(service_factory=lambda: stub, health_factory=lambda: health)


async def test_meta_upstream_on_success() -> None:
    stub = StubService()
    health = UpstreamHealth(Settings())
    facade = _facade_with_health(stub, health)
    data = structured(await facade.call_tool("resolve_variant", {"variant": "rs6025"}))
    upstream = data["_meta"]["upstream"]
    assert upstream["GRCh38"] == "ok"
    assert upstream["GRCh37"] == "ok"
    assert "advice" not in upstream  # all healthy


async def test_fail_fast_when_circuit_open() -> None:
    stub = StubService()
    health = UpstreamHealth(Settings(CIRCUIT_FAILURE_THRESHOLD=3))
    for _ in range(3):
        health.record_failure("GRCh38")  # trip GRCh38 open
    facade = _facade_with_health(stub, health)

    result = await facade.call_tool("annotate_variant", {"variant": "rs6025", "assembly": "GRCh38"})
    assert result.is_error is True
    data = structured(result)
    assert data["error_code"] == "upstream_unavailable"
    assert data["retryable"] is True
    assert data["recovery_action"] == "retry_backoff"
    assert data["retry_after_s"] > 0
    # The recovery should name the healthy fallback host.
    assert "GRCh37" in data["recovery"]
    # The service was never called (fail-fast before the upstream attempt).
    assert not any(c[0] == "annotate" for c in stub.calls)
    # _meta.upstream reflects the degraded GRCh38.
    assert data["_meta"]["upstream"]["GRCh38"] == "down"


async def test_retryable_error_records_failure_and_enriches() -> None:
    stub = StubService()
    stub.annotate_error = EnsemblApiError("ensembl 500")
    health = UpstreamHealth(Settings(CIRCUIT_FAILURE_THRESHOLD=3))
    facade = _facade_with_health(stub, health)

    data = structured(
        await facade.call_tool("annotate_variant", {"variant": "1-1000-A-T", "assembly": "GRCh38"})
    )
    assert data["error_code"] == "upstream_unavailable"
    assert data["retryable"] is True
    # The breaker counted the real upstream failure.
    assert health._host("GRCh38").consecutive_failures == 1


async def test_non_upstream_error_not_retryable() -> None:
    from vep_link.exceptions import DataNotFoundError

    stub = StubService()
    stub.resolve_error = DataNotFoundError("nope")
    health = UpstreamHealth(Settings())
    facade = _facade_with_health(stub, health)
    data = structured(await facade.call_tool("resolve_variant", {"variant": "rs0"}))
    assert data["error_code"] == "not_found"
    assert data["retryable"] is False
    assert data["recovery_action"] == "switch_tool"
    # A not_found is not an upstream fault, so the breaker is untouched.
    assert health._host("GRCh38").consecutive_failures == 0


@respx.mock
async def test_check_upstream_health_tool() -> None:
    respx.get(GRCH38_PING).mock(return_value=httpx.Response(200, json={"ping": 1}))
    respx.get(GRCH37_PING).mock(return_value=httpx.Response(200, json={"ping": 1}))
    stub = StubService()
    health = UpstreamHealth(Settings())
    facade = _facade_with_health(stub, health)
    try:
        data = structured(await facade.call_tool("check_upstream_health", {}))
    finally:
        await health.aclose()
    assert data["upstream"]["GRCh38"]["status"] == "ok"
    assert data["upstream"]["GRCh37"]["reachable"] is True


async def test_get_capabilities_includes_upstream() -> None:
    stub = StubService()
    health = UpstreamHealth(Settings())
    facade = _facade_with_health(stub, health)
    data = structured(await facade.call_tool("get_capabilities", {}))
    assert "upstream" in data
    assert data["upstream"]["GRCh38"]["status"] == "ok"


async def test_health_resource_readable() -> None:
    stub = StubService()
    health = UpstreamHealth(Settings())
    facade = _facade_with_health(stub, health)
    contents = await facade.read_resource("vep://health")
    assert "GRCh38" in str(contents)


async def test_all_tools_registered() -> None:
    stub = StubService()
    facade = _facade_with_health(stub, UpstreamHealth(Settings()))
    expected = {
        "get_capabilities",
        "resolve_variant",
        "recode_variant",
        "annotate_variant",
        "annotate_variants_batch",
        "liftover_variant",
        "check_upstream_health",  # the new seventh tool
    }
    for name in expected:
        assert await facade.get_tool(name) is not None


@pytest.mark.parametrize("tool", ["resolve_variant", "recode_variant"])
async def test_meta_upstream_present_on_each_upstream_tool(tool: str) -> None:
    stub = StubService()
    health = UpstreamHealth(Settings())
    facade = _facade_with_health(stub, health)
    args = {"variant": "rs6025"} if tool == "resolve_variant" else {"variants": ["rs6025"]}
    data = structured(await facade.call_tool(tool, args))
    assert "upstream" in data["_meta"]
