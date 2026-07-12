"""Resilient async HTTP base client for the Ensembl REST API.

A single lazily-built :class:`httpx.AsyncClient` is shared across all requests
and guarded by an :class:`asyncio.Semaphore` so a fan-out of concurrent calls
cannot overwhelm the upstream (or get the whole process rate-limited). Each
request runs through a jittered exponential-backoff retry loop that:

- retries transient transport faults and ``{429, 500, 502, 503, 504}`` statuses;
- honors ``Retry-After`` (integer seconds *or* HTTP-date) on 429s, waiting at
  least that long;
- fails fast (no retry) on deterministic input errors ``{400, 404, 410, 422}``;
- maps every terminal outcome onto the vep-link exception taxonomy so the MCP
  error layer can classify deterministically.

The fault taxonomy mirrors the rest of the fleet (gnomad-link / litvar-link /
spliceailookup-link). ``_sleep`` is an instance method wrapping
:func:`asyncio.sleep` so tests can monkeypatch it to a no-op for determinism.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from vep_link.api.url_guard import build_host_allowlist, make_url_guard
from vep_link.config import Settings
from vep_link.exceptions import (
    EnsemblApiError,
    RateLimitedError,
    ResponseTooLargeError,
    UpstreamInputError,
    UpstreamTimeoutError,
)
from vep_link.observability.telemetry import record_upstream

# Bounded number of redirect hops httpx will auto-follow; each hop is still
# validated by the URL guard event-hook. Small on purpose (Ensembl REST paths do
# not redirect in normal operation).
_MAX_REDIRECTS = 3

logger = logging.getLogger(__name__)

# Transient upstream faults worth retrying (rate limit + 5xx gateway errors).
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
# Deterministic client errors: the request shape is wrong and will never succeed.
_INPUT_ERROR_STATUS = frozenset({400, 404, 410, 422})


class BaseHTTPClient:
    """Shared async httpx client with bounded concurrency and jittered retry."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # Split timeout: a short connect timeout fails fast on a stalled TCP/TLS
        # handshake, while the (longer) read timeout tolerates legitimately slow
        # batch responses.
        self._timeout = httpx.Timeout(settings.REQUEST_TIMEOUT, connect=settings.CONNECT_TIMEOUT)
        self._semaphore = asyncio.Semaphore(max(1, settings.MAX_CONCURRENCY))
        self._client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()
        # Exact outbound-host allowlist DERIVED from the (operator-overridable)
        # Ensembl base URLs -- never hardcoded, so an override moves it too.
        self._allowed_hosts = build_host_allowlist(settings.VEP_GRCH38_URL, settings.VEP_GRCH37_URL)
        self._max_response_bytes = settings.MAX_RESPONSE_BYTES

    def _attempt_timeout(self, remaining: float) -> httpx.Timeout:
        """Per-attempt timeout, capped to the remaining overall-deadline budget."""
        budget = max(0.5, remaining)
        return httpx.Timeout(
            min(float(self._settings.REQUEST_TIMEOUT), budget),
            connect=min(float(self._settings.CONNECT_TIMEOUT), budget),
        )

    # -- lifecycle ---------------------------------------------------------

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Return the shared client, building it once under a lock."""
        if self._client is None:
            async with self._client_lock:
                if self._client is None:
                    self._client = httpx.AsyncClient(
                        timeout=self._timeout,
                        headers={
                            "Accept": "application/json",
                            "User-Agent": self._settings.USER_AGENT,
                        },
                        # Keep httpx's redirect machinery, but validate every hop
                        # (incl. auto-followed redirects) against the exact host
                        # allowlist; a bad hop raises DisallowedURLError (NON-
                        # RETRYABLE). Bound the hop count defensively.
                        follow_redirects=True,
                        max_redirects=_MAX_REDIRECTS,
                        event_hooks={"request": [make_url_guard(self._allowed_hosts)]},
                    )
        return self._client

    async def aclose(self) -> None:
        """Close the shared client. Idempotent."""
        if self._client is not None:
            client, self._client = self._client, None
            await client.aclose()

    async def __aenter__(self) -> BaseHTTPClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # -- injectable sleep --------------------------------------------------

    async def _sleep(self, seconds: float) -> None:
        """Wrap :func:`asyncio.sleep`; monkeypatched to a no-op in tests."""
        await asyncio.sleep(seconds)

    # -- concurrency bounding ---------------------------------------------

    async def _acquire_slot(self, timeout: float) -> None:
        """Acquire a concurrency permit, or raise on a bounded wait timeout."""
        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=max(0.0, timeout))
        except TimeoutError as exc:
            raise RateLimitedError(
                f"Local concurrency limit saturated (max "
                f"{max(1, self._settings.MAX_CONCURRENCY)} concurrent upstream requests). "
                "Retry with exponential backoff or fan out fewer calls at once."
            ) from exc

    # -- public API --------------------------------------------------------

    async def get_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        *,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """GET ``url`` with ``params``, returning parsed JSON (dict or list)."""

        def _send(client: httpx.AsyncClient, timeout: httpx.Timeout) -> Any:
            return client.stream("GET", url, params=params, headers=headers, timeout=timeout)

        return await self._request(url, _send)

    async def post_json(
        self,
        url: str,
        json_body: Any,
        params: dict[str, Any] | None = None,
        *,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """POST ``json_body`` as JSON to ``url``, returning parsed JSON."""
        merged = {"Content-Type": "application/json", **(headers or {})}

        def _send(client: httpx.AsyncClient, timeout: httpx.Timeout) -> Any:
            return client.stream(
                "POST", url, json=json_body, params=params, headers=merged, timeout=timeout
            )

        return await self._request(url, _send)

    # -- capped streamed read ---------------------------------------------

    async def _read_capped(
        self, send: Any, client: httpx.AsyncClient, timeout: httpx.Timeout
    ) -> Any:
        """Send (streaming) and return parsed JSON, capping the DECODED body.

        Reads status/headers FIRST: on a ``>=400`` status it calls
        ``raise_for_status`` WITHOUT reading the body (so the caller's fixed,
        body-free ``_safe_upstream_input_message`` handles it and no upstream
        prose is surfaced). On success it accumulates ``aiter_bytes`` -- httpx
        auto-decompresses gzip, so the running total bounds *decoded* bytes --
        and raises :class:`ResponseTooLargeError` (NON-RETRYABLE) the instant the
        total exceeds the cap, failing closed rather than truncating. The URL
        guard runs as the client's request event-hook, so a disallowed hop raises
        ``DisallowedURLError`` here at connect time; neither guard exception is an
        ``httpx`` fault, so :meth:`_request` never retries it.
        """
        async with send(client, timeout) as response:
            if response.status_code >= 400:
                response.raise_for_status()
            total = 0
            parts: list[bytes] = []
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > self._max_response_bytes:
                    raise ResponseTooLargeError(
                        f"Upstream response exceeded the {self._max_response_bytes}-byte cap."
                    )
                parts.append(chunk)
        body = b"".join(parts)
        return json.loads(body) if body else None

    # -- retry loop --------------------------------------------------------

    async def _request(
        self,
        url: str,
        send: Any,
    ) -> Any:
        """Run ``send`` through the bounded-concurrency, jittered-retry loop.

        A hard wall-clock budget (``OVERALL_DEADLINE_SECONDS``) caps the total
        time across all attempts: each attempt's timeout is shrunk to the
        remaining budget, and once the budget is spent no further attempt is
        started. This guarantees an unhealthy upstream (a 500-storm or a hung
        connection) surfaces a clean ``upstream_unavailable`` quickly instead of
        stacking ``MAX_RETRIES`` full-length timeouts past the caller's deadline.
        """
        client = await self._ensure_client()
        loop = asyncio.get_running_loop()
        op_deadline = loop.time() + self._settings.OVERALL_DEADLINE_SECONDS
        max_retries = self._settings.MAX_RETRIES
        last_exc: BaseException | None = None

        for attempt in range(max_retries + 1):
            remaining = op_deadline - loop.time()
            # No retries left, or the wall-clock budget is spent: this is the
            # final attempt and any fault must propagate.
            is_last = attempt >= max_retries or remaining <= 0
            if attempt > 0 and remaining <= 0:
                _raise_terminal(last_exc, url)
            # Wait for a concurrency slot with a fresh per-attempt budget
            # (backpressure is independent of the read/deadline budget). Using an
            # absolute deadline here would make a retry after a slow first attempt
            # spuriously report saturation.
            await self._acquire_slot(timeout=float(self._settings.QUEUE_WAIT_TIMEOUT))
            retry_after: float | None = None
            attempt_start = loop.time()
            try:
                return await self._read_capped(send, client, self._attempt_timeout(remaining))
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                status = exc.response.status_code
                if status in _INPUT_ERROR_STATUS:
                    raise UpstreamInputError(_safe_upstream_input_message(status)) from exc
                if status not in _RETRYABLE_STATUS:
                    # Non-retryable 4xx (e.g. 401/403): deterministic input error.
                    raise UpstreamInputError(_safe_upstream_input_message(status)) from exc
                if is_last:
                    if status == 429:
                        raise RateLimitedError(
                            f"Rate limited by upstream (HTTP 429) after retries: {url}"
                        ) from exc
                    raise EnsemblApiError(f"Upstream HTTP {status} for {url}") from exc
                if status == 429:
                    retry_after = _parse_retry_after(exc.response.headers.get("Retry-After"))
            except httpx.TimeoutException as exc:
                last_exc = exc
                if is_last:
                    raise UpstreamTimeoutError(f"Upstream request timed out: {url}") from exc
            except httpx.TransportError as exc:
                last_exc = exc
                if is_last:
                    raise EnsemblApiError(f"Upstream request failed: {exc!s}") from exc
            finally:
                # Accumulate this attempt's upstream wall-time (success or fault)
                # into the request-scoped telemetry, then free the concurrency slot.
                record_upstream((loop.time() - attempt_start) * 1000)
                self._semaphore.release()

            await self._backoff(attempt, retry_after)

        # Unreachable: the loop either returns or raises on the final attempt.
        raise EnsemblApiError(  # pragma: no cover
            f"Retry loop exhausted for {url}: {last_exc!s}"
        )

    async def _backoff(self, attempt: int, retry_after: float | None) -> None:
        """Sleep a jittered exponential delay, honoring a 429 ``Retry-After``."""
        base = self._settings.BACKOFF_BASE_SECONDS
        cap = self._settings.BACKOFF_MAX_SECONDS
        delay = min(base * (2**attempt), cap)
        wait = random.uniform(0, delay)  # noqa: S311 - jitter, not crypto
        if retry_after is not None:
            wait = max(wait, retry_after)
        await self._sleep(wait)


def _raise_terminal(last_exc: BaseException | None, url: str) -> None:
    """Raise the right vep-link error when the overall deadline is exhausted.

    Maps the most recent caught fault onto the exception taxonomy so the MCP
    layer classifies the budget-exhausted outcome the same as a normal
    retries-exhausted outcome.
    """
    if isinstance(last_exc, httpx.TimeoutException):
        raise UpstreamTimeoutError(f"Upstream deadline exceeded (timeout): {url}") from last_exc
    if isinstance(last_exc, httpx.HTTPStatusError):
        status = last_exc.response.status_code
        if status == 429:
            raise RateLimitedError(
                f"Rate limited by upstream (HTTP 429), deadline exceeded: {url}"
            ) from last_exc
        raise EnsemblApiError(f"Upstream HTTP {status}, deadline exceeded: {url}") from last_exc
    raise EnsemblApiError(f"Upstream unavailable, deadline exceeded: {url}") from last_exc


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header value (integer seconds or HTTP-date)."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return max(0.0, float(int(value)))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    delta = (when - datetime.now(UTC)).total_seconds()
    return max(0.0, delta)


def _safe_upstream_input_message(status: int) -> str:
    """Fixed, body-free message for a non-retryable upstream (4xx) rejection.

    The upstream response BODY is deliberately NOT read or interpolated: a
    caller-influenced query can make Ensembl reflect hostile prose (including
    control / zero-width / bidi / NUL code points) into a 4xx body, and echoing
    it verbatim would smuggle attacker-controlled text into a caller-visible
    error message (a defense-in-depth, secondary-surface leak). The HTTP status
    is a bounded, non-attacker-controlled scalar, so it is safe to key a fixed
    message on; the body is neither surfaced nor logged (no-PII-in-logs invariant).
    """
    return f"Upstream rejected the request (HTTP {status})."
