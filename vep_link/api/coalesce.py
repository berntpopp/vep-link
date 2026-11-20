"""Micro-batching of concurrent single-item Ensembl POSTs (#130).

Callers that arrive within ``window_s`` of each other for the same key (endpoint
URL + query options) share ONE upstream POST of up to ``max_items`` distinct
items; each caller gets back only the records for its own item. A whole-POST
input rejection (HTTP 400) is split into per-item POSTs so one bad item cannot
fail the others; any other whole-POST failure reaches every waiter.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Hashable
from typing import Any

import structlog

from vep_link.exceptions import UpstreamInputError

logger = structlog.get_logger("vep_link.api.coalesce")

Post = Callable[[list[str]], Awaitable[Any]]
Match = Callable[[dict[str, Any]], str | None]
_Batch = dict[str, list[asyncio.Future[list[dict[str, Any]]]]]


class MicroBatcher:
    """Merge concurrent ``submit`` calls per key into one POST per window."""

    def __init__(self, window_s: float, max_items: int) -> None:
        self._window_s = max(0.0, window_s)
        self._max_items = max(1, max_items)
        self._pending: dict[Hashable, _Batch] = {}
        self._running: set[asyncio.Task[None]] = set()  # strong refs until done

    async def submit(self, key: Hashable, item: str, post: Post, match: Match) -> list[dict]:
        """Queue ``item`` under ``key``; return the records ``match`` assigns to it."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[list[dict[str, Any]]] = loop.create_future()
        batch = self._pending.get(key)
        if batch is None:
            batch = self._pending[key] = {}
            loop.call_later(self._window_s, self._flush, key, batch, post, match)
        batch.setdefault(item, []).append(future)
        if len(batch) >= self._max_items:
            self._flush(key, batch, post, match)
        return await future

    def _flush(self, key: Hashable, batch: _Batch, post: Post, match: Match) -> None:
        # A size-triggered flush already sent this batch; its timer must not
        # flush a newer batch that reuses the key.
        if self._pending.get(key) is not batch:
            return
        del self._pending[key]
        task = asyncio.get_running_loop().create_task(self._send(batch, post, match))
        self._running.add(task)
        task.add_done_callback(self._running.discard)

    async def _send(self, batch: _Batch, post: Post, match: Match) -> None:
        items = list(batch)
        try:
            records = await post(items)
        except UpstreamInputError as exc:
            if len(items) == 1:
                _settle(batch, exc=exc)
                return
            logger.info("ensembl_batch_split", items=len(items))
            await asyncio.gather(*(self._send({i: batch[i]}, post, match) for i in items))
            return
        except Exception as exc:  # every waiter sees the whole-POST failure
            _settle(batch, exc=exc)
            return
        except BaseException:  # cancelled: never leave a waiter hanging
            for futures in batch.values():
                for future in futures:
                    future.cancel()
            raise
        grouped: dict[str, list[dict[str, Any]]] = {}
        for record in records if isinstance(records, list) else []:
            if isinstance(record, dict) and (owner := match(record)) in batch:
                grouped.setdefault(owner, []).append(record)
        for item, futures in batch.items():
            for future in futures:
                if not future.done():
                    future.set_result(grouped.get(item, []))


def _settle(batch: _Batch, *, exc: BaseException) -> None:
    for futures in batch.values():
        for future in futures:
            if not future.done():
                future.set_exception(exc)
