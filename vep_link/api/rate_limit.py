"""Client-side rate limit for one Ensembl host (#130).

A GCRA token bucket: ``rate`` requests/s sustained with a burst of ``burst``.
Each :meth:`TokenBucket.acquire` reserves its start slot synchronously (no await
between reading and writing the schedule), so concurrent callers on one event
loop never over-book it. :meth:`TokenBucket.observe` adapts the schedule to the
upstream's own signals: ``Retry-After`` pauses the host, a low
``X-RateLimit-Remaining`` stretches the interval so the remaining budget lasts
until ``X-RateLimit-Reset``, and a 429 without ``Retry-After`` pauses with jitter.
"""

from __future__ import annotations

import random
import time
from collections.abc import Awaitable, Callable, Mapping

from vep_link.exceptions import RateLimitedError

# Pause after a 429 that carries no Retry-After (seconds, before jitter).
_BARE_429_PAUSE_S = 1.0


class TokenBucket:
    """Schedule requests to one host at most ``rate``/s with a ``burst`` allowance."""

    def __init__(
        self,
        rate: float,
        burst: int,
        sleep: Callable[[float], Awaitable[None]],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base_interval = 1.0 / rate if rate > 0 else 0.0
        self._interval = self._base_interval
        self._burst = max(1, burst)
        self._sleep = sleep
        self._clock = clock
        self._tat = 0.0  # theoretical arrival time of the next request
        self._paused_until = 0.0

    async def acquire(self, max_wait: float) -> None:
        """Wait for a send slot; raise :class:`RateLimitedError` if it is beyond ``max_wait``."""
        if self._base_interval == 0.0:
            return
        now = self._clock()
        tat = max(self._tat, now)
        start = max(tat - (self._burst - 1) * self._interval, now, self._paused_until)
        wait = start - now
        if wait > max_wait:
            raise RateLimitedError(
                f"Client-side Ensembl rate limit: the next request slot is {wait:.0f} s "
                "away, beyond this call's deadline. Retry later or send fewer calls at once."
            )
        self._tat = max(tat, start) + self._interval
        if wait > 0:
            await self._sleep(wait)

    def observe(self, status: int, headers: Mapping[str, str], retry_after: float | None) -> None:
        """Adapt the schedule to one upstream response's status and rate headers."""
        if self._base_interval == 0.0:
            return
        now = self._clock()
        if retry_after is not None:
            self._paused_until = max(self._paused_until, now + retry_after)
        elif status == 429:
            pause = _BARE_429_PAUSE_S * (1 + random.random())  # noqa: S311 - jitter
            self._paused_until = max(self._paused_until, now + pause)
        remaining = _as_float(headers.get("X-RateLimit-Remaining"))
        reset = _as_float(headers.get("X-RateLimit-Reset"))
        if remaining is None or reset is None:
            return
        if remaining < 1:
            self._paused_until = max(self._paused_until, now + reset)
            return
        # Spread what is left of the upstream window evenly until it resets.
        self._interval = max(self._base_interval, reset / remaining)


def _as_float(value: str | None) -> float | None:
    try:
        return max(0.0, float(value)) if value is not None else None
    except ValueError:
        return None
