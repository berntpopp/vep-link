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
import logging
import random
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from vep_link.config import Settings
from vep_link.exceptions import (
    EnsemblApiError,
    RateLimitedError,
    UpstreamInputError,
    UpstreamTimeoutError,
)

logger = logging.getLogger(__name__)

# Transient upstream faults worth retrying (rate limit + 5xx gateway errors).
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
# Deterministic client errors: the request shape is wrong and will never succeed.
_INPUT_ERROR_STATUS = frozenset({400, 404, 410, 422})


class BaseHTTPClient:
    """Shared async httpx client with bounded concurrency and jittered retry."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._timeout = httpx.Timeout(settings.REQUEST_TIMEOUT)
        self._semaphore = asyncio.Semaphore(max(1, settings.MAX_CONCURRENCY))
        self._client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()

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
                        follow_redirects=True,
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

        async def _send(client: httpx.AsyncClient) -> httpx.Response:
            return await client.get(url, params=params, headers=headers)

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

        async def _send(client: httpx.AsyncClient) -> httpx.Response:
            return await client.post(url, json=json_body, params=params, headers=merged)

        return await self._request(url, _send)

    # -- retry loop --------------------------------------------------------

    async def _request(
        self,
        url: str,
        send: Any,
    ) -> Any:
        """Run ``send`` through the bounded-concurrency, jittered-retry loop."""
        client = await self._ensure_client()
        loop = asyncio.get_running_loop()
        queue_deadline = loop.time() + self._settings.QUEUE_WAIT_TIMEOUT
        max_retries = self._settings.MAX_RETRIES
        last_exc: BaseException | None = None

        for attempt in range(max_retries + 1):
            await self._acquire_slot(timeout=queue_deadline - loop.time())
            retry_after: float | None = None
            try:
                response = await send(client)
                status = response.status_code
                if status >= 400:
                    response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                status = exc.response.status_code
                if status in _INPUT_ERROR_STATUS:
                    raise UpstreamInputError(_extract_error_message(exc.response, status)) from exc
                if status not in _RETRYABLE_STATUS:
                    # Non-retryable 4xx (e.g. 401/403): deterministic input error.
                    raise UpstreamInputError(_extract_error_message(exc.response, status)) from exc
                if attempt >= max_retries:
                    if status == 429:
                        raise RateLimitedError(
                            f"Rate limited by upstream (HTTP 429) after retries: {url}"
                        ) from exc
                    raise EnsemblApiError(f"Upstream HTTP {status} for {url}") from exc
                if status == 429:
                    retry_after = _parse_retry_after(exc.response.headers.get("Retry-After"))
            except httpx.TimeoutException as exc:
                last_exc = exc
                if attempt >= max_retries:
                    raise UpstreamTimeoutError(f"Upstream request timed out: {url}") from exc
            except httpx.TransportError as exc:
                last_exc = exc
                if attempt >= max_retries:
                    raise EnsemblApiError(f"Upstream request failed: {exc!s}") from exc
            finally:
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


def _extract_error_message(response: httpx.Response, status: int) -> str:
    """Best-effort human-readable message from a 4xx body (Ensembl: ``{"error": ...}``)."""
    try:
        body = response.json()
    except Exception:
        body = None
    if isinstance(body, dict) and body.get("error"):
        return str(body["error"])
    return f"Upstream rejected the request (HTTP {status})."
