"""Tests for vep_link.observability.metrics.

The metrics registry is a small, dependency-free, thread-safe in-process counter
set rendered in Prometheus text-exposition format. These tests use a fresh
``MetricsRegistry`` instance (not the process-wide singleton) so they stay
deterministic and isolated.
"""

from __future__ import annotations

from vep_link.observability.metrics import MetricsRegistry, render_circuit_state


def test_record_tool_call_counts_success_and_error() -> None:
    reg = MetricsRegistry()
    reg.record_tool_call("annotate_variant", outcome="success", code=None, elapsed_ms=12)
    reg.record_tool_call("annotate_variant", outcome="success", code=None, elapsed_ms=8)
    reg.record_tool_call("annotate_variant", outcome="error", code="not_found", elapsed_ms=3)
    text = reg.render_prometheus()
    assert 'vep_link_tool_calls_total{outcome="success",tool="annotate_variant"} 2' in text
    assert 'vep_link_tool_calls_total{outcome="error",tool="annotate_variant"} 1' in text


def test_record_tool_call_counts_error_codes() -> None:
    reg = MetricsRegistry()
    reg.record_tool_call("resolve_variant", outcome="error", code="not_found", elapsed_ms=1)
    reg.record_tool_call("resolve_variant", outcome="error", code="not_found", elapsed_ms=1)
    reg.record_tool_call("resolve_variant", outcome="error", code="rate_limited", elapsed_ms=1)
    text = reg.render_prometheus()
    assert 'vep_link_tool_errors_total{code="not_found",tool="resolve_variant"} 2' in text
    assert 'vep_link_tool_errors_total{code="rate_limited",tool="resolve_variant"} 1' in text


def test_latency_histogram_buckets_and_sum() -> None:
    reg = MetricsRegistry()
    reg.record_tool_call("annotate_variant", outcome="success", code=None, elapsed_ms=12)
    reg.record_tool_call("annotate_variant", outcome="success", code=None, elapsed_ms=300)
    text = reg.render_prometheus()
    # Histogram exposition: _bucket (cumulative), _sum, _count.
    assert "vep_link_tool_latency_ms_bucket" in text
    assert 'vep_link_tool_latency_ms_count{tool="annotate_variant"} 2' in text
    assert 'vep_link_tool_latency_ms_sum{tool="annotate_variant"} 312' in text
    # 12ms falls in le=25 (and every larger bucket); 300ms only in le=500+.
    assert 'vep_link_tool_latency_ms_bucket{le="25",tool="annotate_variant"} 1' in text
    assert 'vep_link_tool_latency_ms_bucket{le="+Inf",tool="annotate_variant"} 2' in text


def test_render_includes_help_and_type_headers() -> None:
    reg = MetricsRegistry()
    reg.record_tool_call("annotate_variant", outcome="success", code=None, elapsed_ms=1)
    text = reg.render_prometheus()
    assert "# HELP vep_link_tool_calls_total" in text
    assert "# TYPE vep_link_tool_calls_total counter" in text
    assert "# TYPE vep_link_tool_latency_ms histogram" in text


def test_empty_registry_renders_headers_without_samples() -> None:
    reg = MetricsRegistry()
    text = reg.render_prometheus()
    # No calls recorded -> no sample lines, but valid (header-only) output.
    assert "vep_link_tool_calls_total{" not in text
    assert isinstance(text, str)


def test_render_circuit_state_gauge() -> None:
    snapshot = {
        "GRCh38": {"circuit": "closed"},
        "GRCh37": {"circuit": "open"},
    }
    text = render_circuit_state(snapshot)
    assert "# TYPE vep_link_circuit_state gauge" in text
    assert 'vep_link_circuit_state{assembly="GRCh38",state="closed"} 1' in text
    assert 'vep_link_circuit_state{assembly="GRCh37",state="open"} 1' in text
    # A host reports exactly one active state; the others are 0.
    assert 'vep_link_circuit_state{assembly="GRCh38",state="open"} 0' in text


def test_label_values_are_escaped() -> None:
    reg = MetricsRegistry()
    # A pathological code with a quote/backslash must not break the exposition.
    reg.record_tool_call('weird"\\tool', outcome="error", code='b"d', elapsed_ms=1)
    text = reg.render_prometheus()
    assert 'b\\"d' in text
    assert 'weird\\"\\\\tool' in text
