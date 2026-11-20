"""Batching, coalescing, rate limiting and 403 handling for Ensembl calls (#129, #130).

Upstream HTTP is mocked with ``respx``; the rate limiter is driven by a fake clock
so no test sleeps for real.
"""

from __future__ import annotations

import asyncio
import json
import math
from copy import deepcopy
from typing import Any

import httpx
import pytest
import respx

from tests.fixtures import VEP_REGION_MISSENSE
from vep_link.api.base_client import BaseHTTPClient, _endpoint
from vep_link.api.ensembl_client import EnsemblClient
from vep_link.api.rate_limit import TokenBucket
from vep_link.config import Settings
from vep_link.exceptions import (
    EnsemblApiError,
    RateLimitedError,
    UpstreamBlockedError,
    UpstreamInputError,
)
from vep_link.mcp.errors import canonical_error_code
from vep_link.models.enums import GenomeBuild
from vep_link.services import VepService

BASE = "https://rest.ensembl.org"
REGION_URL = f"{BASE}/vep/homo_sapiens/region"
RECODER_POST_URL = f"{BASE}/variant_recoder/homo_sapiens"
GRCH38 = GenomeBuild.GRCH38


@pytest.fixture
def fast(settings: Settings) -> Settings:
    """No client-side rate limit, so request counts are the only thing measured."""
    return settings.model_copy(update={"RATE_LIMIT_PER_SECOND": 0.0})


def _vep_echo(posted: list[str]) -> list[dict[str, Any]]:
    """One realistic VEP record per posted region line."""
    out = []
    for line in posted:
        record = deepcopy(VEP_REGION_MISSENSE[0])
        record["input"] = line
        out.append(record)
    return out


def _mock_region(bodies: list[list[str]], *, skip: str | None = None) -> respx.Route:
    def handler(request: httpx.Request) -> httpx.Response:
        posted = json.loads(request.content)["variants"]
        bodies.append(posted)
        return httpx.Response(200, json=_vep_echo([v for v in posted if v != skip]))

    return respx.post(REGION_URL).mock(side_effect=handler)


# --- coalescing -------------------------------------------------------------


@respx.mock
async def test_concurrent_single_annotates_share_one_post_per_200(fast: Settings) -> None:
    client = EnsemblClient(fast)
    service = VepService(client, fast)
    bodies: list[list[str]] = []
    route = _mock_region(bodies)
    variants = [f"1-{1000 + i}-A-T" for i in range(450)]
    try:
        results = await asyncio.gather(*(service.annotate(v, GRCH38) for v in variants))
    finally:
        await client.aclose()

    assert route.call_count == math.ceil(450 / 200)
    assert sorted(len(b) for b in bodies) == [50, 200, 200]
    # Each caller got the record for its own variant, not a neighbour's.
    assert [r["variants"][0]["variant_id"] for r in results] == variants


@respx.mock
async def test_batch_tool_chunks_at_200_and_keeps_per_item_errors(fast: Settings) -> None:
    client = EnsemblClient(fast)
    service = VepService(client, fast)
    bodies: list[list[str]] = []
    missing = "1 1001 . A T . . ."  # VEP returns nothing for this line
    route = _mock_region(bodies, skip=missing)
    respx.post(RECODER_POST_URL).mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{BASE}/variant_recoder/human/rs0").mock(
        return_value=httpx.Response(400, json={"error": "No variant found with ID 'rs0'"})
    )
    variants = [f"1-{1000 + i}-A-T" for i in range(199)] + ["rs0"]
    try:
        batch = await service.annotate_batch(variants, GRCH38)
    finally:
        await client.aclose()

    assert route.call_count == 1  # 199 coordinates in one POST; rs0 never resolved
    assert len(bodies[0]) == 199
    assert batch["summary"] == {
        "requested": 200,
        "annotated": 198,
        "failed": 2,
        "result_rows": 198,
    }
    codes = {e["input"]: e["error_code"] for e in batch["errors"]}
    assert codes == {"1-1001-A-T": "not_found", "rs0": "invalid_input"}


