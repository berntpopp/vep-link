"""Adversarial tests for the outbound URL guard + decoded-response byte cap (F-17).

Recipe B (2026-07-12 post-remediation hardening): the shared HTTP client keeps
httpx's ``follow_redirects=True`` machinery but validates every hop with a
request event-hook (scheme==https, host in the EXACT allowlist DERIVED from
``VEP_GRCH38_URL``+``VEP_GRCH37_URL``, no userinfo) and caps the DECODED
response body. Both guard failures are NON-RETRYABLE ``VepLinkError`` subclasses
(never httpx faults), so the base-client retry loop -- which only retries httpx
faults -- never retries them, and the byte cap fails closed (raises, never
truncates).

Everything is mocked with ``respx``; the event hook fires *before* the mock
transport, so a rejected redirect raises without needing a route for the
forbidden host.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from vep_link.api.base_client import BaseHTTPClient
from vep_link.api.url_guard import build_host_allowlist, make_url_guard
from vep_link.config import Settings
from vep_link.exceptions import DisallowedURLError, ResponseTooLargeError

GRCH38 = "https://rest.ensembl.org"
GRCH37 = "https://grch37.rest.ensembl.org"
BOTH_HOSTS = ("rest.ensembl.org", "grch37.rest.ensembl.org")


async def _noop_sleep(_seconds: float) -> None:
    return None


def _client(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> BaseHTTPClient:
    c = BaseHTTPClient(settings)
    monkeypatch.setattr(c, "_sleep", _noop_sleep)
    return c


# -- allowlist derivation (never hardcoded) --------------------------------


def test_allowlist_derived_from_both_configured_base_urls(settings: Settings) -> None:
    hosts = build_host_allowlist(settings.VEP_GRCH38_URL, settings.VEP_GRCH37_URL)
    assert hosts == frozenset(BOTH_HOSTS)


def test_allowlist_follows_env_override_not_a_hardcoded_literal() -> None:
    overridden = Settings(
        VEP_GRCH38_URL="https://mirror.example.org",
        VEP_GRCH37_URL="https://grch37.mirror.example.org",
    )
    hosts = build_host_allowlist(overridden.VEP_GRCH38_URL, overridden.VEP_GRCH37_URL)
    assert hosts == frozenset({"mirror.example.org", "grch37.mirror.example.org"})
    assert "rest.ensembl.org" not in hosts


# -- guard hook, direct (scheme / userinfo / host) -------------------------


@pytest.mark.parametrize("host", BOTH_HOSTS)
async def test_guard_allows_both_allowlisted_hosts(host: str) -> None:
    guard = make_url_guard(frozenset(BOTH_HOSTS))
    await guard(httpx.Request("GET", f"https://{host}/vep/human/id/rs6"))


async def test_guard_rejects_non_https_scheme() -> None:
    guard = make_url_guard(frozenset(BOTH_HOSTS))
    with pytest.raises(DisallowedURLError):
        await guard(httpx.Request("GET", "http://rest.ensembl.org/x"))


async def test_guard_rejects_userinfo() -> None:
    guard = make_url_guard(frozenset(BOTH_HOSTS))
    with pytest.raises(DisallowedURLError):
        await guard(httpx.Request("GET", "https://user:pass@rest.ensembl.org/x"))


async def test_guard_rejects_empty_colon_at_userinfo() -> None:
    # The empty ``:@`` form has username==password=="" but httpx exposes it as a
    # non-empty ``userinfo`` (``b':'``); a username-or-password check would miss
    # it. The guard rejects ANY non-empty userinfo, while a clean URL passes.
    guard = make_url_guard(frozenset(BOTH_HOSTS))
    with pytest.raises(DisallowedURLError):
        await guard(httpx.Request("GET", "https://:@rest.ensembl.org/x"))
    await guard(httpx.Request("GET", "https://rest.ensembl.org/x"))


async def test_guard_rejects_non_allowlisted_host() -> None:
    guard = make_url_guard(frozenset(BOTH_HOSTS))
    with pytest.raises(DisallowedURLError):
        await guard(httpx.Request("GET", "https://evil.example/x"))


# -- through the client (redirect hops) ------------------------------------


@respx.mock
@pytest.mark.parametrize("host", BOTH_HOSTS)
async def test_cross_host_redirect_raises_and_is_not_retried(
    host: str, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = f"https://{host}/vep/human/id/rs1"
    route = respx.get(src).mock(
        return_value=httpx.Response(302, headers={"Location": "https://evil.example/x"})
    )
    client = _client(settings, monkeypatch)
    try:
        with pytest.raises(DisallowedURLError):
            await client.get_json(src)
    finally:
        await client.aclose()
    # NON-RETRYABLE: the retry loop must not re-issue the redirecting request.
    assert route.call_count == 1


@respx.mock
@pytest.mark.parametrize("host", BOTH_HOSTS)
async def test_https_downgrade_redirect_raises(
    host: str, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = f"https://{host}/vep/human/id/rs2"
    respx.get(src).mock(return_value=httpx.Response(302, headers={"Location": f"http://{host}/x"}))
    client = _client(settings, monkeypatch)
    try:
        with pytest.raises(DisallowedURLError):
            await client.get_json(src)
    finally:
        await client.aclose()


@respx.mock
@pytest.mark.parametrize("host", BOTH_HOSTS)
async def test_userinfo_redirect_raises(
    host: str, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = f"https://{host}/vep/human/id/rs3"
    respx.get(src).mock(
        return_value=httpx.Response(302, headers={"Location": f"https://u:p@{host}/x"})
    )
    client = _client(settings, monkeypatch)
    try:
        with pytest.raises(DisallowedURLError):
            await client.get_json(src)
    finally:
        await client.aclose()


# -- decoded-byte cap (fail closed, non-retryable) -------------------------


@respx.mock
@pytest.mark.parametrize("host", BOTH_HOSTS)
async def test_over_cap_decoded_response_raises_and_is_not_retried(
    host: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        MAX_RETRIES=2,
        BACKOFF_BASE_SECONDS=0.0,
        BACKOFF_MAX_SECONDS=0.0,
        MAX_RESPONSE_BYTES=64,
    )
    url = f"https://{host}/vep/homo_sapiens/region"
    big = b'{"data":"' + b"x" * 4096 + b'"}'
    route = respx.post(url).mock(return_value=httpx.Response(200, content=big))
    client = _client(settings, monkeypatch)
    try:
        with pytest.raises(ResponseTooLargeError):
            await client.post_json(url, {"variants": ["1 1 . A C . . ."]})
    finally:
        await client.aclose()
    # Deterministic: a too-large response is not retried.
    assert route.call_count == 1


@respx.mock
async def test_over_cap_response_uses_fixed_policy_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(MAX_RETRIES=0, MAX_RESPONSE_BYTES=5)
    url = f"{GRCH38}/vep/human/id/rs-fixed-error"
    respx.get(url).mock(return_value=httpx.Response(200, content=b"abcdef"))
    client = _client(settings, monkeypatch)
    try:
        with pytest.raises(ResponseTooLargeError) as captured:
            await client.get_json(url)
    finally:
        await client.aclose()
    assert str(captured.value) == "outbound request rejected by policy"


@respx.mock
@pytest.mark.parametrize("host", BOTH_HOSTS)
async def test_response_under_cap_parses_normally(
    host: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(MAX_RETRIES=2, MAX_RESPONSE_BYTES=50 * 1024 * 1024)
    url = f"https://{host}/vep/human/id/rs4"
    respx.get(url).mock(return_value=httpx.Response(200, json={"ok": True, "host": host}))
    client = _client(settings, monkeypatch)
    try:
        result = await client.get_json(url)
    finally:
        await client.aclose()
    assert result == {"ok": True, "host": host}


# -- retry / chunk behavior unchanged with the guard active ----------------


@respx.mock
async def test_429_retry_after_still_honored_with_guard_active(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = f"{GRCH38}/vep/human/id/rs5"
    route = respx.get(url).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    client = _client(settings, monkeypatch)
    try:
        result = await client.get_json(url)
    finally:
        await client.aclose()
    assert result == {"ok": True}
    assert route.call_count == 2  # 429 retried on the same allowlisted host


async def test_disallowed_url_error_is_not_an_httpx_exception() -> None:
    # If the guard exception subclassed an httpx transport/timeout type, the
    # base-client retry loop would swallow and retry it. It must not.
    assert not issubclass(DisallowedURLError, httpx.HTTPError)
    assert not issubclass(ResponseTooLargeError, httpx.HTTPError)
