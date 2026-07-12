"""Outbound URL guard for the shared Ensembl HTTP client (Recipe B, F-17).

Keeps httpx's ``follow_redirects=True`` machinery but validates every outgoing
hop -- the initial request AND each auto-followed redirect -- through a request
event-hook. A hop is refused (deterministically, NON-RETRYABLE) when it uses a
non-https scheme (downgrade), carries userinfo (``user:pass@host``), or targets
a host outside the EXACT allowlist. Validating each hop is functionally safer
and more minimal than disabling redirects and re-implementing httpx's
301/302/303->GET-drop-body vs 307/308 method-switch semantics by hand (a silent
correctness landmine for the POST endpoints).

The allowlist is **derived from the operator-overridable Ensembl base URLs**
(``VEP_GRCH38_URL`` / ``VEP_GRCH37_URL``) at client-build time -- never a
hardcoded literal -- so an override moves the allowlist with it.

Guard failures raise :class:`~vep_link.exceptions.DisallowedURLError`, a
``VepLinkError`` (never an ``httpx`` fault): the base-client retry loop retries
only ``httpx`` faults, so a policy refusal is never retried. Messages are FIXED
and reflect no upstream-controlled value (host / redirect target), matching the
backend's no-reflection invariant.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from vep_link.exceptions import DisallowedURLError

OUTBOUND_POLICY_ERROR = "outbound request rejected by policy"


@dataclass(frozen=True, slots=True)
class AllowedOrigin:
    """A normalized configured HTTPS origin."""

    host: str
    port: int


def build_host_allowlist(*base_urls: str) -> frozenset[str]:
    """Derive an exact, lowercase host allowlist from configured base URL(s).

    Hosts with no parseable hostname are skipped. Matching is exact -- no suffix
    or substring match -- so ``evil-rest.ensembl.org.attacker.tld`` never passes.
    """
    hosts: set[str] = set()
    for url in base_urls:
        host = urlsplit(url).hostname
        if host:
            hosts.add(host.lower())
    return frozenset(hosts)


def build_allowed_origins(*base_urls: str) -> frozenset[AllowedOrigin]:
    """Derive exact normalized origins from configured base URL(s)."""
    origins: set[AllowedOrigin] = set()
    for url in base_urls:
        parsed = urlsplit(url)
        host = parsed.hostname
        if host:
            origins.add(AllowedOrigin(host.lower(), parsed.port or 443))
    return frozenset(origins)


def make_url_guard(
    allowed_origins: frozenset[AllowedOrigin] | frozenset[str],
) -> Callable[[httpx.Request], Awaitable[None]]:
    """Return an async httpx *request* event-hook enforcing the per-hop policy.

    Fires on every outgoing request, including auto-followed redirect hops.
    Rejects (fixed message, no upstream value reflected) a non-https scheme, any
    userinfo, or a host outside ``allowed_hosts`` (exact match only).
    """

    normalized = frozenset(
        AllowedOrigin(origin, 443) if isinstance(origin, str) else origin
        for origin in allowed_origins
    )

    async def _guard(request: httpx.Request) -> None:
        url = request.url
        if url.scheme != "https":
            raise DisallowedURLError(OUTBOUND_POLICY_ERROR)
        if url.userinfo:
            raise DisallowedURLError(OUTBOUND_POLICY_ERROR)
        if AllowedOrigin((url.host or "").lower(), url.port or 443) not in normalized:
            raise DisallowedURLError(OUTBOUND_POLICY_ERROR)

    return _guard
