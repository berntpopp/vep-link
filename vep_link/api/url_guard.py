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
from urllib.parse import urlsplit

import httpx

from vep_link.exceptions import DisallowedURLError


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


def make_url_guard(
    allowed_hosts: frozenset[str],
) -> Callable[[httpx.Request], Awaitable[None]]:
    """Return an async httpx *request* event-hook enforcing the per-hop policy.

    Fires on every outgoing request, including auto-followed redirect hops.
    Rejects (fixed message, no upstream value reflected) a non-https scheme, any
    userinfo, or a host outside ``allowed_hosts`` (exact match only).
    """

    async def _guard(request: httpx.Request) -> None:
        url = request.url
        if url.scheme != "https":
            raise DisallowedURLError("Outbound request blocked: non-https scheme.")
        if url.username or url.password:
            raise DisallowedURLError("Outbound request blocked: userinfo not permitted.")
        if (url.host or "").lower() not in allowed_hosts:
            raise DisallowedURLError("Outbound request blocked: destination host not allowlisted.")

    return _guard