@respx.mock
async def test_whole_post_400_is_split_so_one_bad_item_fails_alone(fast: Settings) -> None:
    client = EnsemblClient(fast)
    bad = "1 2 . A T . . ."

    def handler(request: httpx.Request) -> httpx.Response:
        posted = json.loads(request.content)["variants"]
        if bad in posted:
            return httpx.Response(400, json={"error": "does not match reference allele"})
        return httpx.Response(200, json=[{"input": line} for line in posted])

    route = respx.post(REGION_URL).mock(side_effect=handler)
    lines = ["1 1 . A T . . .", bad, "1 3 . A T . . ."]
    try:
        results = await asyncio.gather(
            *(client.vep_region_post([line], GRCH38) for line in lines),
            return_exceptions=True,
        )
    finally:
        await client.aclose()

    assert route.call_count == 4  # the shared POST, then one per item
    assert results[0] == [{"input": lines[0]}]
    assert isinstance(results[1], UpstreamInputError)
    assert results[2] == [{"input": lines[2]}]


@respx.mock
async def test_whole_post_failure_reaches_every_waiter(fast: Settings) -> None:
    client = EnsemblClient(fast)
    route = respx.post(REGION_URL).mock(return_value=httpx.Response(503))
    try:
        results = await asyncio.gather(
            *(client.vep_region_post([f"1 {i} . A T . . ."], GRCH38) for i in range(3)),
            return_exceptions=True,
        )
    finally:
        await client.aclose()

    assert all(isinstance(r, EnsemblApiError) for r in results)
    assert route.call_count == fast.MAX_RETRIES + 1  # retried as one POST, not per caller


@respx.mock
async def test_concurrent_recoder_lookups_share_one_post_with_get_fallback(
    fast: Settings,
) -> None:
    client = EnsemblClient(fast)
    entries = {
        "rs1": {"A": {"input": "rs1", "vcf_string": ["1-1-C-A"]}},
        "rs2": {"T": {"input": "rs2", "vcf_string": ["1-2-C-T"]}},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        ids = json.loads(request.content)["ids"]
        return httpx.Response(200, json=[entries[i] for i in ids if i in entries])

    post = respx.post(RECODER_POST_URL).mock(side_effect=handler)
    get = respx.get(f"{BASE}/variant_recoder/human/rs3").mock(
        return_value=httpx.Response(400, json={"error": "No variant found with ID 'rs3'"})
    )
    try:
        results = await asyncio.gather(
            *(client.recoder_get(v, GRCH38) for v in ("rs1", "rs2", "rs3")),
            return_exceptions=True,
        )
    finally:
        await client.aclose()

    assert post.call_count == 1
    assert results[0] == [entries["rs1"]]
    assert results[1] == [entries["rs2"]]
    # Absent from the batched reply: the single GET surfaces Ensembl's precise error.
    assert get.call_count == 1
    assert isinstance(results[2], UpstreamInputError)


# --- rate limiter -----------------------------------------------------------


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


async def test_rate_limiter_never_exceeds_budget() -> None:
    clock = FakeClock()
    bucket = TokenBucket(8.0, 4, sleep=clock.sleep, clock=clock)
    starts: list[float] = []

    async def one() -> None:
        await bucket.acquire(max_wait=60)
        starts.append(clock.now)

    for _ in range(100):
        await one()

    assert starts[:4] == [1000.0] * 4  # the burst goes at once
    for i, t in enumerate(starts):
        in_window = [s for s in starts[i:] if s < t + 1.0]
        assert len(in_window) <= 4 + 8
    assert starts[-1] == pytest.approx(1000.0 + (100 - 4) / 8)


async def test_rate_limiter_refuses_a_wait_beyond_the_deadline() -> None:
    clock = FakeClock()
    bucket = TokenBucket(1.0, 1, sleep=clock.sleep, clock=clock)
    await bucket.acquire(max_wait=0)
    with pytest.raises(RateLimitedError):
        await bucket.acquire(max_wait=0.5)
    await bucket.acquire(max_wait=1.0)  # the refused call reserved nothing
    assert clock.now == pytest.approx(1001.0)


async def test_rate_limiter_honours_retry_after_and_429_jitter() -> None:
    clock = FakeClock()
    bucket = TokenBucket(8.0, 4, sleep=clock.sleep, clock=clock)
    bucket.observe(429, {}, retry_after=5.0)
    await bucket.acquire(max_wait=60)
    assert clock.now == pytest.approx(1005.0)

    bucket.observe(429, {}, retry_after=None)  # bare 429: pause 1-2 s with jitter
    await bucket.acquire(max_wait=60)
    assert 1006.0 <= clock.now <= 1007.0


async def test_rate_limiter_slows_when_remaining_is_low_and_pauses_at_zero() -> None:
    clock = FakeClock()
    bucket = TokenBucket(8.0, 1, sleep=clock.sleep, clock=clock)
    bucket.observe(200, {"X-RateLimit-Remaining": "10", "X-RateLimit-Reset": "20"}, None)
    await bucket.acquire(max_wait=60)
    await bucket.acquire(max_wait=60)
    assert clock.now == pytest.approx(1002.0)  # 20 s / 10 left = one per 2 s

    bucket.observe(200, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "30"}, None)
    await bucket.acquire(max_wait=60)
    assert clock.now == pytest.approx(1032.0)


