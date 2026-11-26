"""Request-scoped telemetry: cache status + upstream time (ContextVar based).

Each MCP tool call resets these at the start (see ``run_mcp_tool``); the HTTP
client accumulates upstream time, and the cache wrapper classifies each call as
miss/hit/coalesced. ContextVars are copied per asyncio task, so concurrent
requests under the bounded-concurrency semaphore do not clobber one another.

**Why this is not the obvious implementation.** ``async_lru`` runs the wrapped
coroutine inside ``loop.create_task`` on a miss, and ``create_task`` *copies* the
current context. Two consequences shape the design:

* ``cache_status`` is classified in the WRAPPER (the requesting task's own
  context) -- not inside the cached body, whose ``ContextVar.set`` would land in
  the throwaway child-task copy and never reach the request.
* ``upstream_ms`` is accumulated into a **mutable single-element list** held by
  the ContextVar. ``copy_context`` shares the list object by reference, so HTTP
  time recorded inside the cache's child task mutates the same list the request
  reads back. A plain ``float`` ContextVar with ``.set(get() + x)`` would be lost
  across that task boundary (it would always read 0 on a cache miss -- the very
  case ``upstream_ms`` exists to measure).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import suppress
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from async_lru import alru_cache

_cache_status: ContextVar[str] = ContextVar("vep_link_cache_status", default="miss")
# A mutable accumulator (not a bare float) so the value survives the asyncio.Task
# boundary async_lru introduces on a miss -- see the module docstring. No default:
# a context that never called reset() reads as 0 via the LookupError guards below.
_upstream_ms: ContextVar[list[float]] = ContextVar("vep_link_upstream_ms")
# Fetch times of the cache entries this request was served from (same container trick).
_fetched_at: ContextVar[list[datetime]] = ContextVar("vep_link_fetched_at")


def reset() -> None:
    """Reset both telemetry vars; call once at the start of each tool body."""
    _cache_status.set("miss")
    _upstream_ms.set([0.0])
    _fetched_at.set([])


def set_cache_status(status: str) -> None:
    _cache_status.set(status)


def get_cache_status() -> str:
    return _cache_status.get()


def record_upstream(elapsed_ms: float) -> None:
    """Add one upstream attempt's wall-time (ms) to the request accumulator.

    Mutates the shared container in place (not ``.set``) so the accumulation is
    visible across the asyncio.Task boundary async_lru creates on a cache miss.
    """
    try:
        acc = _upstream_ms.get()
    except LookupError:
        acc = [0.0]
        _upstream_ms.set(acc)
    acc[0] += max(0.0, elapsed_ms)


def get_upstream_ms() -> int:
    try:
        return int(_upstream_ms.get()[0])
    except LookupError:
        return 0


def retrieved_at() -> str:
    """When the data of this request was fetched upstream (RFC 3339 UTC, ``Z``).

    The oldest fetch among the cache entries the request read (Response-Envelope v1.2: a
    cache hit keeps the time of the fetch that filled the entry); now for an uncached call.
    """
    try:
        fetched = _fetched_at.get()
    except LookupError:
        fetched = []
    oldest = min(fetched) if fetched else datetime.now(UTC)
    return oldest.isoformat(timespec="seconds").replace("+00:00", "Z")


def telemetry_cache[R](
    impl: Callable[..., Awaitable[R]], *, maxsize: int, ttl: float
) -> Callable[..., Awaitable[R]]:
    """Wrap ``impl`` in ``alru_cache`` and classify each call as miss/hit/coalesced.

    Classification runs entirely in the REQUESTING task's context (so the
    ``ContextVar`` write reaches the request). Before delegating, the wrapper
    inspects the live cache:

    * key absent from the cache       -> ``"miss"`` (this request computes it);
    * key present and a computation    -> ``"coalesced"`` (this request joins an
      is still in-flight (``inflight``)    in-flight task started by another and
                                           issues no upstream call of its own);
    * key present and not in-flight    -> ``"hit"`` (served from a completed
                                           entry).

    ``inflight`` is a plain ``set`` (a shared object, not a ContextVar) so the
    inner body can register/clear keys and the wrapper can observe them across the
    cache's child-task boundary.
    """
    inflight: set[tuple[Any, ...]] = set()

    async def marking_impl(*args: Any) -> tuple[R, datetime]:
        inflight.add(args)
        try:
            # The fetch time is cached with the value, so a hit reports it unchanged.
            started = datetime.now(UTC)
            return await impl(*args), started
        finally:
            inflight.discard(args)

    cached = alru_cache(maxsize=maxsize, ttl=ttl)(marking_impl)

    async def wrapper(*args: Any) -> R:
        if cached.cache_contains(*args):
            set_cache_status("coalesced" if args in inflight else "hit")
        else:
            set_cache_status("miss")
        value, fetched = await cached(*args)
        with suppress(LookupError):  # no reset() in this context: nothing reads it
            _fetched_at.get().append(fetched)
        return value

    return wrapper
