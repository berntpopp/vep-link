"""Unit tests for the resilient async HTTP base client.

All upstream HTTP is mocked with ``respx`` against a dummy Ensembl host; the
``settings`` conftest fixture pins backoff to 0 and ``MAX_RETRIES=2`` so the
retry loop is deterministic. The ``_sleep`` indirection is monkeypatched to a
no-op where any real backoff would otherwise occur.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any

import httpx
import pytest
import respx

from vep_link.api.base_client import BaseHTTPClient
from vep_link.config import Settings
from vep_link.exceptions import (
    EnsemblApiError,
    RateLimitedError,
    UpstreamInputError,
    UpstreamTimeoutError,
)

BASE = "https://rest.ensembl.org"
URL = f"{BASE}/vep/homo_sapiens/region"


async def _noop_sleep(_seconds: float) -> None:
    return None


@pytest.fixture
def client(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> BaseHTTPClient:
    """A client whose backoff sleep is a no-op for deterministic retry timing."""
    c = BaseHTTPClient(settings)
    monkeypatch.setattr(c, "_sleep", _noop_sleep)
    return c


@respx.mock
async def test_get_json_parses_200(client: BaseHTTPClient) -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(200, json={"ok": True}))
    try:
        result = await client.get_json(URL, {"a": "1"})
    finally:
        await client.aclose()
    assert result == {"ok": True}
    assert route.called


@respx.mock
async def test_post_json_parses_200_and_sends_body(client: BaseHTTPClient) -> None:
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        captured["content_type"] = request.headers.get("content-type")
        return httpx.Response(200, json=[{"id": "rs1"}])

    route = respx.post(URL).mock(side_effect=_handler)
    try:
        result = await client.post_json(URL, {"variants": ["1 1 . A C . . ."]})
    finally:
        await client.aclose()

    assert result == [{"id": "rs1"}]
    assert route.called
    assert json.loads(captured["body"]) == {"variants": ["1 1 . A C . . ."]}
    assert captured["content_type"] is not None
    assert "application/json" in captured["content_type"]


@respx.mock
async def test_429_then_200_is_retried(client: BaseHTTPClient) -> None:
    route = respx.get(URL).mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    try:
        result = await client.get_json(URL)
    finally:
        await client.aclose()
    assert result == {"ok": True}
    assert route.call_count == 2


@respx.mock
async def test_request_records_upstream_ms(
    client: BaseHTTPClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Each attempt feeds the request-scoped upstream-time accumulator. Capture the
    # actual record_upstream calls (a >= 0 assertion alone can't prove wiring,
    # since the default is already 0 and respx is instant).
    captured: list[float] = []
    monkeypatch.setattr("vep_link.api.base_client.record_upstream", lambda ms: captured.append(ms))
    respx.get(URL).mock(return_value=httpx.Response(200, json={"ok": 1}))
    try:
        await client.get_json(URL)
    finally:
        await client.aclose()
    assert len(captured) == 1  # one attempt -> one record
    assert captured[0] >= 0.0


@respx.mock
async def test_request_records_upstream_ms_per_attempt(
    client: BaseHTTPClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Upstream time accumulates across retries: a 429 then 200 is two attempts.
    captured: list[float] = []
    monkeypatch.setattr("vep_link.api.base_client.record_upstream", lambda ms: captured.append(ms))
    respx.get(URL).mock(side_effect=[httpx.Response(429), httpx.Response(200, json={"ok": True})])
    try:
        await client.get_json(URL)
    finally:
        await client.aclose()
    assert len(captured) == 2  # one record per attempt, including the retry


@respx.mock
async def test_429_always_raises_rate_limited(client: BaseHTTPClient) -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(429))
    try:
        with pytest.raises(RateLimitedError):
            await client.get_json(URL)
    finally:
        await client.aclose()
    # MAX_RETRIES=2 => 1 initial + 2 retries == 3 attempts.
    assert route.call_count == 3


@respx.mock
async def test_503_then_200_is_retried(client: BaseHTTPClient) -> None:
    route = respx.get(URL).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    try:
        result = await client.get_json(URL)
    finally:
        await client.aclose()
    assert result == {"ok": True}
    assert route.call_count == 2


@respx.mock
async def test_503_exhausted_raises_ensembl_api_error(client: BaseHTTPClient) -> None:
    respx.get(URL).mock(return_value=httpx.Response(503))
    try:
        with pytest.raises(EnsemblApiError):
            await client.get_json(URL)
    finally:
        await client.aclose()


@respx.mock
async def test_400_raises_input_error_without_retry(client: BaseHTTPClient) -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(400, json={"error": "bad region"}))
    try:
        with pytest.raises(UpstreamInputError):
            await client.get_json(URL)
    finally:
        await client.aclose()
    # Deterministic input errors are NOT retried.
    assert route.call_count == 1


@respx.mock
async def test_404_raises_input_error_without_retry(client: BaseHTTPClient) -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(404))
    try:
        with pytest.raises(UpstreamInputError):
            await client.get_json(URL)
    finally:
        await client.aclose()
    assert route.call_count == 1


@respx.mock
async def test_other_4xx_raises_input_error(client: BaseHTTPClient) -> None:
    respx.get(URL).mock(return_value=httpx.Response(403))
    try:
        with pytest.raises(UpstreamInputError):
            await client.get_json(URL)
    finally:
        await client.aclose()


@respx.mock
async def test_connect_timeout_exhausted_raises_upstream_timeout(
    client: BaseHTTPClient,
) -> None:
    respx.get(URL).mock(side_effect=httpx.ConnectTimeout("timed out"))
    try:
        with pytest.raises(UpstreamTimeoutError):
            await client.get_json(URL)
    finally:
        await client.aclose()


@respx.mock
async def test_transport_error_exhausted_raises_ensembl_api_error(
    client: BaseHTTPClient,
) -> None:
    respx.get(URL).mock(side_effect=httpx.ConnectError("conn refused"))
    try:
        with pytest.raises(EnsemblApiError):
            await client.get_json(URL)
    finally:
        await client.aclose()


@respx.mock
async def test_retry_after_zero_is_honored_then_200(
    client: BaseHTTPClient,
) -> None:
    route = respx.get(URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    try:
        result = await client.get_json(URL)
    finally:
        await client.aclose()
    assert result == {"ok": True}
    assert route.call_count == 2


@respx.mock
async def test_retry_after_http_date_is_parsed(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 429 with an HTTP-date Retry-After is parsed and the wait passed to _sleep."""
    recorded: list[float] = []

    async def _record_sleep(seconds: float) -> None:
        recorded.append(seconds)

    c = BaseHTTPClient(settings)
    monkeypatch.setattr(c, "_sleep", _record_sleep)

    # An HTTP-date a little in the future; the parsed wait should be > 0.
    future = format_datetime(datetime.now(UTC) + timedelta(seconds=5))
    route = respx.get(URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": future}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    try:
        result = await c.get_json(URL)
    finally:
        await c.aclose()
    assert result == {"ok": True}
    assert route.call_count == 2
    assert recorded, "expected _sleep to be invoked for the 429 backoff"
    assert recorded[0] > 0


@respx.mock
async def test_semaphore_caps_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fire N concurrent calls; assert max simultaneous in-flight <= MAX_CONCURRENCY."""
    max_concurrency = 3
    settings = Settings(
        MAX_RETRIES=0,
        BACKOFF_BASE_SECONDS=0.0,
        BACKOFF_MAX_SECONDS=0.0,
        MAX_CONCURRENCY=max_concurrency,
        QUEUE_WAIT_TIMEOUT=10,
    )
    client = BaseHTTPClient(settings)

    in_flight = 0
    peak = 0
    gate = asyncio.Event()

    async def _handler(_request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        # Hold each request until all launched ones have entered, so the peak
        # reflects the semaphore ceiling rather than fast serial completion.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(gate.wait(), timeout=2.0)
        in_flight -= 1
        return httpx.Response(200, json={"ok": True})

    respx.get(URL).mock(side_effect=_handler)

    async def _release_when_capped() -> None:
        # Once we have observed the cap saturated, let everyone through.
        for _ in range(200):
            if in_flight >= max_concurrency:
                gate.set()
                return
            await asyncio.sleep(0.005)
        gate.set()

    try:
        results = await asyncio.gather(
            _release_when_capped(),
            *[client.get_json(URL) for _ in range(8)],
        )
    finally:
        await client.aclose()

    payloads = results[1:]
    assert all(p == {"ok": True} for p in payloads)
    assert peak <= max_concurrency


async def test_acquire_timeout_raises_rate_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A saturated semaphore that cannot be acquired within QUEUE_WAIT_TIMEOUT raises."""
    settings = Settings(MAX_CONCURRENCY=1, QUEUE_WAIT_TIMEOUT=0)
    client = BaseHTTPClient(settings)
    # Drain the only permit so the next acquire must wait past the (zero) deadline.
    await client._semaphore.acquire()
    try:
        with pytest.raises(RateLimitedError):
            await client.get_json(URL)
    finally:
        client._semaphore.release()
        await client.aclose()


@respx.mock
async def test_aclose_is_idempotent(client: BaseHTTPClient) -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, json={"ok": True}))
    await client.get_json(URL)
    await client.aclose()
    await client.aclose()  # second close must not raise


@respx.mock
async def test_overall_deadline_skips_retries(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the wall-clock budget spent, a retryable 500 fails on the first
    attempt instead of stacking MAX_RETRIES full-length timeouts."""
    route = respx.get(URL).mock(return_value=httpx.Response(500, json={"error": "down"}))
    deadline_settings = settings.model_copy(update={"OVERALL_DEADLINE_SECONDS": 0.0})
    c = BaseHTTPClient(deadline_settings)
    monkeypatch.setattr(c, "_sleep", _noop_sleep)
    try:
        with pytest.raises(EnsemblApiError):
            await c.get_json(URL)
    finally:
        await c.aclose()
    # MAX_RETRIES=2 would normally mean 3 attempts; the spent budget caps it to 1.
    assert route.call_count == 1


@respx.mock
async def test_connect_timeout_is_short(settings: Settings) -> None:
    """The client is built with a short connect timeout (fast-fail on a stalled
    handshake) distinct from the longer read timeout."""
    c = BaseHTTPClient(settings)
    try:
        assert c._timeout.connect == settings.CONNECT_TIMEOUT
        assert c._timeout.read == settings.REQUEST_TIMEOUT
    finally:
        await c.aclose()