@respx.mock
async def test_client_pauses_the_host_after_a_429_retry_after(settings: Settings) -> None:
    http = BaseHTTPClient(settings)
    clock = FakeClock()

    async def no_backoff(_seconds: float) -> None:
        return None

    http._sleep = no_backoff  # type: ignore[method-assign]
    # Only the bucket moves the clock, so the wait below is the bucket's pause.
    http._buckets["rest.ensembl.org"] = TokenBucket(8.0, 4, sleep=clock.sleep, clock=clock)
    respx.get(REGION_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, json=[]),
        ]
    )
    try:
        assert await http.get_json(REGION_URL) == []
    finally:
        await http.aclose()
    assert clock.now == pytest.approx(1007.0)


# --- 403 (#129) ---------------------------------------------------------------


@respx.mock
async def test_403_is_an_upstream_block_not_invalid_input(settings: Settings) -> None:
    http = BaseHTTPClient(settings)
    route = respx.get(REGION_URL).mock(return_value=httpx.Response(403))
    try:
        with pytest.raises(UpstreamBlockedError) as info:
            await http.get_json(REGION_URL)
    finally:
        await http.aclose()
    assert route.call_count == 1  # never retried
    assert canonical_error_code(info.value) == ("upstream_unavailable", "upstream_blocked")


# --- config + logging ---------------------------------------------------------


def test_batching_knobs_are_env_overridable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VEP_LINK_RATE_LIMIT_PER_SECOND", "3.5")
    monkeypatch.setenv("VEP_LINK_RATE_LIMIT_BURST", "2")
    monkeypatch.setenv("VEP_LINK_COALESCE_WINDOW_MS", "100")
    monkeypatch.setenv("VEP_LINK_CHUNK_SIZE", "50")
    s = Settings()
    assert (s.RATE_LIMIT_PER_SECOND, s.RATE_LIMIT_BURST) == (3.5, 2)
    assert (s.COALESCE_WINDOW_MS, s.CHUNK_SIZE) == (100, 50)
    monkeypatch.setenv("VEP_LINK_CHUNK_SIZE", "201")  # over Ensembl's POST maximum
    with pytest.raises(ValueError, match="CHUNK_SIZE"):
        Settings()


def test_endpoint_label_never_carries_the_input() -> None:
    assert _endpoint(f"{BASE}/vep/homo_sapiens/region") == "/vep/homo_sapiens/region"
    assert _endpoint(f"{BASE}/variant_recoder/human/rs6025") == "/variant_recoder/human"
    assert _endpoint(f"{BASE}/vep/human/hgvs/NM_000059.3%3Ac.274G%3EA") == "/vep/human/hgvs"
    assert _endpoint(f"{BASE}/map/human/GRCh37/1%3A5..5/GRCh38") == "/map/human"
