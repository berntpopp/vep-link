"""Tests for request-scoped cache/upstream telemetry (``observability.telemetry``).

These pin the three-state cache classification (miss/hit/coalesced) and the
upstream-time accumulator. The non-obvious invariant under test: ``alru_cache``
runs the wrapped coroutine inside ``loop.create_task`` (a copied context), so the
classification must be decided in the REQUESTING task's context (not inside the
cached body), and ``upstream_ms`` must survive that task boundary.
"""

from __future__ import annotations

import asyncio
import contextvars
from typing import Any


async def test_telemetry_cache_reports_miss_then_hit() -> None:
    from vep_link.observability import telemetry as t

    calls = {"n": 0}

    async def impl(x: int) -> int:
        calls["n"] += 1
        return x * 2

    cached = t.telemetry_cache(impl, maxsize=8, ttl=60.0)

    t.reset()
    assert await cached(3) == 6
    assert t.get_cache_status() == "miss"

    t.reset()
    assert await cached(3) == 6
    assert t.get_cache_status() == "hit"
    assert calls["n"] == 1


async def test_telemetry_cache_reports_coalesced() -> None:
    from vep_link.observability import telemetry as t

    started = asyncio.Event()
    release = asyncio.Event()

    async def impl(x: int) -> int:
        started.set()
        await release.wait()
        return x

    cached = t.telemetry_cache(impl, maxsize=8, ttl=60.0)

    async def first() -> tuple[int, str]:
        t.reset()
        return await cached(1), t.get_cache_status()

    async def second() -> tuple[int, str]:
        await started.wait()
        t.reset()
        coro = cached(1)
        release.set()
        return await coro, t.get_cache_status()

    (_, s1), (_, s2) = await asyncio.gather(first(), second())
    assert {s1, s2} == {"miss", "coalesced"}


def test_record_upstream_accumulates() -> None:
    from vep_link.observability import telemetry as t

    t.reset()
    t.record_upstream(12.0)
    t.record_upstream(8.0)
    assert t.get_upstream_ms() == 20


async def test_upstream_ms_survives_cache_task_boundary() -> None:
    # async_lru runs the inner coroutine in loop.create_task (a copied context);
    # upstream time recorded inside that child task must still be visible to the
    # requesting context. This guards the mutable-container accumulator design.
    from vep_link.observability import telemetry as t

    async def impl(x: int) -> int:
        t.record_upstream(15.0)  # runs inside alru's child task on a miss
        return x

    cached = t.telemetry_cache(impl, maxsize=8, ttl=60.0)

    t.reset()
    await cached(1)
    assert t.get_cache_status() == "miss"
    assert t.get_upstream_ms() == 15


def test_get_upstream_ms_defaults_to_zero_in_fresh_context() -> None:
    # A fresh context that never called reset() reports 0, never raises. Run in
    # an isolated Context so it does not observe a sibling sync test's leftover.
    from vep_link.observability import telemetry as t

    assert contextvars.Context().run(t.get_upstream_ms) == 0


async def test_cache_hit_keeps_the_original_fetch_time(monkeypatch: Any) -> None:
    """Response-Envelope v1.2: retrieved_at is the fetch that filled the entry."""
    from datetime import UTC, datetime

    from vep_link.observability import telemetry as t

    clock = iter(datetime(2026, 11, 20, 8, 0, s, tzinfo=UTC) for s in range(60))

    class FakeDatetime:
        @staticmethod
        def now(tz: Any = None) -> datetime:
            return next(clock)

    monkeypatch.setattr(t, "datetime", FakeDatetime)

    async def impl(x: int) -> int:
        return x

    cached = t.telemetry_cache(impl, maxsize=8, ttl=60.0)
    t.reset()
    await cached(1)
    assert t.retrieved_at() == "2026-11-20T08:00:00Z"
    t.reset()
    await cached(1)  # hit, later
    await cached(2)  # miss, fetched later still: the oldest fetch wins
    assert t.retrieved_at() == "2026-11-20T08:00:00Z"
    t.reset()
    assert t.retrieved_at() == "2026-11-20T08:00:02Z"  # uncached call: now
