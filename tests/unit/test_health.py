"""Tests for the upstream-health circuit-breaker monitor."""

from __future__ import annotations

import httpx
import pytest
import respx

from vep_link.api.health import UpstreamHealth
from vep_link.config import Settings
from vep_link.exceptions import EnsemblApiError
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
        h.record_failure("GRCh38", error=EnsemblApiError("boom"))
    assert h.status_for("GRCh38") == "down"
    assert h.allow("GRCh38") is False
    # last_error stores the exception CLASS name only (never str(error)), so a
    # reflected upstream body can never leak through the health snapshot.
    assert h.snapshot()["GRCh38"]["last_error"] == "EnsemblApiError"
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


def test_fallback_advice_is_endpoint_honest_not_a_guaranteed_fix(
    health_settings: Settings,
) -> None:
    # vep tracks health PER HOST via /info/ping, not per endpoint. Advising the
    # model to "retry the other build" as a guaranteed fix misdirects when the
    # failing endpoint (e.g. variant_recoder) is dead on BOTH builds. The advice
    # must name the ping caveat and NOT promise the other host will succeed.
    clock = FakeClock()
    h = UpstreamHealth(health_settings, clock=clock)
    for _ in range(health_settings.CIRCUIT_FAILURE_THRESHOLD):
        h.record_failure("GRCh38", error=EnsemblApiError("boom"))
    advice = h.meta_hint()["advice"]
    assert "GRCh37" in advice
    assert "/info/ping" in advice  # names the (host-level) health signal
    # Must NOT claim the other host is simply "healthy" / a sure retry target.
    assert "currently healthy" not in advice
    assert "endpoint-wide" in advice


def test_host_view_reports_accepting_true_when_open_but_cooled_down(
    health_settings: Settings,
) -> None:
    # The audit's "breaker open-but-passing": an open breaker whose cooldown has
    # elapsed still lets the next call through. The snapshot must say so, so
    # circuit=open is never silently contradicted by a call that succeeds.
    clock = FakeClock()
    h = UpstreamHealth(health_settings, clock=clock)
    for _ in range(3):
        h.record_failure("GRCh38")
    view_open = h.snapshot()["GRCh38"]
    assert view_open["circuit"] == "open"
    assert view_open["accepting"] is False  # inside cooldown: really blocking
    clock.advance(31)  # past the 30s cooldown
    # Reading the view MUST NOT mutate breaker state (no side effect in a getter).
    view_cooled = h.snapshot()["GRCh38"]
    assert view_cooled["circuit"] == "open"
    assert view_cooled["accepting"] is True  # honest: allow() would now pass
    assert h._host("GRCh38").state == "open"  # snapshot did not flip it
