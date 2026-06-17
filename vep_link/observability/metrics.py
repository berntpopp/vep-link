"""In-process metrics for vep-link, rendered in Prometheus text format.

A deliberately tiny, dependency-free registry (no ``prometheus_client``, no OTel
SDK) that records the production-baseline signals a fleet operator needs:

* ``vep_link_tool_calls_total{tool,outcome}`` — call volume + success rate.
* ``vep_link_tool_errors_total{tool,code}`` — error-code distribution.
* ``vep_link_tool_latency_ms`` — a latency histogram per tool (``_bucket`` /
  ``_sum`` / ``_count``).
* ``vep_link_circuit_state{assembly,state}`` — the live circuit-breaker state per
  assembly, rendered on demand from the health snapshot (see
  :func:`render_circuit_state`).

The registry is process-wide (:data:`METRICS`) and thread-safe via a single
lock; the work per record is O(buckets) and contention is negligible at MCP call
rates. Scrape it through the host's ``GET /metrics`` ops endpoint.
"""

from __future__ import annotations

import threading
from typing import Any

__all__ = ["METRICS", "MetricsRegistry", "render_circuit_state"]

# Upper bounds (milliseconds) for the latency histogram. Cumulative ``le``
# buckets per Prometheus convention; ``+Inf`` is appended at render time.
_LATENCY_BUCKETS_MS: tuple[int, ...] = (5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000)


def _escape_label_value(value: str) -> str:
    """Escape a Prometheus label value (backslash, double-quote, newline)."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _labels(pairs: dict[str, str]) -> str:
    """Render a label set as ``{k="v",...}`` with keys sorted for stable output."""
    inner = ",".join(f'{k}="{_escape_label_value(v)}"' for k, v in sorted(pairs.items()))
    return "{" + inner + "}"


class MetricsRegistry:
    """Thread-safe counters + per-tool latency histograms (Prometheus text)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # (tool, outcome) -> count
        self._calls: dict[tuple[str, str], int] = {}
        # (tool, code) -> count
        self._errors: dict[tuple[str, str], int] = {}
        # tool -> [count per bucket incl. +Inf]
        self._latency_buckets: dict[str, list[int]] = {}
        # tool -> (sum_ms, count)
        self._latency_totals: dict[str, list[int]] = {}

    def record_tool_call(
        self, tool: str, *, outcome: str, code: str | None, elapsed_ms: int
    ) -> None:
        """Record one tool call: volume, optional error code, and latency."""
        with self._lock:
            self._calls[(tool, outcome)] = self._calls.get((tool, outcome), 0) + 1
            if outcome == "error" and code:
                self._errors[(tool, code)] = self._errors.get((tool, code), 0) + 1
            self._observe_latency(tool, max(0, int(elapsed_ms)))

    def _observe_latency(self, tool: str, elapsed_ms: int) -> None:
        buckets = self._latency_buckets.get(tool)
        if buckets is None:
            buckets = [0] * (len(_LATENCY_BUCKETS_MS) + 1)  # +1 for +Inf
            self._latency_buckets[tool] = buckets
            self._latency_totals[tool] = [0, 0]
        for i, upper in enumerate(_LATENCY_BUCKETS_MS):
            if elapsed_ms <= upper:
                buckets[i] += 1
        buckets[-1] += 1  # +Inf always
        totals = self._latency_totals[tool]
        totals[0] += elapsed_ms
        totals[1] += 1

    def render_prometheus(self) -> str:
        """Render all metrics as a Prometheus text-exposition string."""
        with self._lock:
            calls = dict(self._calls)
            errors = dict(self._errors)
            latency_buckets = {t: list(b) for t, b in self._latency_buckets.items()}
            latency_totals = {t: list(v) for t, v in self._latency_totals.items()}

        lines: list[str] = []
        lines += _render_counter(
            "vep_link_tool_calls_total",
            "Total MCP tool calls by tool and outcome.",
            calls,
            ("tool", "outcome"),
        )
        lines += _render_counter(
            "vep_link_tool_errors_total",
            "Total MCP tool errors by tool and deterministic error code.",
            errors,
            ("tool", "code"),
        )
        lines += _render_latency(latency_buckets, latency_totals)
        return "\n".join(lines) + ("\n" if lines else "")


def _render_counter(
    name: str, help_text: str, data: dict[tuple[str, str], int], label_names: tuple[str, str]
) -> list[str]:
    """Render a labelled counter family (HELP/TYPE + one line per label set)."""
    lines = [f"# HELP {name} {help_text}", f"# TYPE {name} counter"]
    for key in sorted(data):
        labels = _labels({label_names[0]: key[0], label_names[1]: key[1]})
        lines.append(f"{name}{labels} {data[key]}")
    return lines


def _render_latency(buckets: dict[str, list[int]], totals: dict[str, list[int]]) -> list[str]:
    """Render the per-tool latency histogram family."""
    name = "vep_link_tool_latency_ms"
    lines = [
        f"# HELP {name} MCP tool wall-clock latency in milliseconds.",
        f"# TYPE {name} histogram",
    ]
    edges = [str(b) for b in _LATENCY_BUCKETS_MS] + ["+Inf"]
    for tool in sorted(buckets):
        for edge, count in zip(edges, buckets[tool], strict=True):
            labels = _labels({"tool": tool, "le": edge})
            lines.append(f"{name}_bucket{labels} {count}")
        total_sum, total_count = totals[tool]
        tool_label = _labels({"tool": tool})
        lines.append(f"{name}_sum{tool_label} {total_sum}")
        lines.append(f"{name}_count{tool_label} {total_count}")
    return lines


# Circuit-breaker states a host can be in (see ``vep_link.api.health``). Rendered
# as a one-hot gauge so dashboards can alert on ``state="open"``.
_CIRCUIT_STATES: tuple[str, ...] = ("closed", "half_open", "open")


def render_circuit_state(snapshot: dict[str, Any]) -> str:
    """Render per-assembly circuit-breaker state as a one-hot Prometheus gauge.

    ``snapshot`` is :meth:`vep_link.api.health.UpstreamHealth.snapshot` output:
    ``{assembly: {"circuit": state, ...}}``. Each assembly emits one ``1`` for its
    active state and ``0`` for the others, so a transition is visible as the
    series that flips to ``1``.
    """
    name = "vep_link_circuit_state"
    lines = [
        f"# HELP {name} Upstream circuit-breaker state per assembly (1=active).",
        f"# TYPE {name} gauge",
    ]
    for assembly in sorted(snapshot):
        host = snapshot[assembly]
        active = host.get("circuit") if isinstance(host, dict) else None
        for state in _CIRCUIT_STATES:
            labels = _labels({"assembly": assembly, "state": state})
            lines.append(f"{name}{labels} {1 if state == active else 0}")
    return "\n".join(lines) + "\n"


# Process-wide singleton used by the MCP error boundary and the /metrics endpoint.
METRICS = MetricsRegistry()
