"""Tests for the upstream-health circuit-breaker monitor."""

from __future__ import annotations

import httpx
import pytest
import respx

from vep_link.api.health import UpstreamHealth
from vep_link.config import Settings
from vep_link.models.enums import GenomeBuild

GRCH38_PING = "https://rest.ensembl.org/info/ping"
GRCH37_PING = "https://grch37.rest.ensembl.org/info/ping"


class FakeClock:
    """A manually-advanced monotonic clock for deterministic breaker timing."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def health_settings() -> Settings:
    return Settings(CIRCUIT_FAILURE_THRESHOLD=3, CIRCUIT_COOLDOWN_SECONDS=30.0)


def test_starts_closed_ok(health_settings: Settings) -> None:
    h = UpstreamHealth(health_settings, clock=FakeClock())
    assert h.status_for("GRCh38") == "ok"
    assert h.allow("GRCh38") is True
    assert h.snapshot()["GRCh38"]["circuit"] == "closed"


def test_trips_open_after_threshold(health_settings: Settings) -> None:
    clock = FakeClock()
    h = UpstreamHealth(health_settings, clock=clock)
    for _ in range(3):
        h.record_failure("GRCh38", error="boom")
    assert h.status_for("GRCh38") == "down"
    assert h.allow("GRCh38") is False
    assert h.snapshot()["GRCh38"]["last_error"] == "boom"
    # The other host is unaffected.
    assert h.status_for("GRCh37") == "ok"


def test_failure_while_open_does_not_extend_cooldown(health_settings: Settings) -> None:
    clock = FakeClock()
    h = UpstreamHealth(health_settings, clock=clock)
    for _ in range(3):
        h.record_failure("GRCh38")
    opened = h._host("GRCh38").opened_at
    clock.advance(10)
    h.record_failure("GRCh38")  # fail-fast path: must be a no-op
    assert h._host("GRCh38").opened_at == opened


def test_half_open_after_cooldown_then_close_on_success(health_settings: Settings) -> None:
    clock = FakeClock()
    h = UpstreamHealth(health_settings, clock=clock)
    for _ in range(3):
        h.record_failure("GRCh38")
    assert h.allow("GRCh38") is False
    clock.advance(31)  # past the 30s cooldown
    assert h.allow("GRCh38") is True  # flips to half_open, allows one probe
    assert h.status_for("GRCh38") == "recovering"
    h.record_success("GRCh38")
    assert h.status_for("GRCh38") == "ok"


def test_half_open_failure_reopens(health_settings: Settings) -> None:
    clock = FakeClock()
    h = UpstreamHealth(health_settings, clock=clock)
    for _ in range(3):
        h.record_failure("GRCh38")
    clock.advance(31)
    assert h.allow("GRCh38") is True  # half_open
    h.record_failure("GRCh38")  # re-open
    assert h.status_for("GRCh38") == "down"
    assert h.allow("GRCh38") is False


def test_record_success_resets(health_settings: Settings) -> None:
    h = UpstreamHealth(health_settings, clock=FakeClock())
    h.record_failure("GRCh38")
    h.record_failure("GRCh38")
    h.record_success("GRCh38")
    assert h._host("GRCh38").consecutive_failures == 0


@respx.mock
async def test_probe_success(health_settings: Settings) -> None:
    respx.get(GRCH38_PING).mock(return_value=httpx.Response(200, json={"ping": 1}))
    h = UpstreamHealth(health_settings, clock=FakeClock())
    try:
        ok = await h.probe(GenomeBuild.GRCH38)
    finally:
        await h.aclose()
    assert ok is True
    view = h.snapshot()["GRCh38"]
    assert view["reachable"] is True
    assert view["status"] == "ok"
    assert view["checked_at"] is not None


@respx.mock
async def test_probe_failure_records_breaker(health_settings: Settings) -> None:
    respx.get(GRCH38_PING).mock(return_value=httpx.Response(500, json={"error": "down"}))
    h = UpstreamHealth(health_settings, clock=FakeClock())
    try:
        ok = await h.probe(GenomeBuild.GRCH38)
    finally:
        await h.aclose()
    assert ok is False
    assert h.snapshot()["GRCh38"]["reachable"] is False
    assert h._host("GRCh38").consecutive_failures == 1
    # Early warning: one failed probe -> degraded, even though the breaker
    # (threshold 3) is still closed and allow() still returns True.
    assert h.status_for("GRCh38") == "degraded"
    assert h._host("GRCh38").state == "closed"
    assert h.allow("GRCh38") is True
    assert "advice" in h.meta_hint()


@respx.mock
async def test_refresh_both_hosts_and_meta_hint(health_settings: Settings) -> None:
    respx.get(GRCH38_PING).mock(return_value=httpx.Response(500, json={"error": "down"}))
    respx.get(GRCH37_PING).mock(return_value=httpx.Response(200, json={"ping": 1}))
    # Threshold 1 so a single failed probe trips GRCh38 open.
    settings = Settings(CIRCUIT_FAILURE_THRESHOLD=1)
    h = UpstreamHealth(settings, clock=FakeClock())
    try:
        await h.refresh()
    finally:
        await h.aclose()
    statuses = {k: v["status"] for k, v in h.snapshot().items()}
    assert statuses == {"GRCh38": "down", "GRCh37": "ok"}
    hint = h.meta_hint()
    assert hint["GRCh38"] == "down"
    assert hint["GRCh37"] == "ok"
    assert "advice" in hint and "GRCh37" in hint["advice"]


def test_meta_hint_all_ok_has_no_advice(health_settings: Settings) -> None:
    h = UpstreamHealth(health_settings, clock=FakeClock())
    hint = h.meta_hint()
    assert hint["GRCh38"] == "ok"
    assert "advice" not in hint
