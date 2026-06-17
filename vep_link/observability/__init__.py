"""Cross-cutting observability for vep-link (metrics, instrumentation).

Kept separate from the data path: importing this package has no side effects and
no third-party dependencies, so it is safe to import from the MCP error boundary
and the FastAPI host alike.
"""

from __future__ import annotations

from vep_link.observability.metrics import METRICS, MetricsRegistry, render_circuit_state

__all__ = ["METRICS", "MetricsRegistry", "render_circuit_state"]
