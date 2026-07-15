"""Per-assembly upstream health monitor with a circuit breaker.

Tracks the health of the two Ensembl REST hosts (GRCh38 ``rest.ensembl.org`` and
GRCh37 ``grch37.rest.ensembl.org``) so the MCP layer can warn the LLM consumer
early and fail fast when a host is degraded, instead of discovering an outage one
slow tool failure at a time.

Two signals feed the breaker:

- **Passive**: real tool-call outcomes (``record_success`` / ``record_failure``).
- **Active**: a cheap ``/info/ping`` probe per host (``refresh``), run by a
  background poller in the server lifespan. Never probed per tool call.

The breaker is the standard closed -> open -> half_open cycle:

- ``closed``  — healthy; calls flow normally.
- ``open``    — degraded; ``allow`` returns ``False`` so callers fail fast. A
  ``record_failure`` while already open is a no-op so a fail-fast cannot keep
  extending the cooldown and starve recovery.
- ``half_open`` — after the cooldown, ``allow`` returns ``True`` once to let a
  probe test recovery; a success closes the breaker, a failure re-opens it.

A monotonic ``clock`` is injected for deterministic breaker tests.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from vep_link.config import Settings
from vep_link.models.enums import GenomeBuild

if TYPE_CHECKING:
    from vep_link.api.base_client import BaseHTTPClient

_ASSEMBLIES: tuple[str, ...] = (GenomeBuild.GRCH38.value, GenomeBuild.GRCH37.value)
_INFO_PING_PATH = "/info/ping"


@dataclass
class _HostState:
    """Circuit-breaker bookkeeping for a single Ensembl host."""

    state: str = "closed"
    consecutive_failures: int = 0
    opened_at: float = 0.0
    reachable: bool | None = None
    checked_at: str | None = None
    latency_ms: int | None = None
    last_error: str | None = None


@dataclass
class UpstreamHealth:
    """Circuit-breaker-backed health view over the Ensembl REST hosts."""

    settings: Settings
    http: BaseHTTPClient | None = None
    clock: Callable[[], float] = time.monotonic
    _hosts: dict[str, _HostState] = field(default_factory=dict)
    _owns_http: bool = False

    def __post_init__(self) -> None:
        self._hosts = {name: _HostState() for name in _ASSEMBLIES}
        if self.http is None:
            # A dedicated short-timeout, no-retry client so a probe of a hung host
            # fails fast and never stacks retries.
            from vep_link.api.base_client import BaseHTTPClient

            probe = self.settings.model_copy(
                update={
                    "REQUEST_TIMEOUT": int(self.settings.HEALTH_PROBE_TIMEOUT),
                    "CONNECT_TIMEOUT": min(
                        self.settings.CONNECT_TIMEOUT, self.settings.HEALTH_PROBE_TIMEOUT
                    ),
                    "OVERALL_DEADLINE_SECONDS": self.settings.HEALTH_PROBE_TIMEOUT + 1.0,
                    "MAX_RETRIES": 0,
                }
            )
            self.http = BaseHTTPClient(probe)
            self._owns_http = True

    # -- lifecycle ---------------------------------------------------------

    async def aclose(self) -> None:
        if self._owns_http and self.http is not None:
            await self.http.aclose()

    # -- breaker transitions ----------------------------------------------

    def _host(self, assembly: str | GenomeBuild) -> _HostState:
        key = assembly.value if isinstance(assembly, GenomeBuild) else str(assembly)
        return self._hosts.setdefault(key, _HostState())

    def record_success(self, assembly: str | GenomeBuild) -> None:
        st = self._host(assembly)
        st.state = "closed"
        st.consecutive_failures = 0
        st.last_error = None

    def record_failure(self, assembly: str | GenomeBuild, error: object | None = None) -> None:
        st = self._host(assembly)
        if error is not None:
            # Store ONLY the exception class name -- a bounded, server-controlled
            # identifier -- never str(error). The message can embed an upstream
            # URL, httpx transport text, or a reflected Ensembl 4xx body, any of
            # which would leak verbatim through the vep://health resource, the
            # check_upstream_health tool, AND get_capabilities.upstream (all read
            # this stored value). Sanitizing on storage covers every reader; a
            # fixed short form (the class name) is preferred over str(error)
            # because the raw text can carry injection prose the sanitizer keeps.
            st.last_error = type(error).__name__
        if st.state == "open":
            # Already open: do not extend the cooldown window.
            return
        if st.state == "half_open":
            st.state = "open"
            st.opened_at = self.clock()
            st.consecutive_failures += 1
            return
        st.consecutive_failures += 1
        if st.consecutive_failures >= self.settings.CIRCUIT_FAILURE_THRESHOLD:
            st.state = "open"
            st.opened_at = self.clock()

    def allow(self, assembly: str | GenomeBuild) -> bool:
        """Whether a call to ``assembly`` should proceed (fail fast when open)."""
        st = self._host(assembly)
        if st.state == "open":
            if self.clock() - st.opened_at >= self.settings.CIRCUIT_COOLDOWN_SECONDS:
                st.state = "half_open"
                return True
            return False
        return True

    def _accepting(self, st: _HostState) -> bool:
        """Whether ``allow`` WOULD currently permit a call — without mutating state.

        Honesty for the snapshot: an ``open`` breaker whose cooldown has elapsed
        still lets the next call through (it transitions to ``half_open`` on
        ``allow``). Reporting ``circuit: open`` while calls pass is the audit's
        "open-but-passing" confusion; surfacing ``accepting`` alongside the raw
        circuit state makes the two facts consistent for a reader.
        """
        if st.state == "open":
            return self.clock() - st.opened_at >= self.settings.CIRCUIT_COOLDOWN_SECONDS
        return True

    # -- active probe ------------------------------------------------------

    async def probe(self, assembly: str | GenomeBuild) -> bool:
        """Ping ``/info/ping`` on a host; update the breaker; return reachability."""
        key = assembly.value if isinstance(assembly, GenomeBuild) else str(assembly)
        st = self._host(key)
        url = f"{self.settings.base_url(key)}{_INFO_PING_PATH}"
        assert self.http is not None
        start = self.clock()
        try:
            await self.http.get_json(url, {"content-type": "application/json"})
        except Exception as exc:
            st.reachable = False
            st.checked_at = _now_iso()
            st.latency_ms = None
            self.record_failure(key, exc)
            return False
        st.reachable = True
        st.checked_at = _now_iso()
        st.latency_ms = int((self.clock() - start) * 1000)
        self.record_success(key)
        return True

    async def refresh(self) -> None:
        """Probe every host (used by the background poller)."""
        for assembly in _ASSEMBLIES:
            await self.probe(assembly)

    # -- views -------------------------------------------------------------

    def _status(self, st: _HostState) -> str:
        """Public status for a host.

        Deliberately *more sensitive* than the breaker: a single failed probe
        flips the status to ``degraded`` (early warning) even before the breaker
        trips at ``CIRCUIT_FAILURE_THRESHOLD``. The breaker (``allow``) governs the
        stronger fail-fast action and stays conservative to avoid flapping.
        """
        if st.state == "open":
            return "down"
        if st.state == "half_open":
            return "recovering"
        if st.reachable is False:
            return "degraded"
        return "ok"

    def status_for(self, assembly: str | GenomeBuild) -> str:
        return self._status(self._host(assembly))

    def snapshot(self) -> dict[str, Any]:
        return {name: self._host_view(name) for name in _ASSEMBLIES}

    def _host_view(self, assembly: str) -> dict[str, Any]:
        st = self._host(assembly)
        return {
            "status": self._status(st),
            "circuit": st.state,
            # Honest companion to ``circuit``: whether the breaker would let the
            # next call through right now (an open-but-cooled-down breaker does).
            "accepting": self._accepting(st),
            "reachable": st.reachable,
            "checked_at": st.checked_at,
            "latency_ms": st.latency_ms,
            "last_error": st.last_error,
        }

    def meta_hint(self) -> dict[str, Any]:
        """Compact always-on ``_meta.upstream`` payload.

        Per-host status plus a human-readable ``advice`` line naming a healthy
        fallback assembly when something is not ``ok``.
        """
        statuses = {name: self.status_for(name) for name in _ASSEMBLIES}
        hint: dict[str, Any] = dict(statuses)
        checked = [c for n in _ASSEMBLIES if (c := self._host(n).checked_at) is not None]
        hint["checked_at"] = max(checked) if checked else None
        degraded = [n for n, s in statuses.items() if s not in ("ok", "unknown")]
        if degraded:
            healthy = [n for n, s in statuses.items() if s == "ok"]
            advice = f"Ensembl {', '.join(degraded)} is degraded."
            if healthy:
                # Endpoint-honest: health is tracked PER HOST via /info/ping, not
                # per endpoint. A host answering ping does NOT prove the specific
                # failed endpoint (e.g. variant_recoder) works there — a single-
                # endpoint outage can hit both builds equally, so "retry the other
                # build" must not be stated as a guaranteed fix (audit misdirection).
                advice += (
                    f" The {', '.join(healthy)} host answered a health ping (/info/ping),"
                    " but that does not confirm the specific failed endpoint works there;"
                    " retry that build only if a result on it is acceptable, and treat a"
                    " repeat failure as an endpoint-wide (not host-specific) outage."
                )
            else:
                advice += " Retry shortly with backoff."
            hint["advice"] = advice
        return hint


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
