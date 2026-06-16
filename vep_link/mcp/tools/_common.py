"""Shared helpers for the vep-link MCP tool layer.

Small, dependency-light utilities the six tool modules reuse so each tool file
stays focused on its own argument schema and service call:

* :func:`new_request_id` -- a short, stable per-call id for ``_meta.request_id``.
* :func:`validate_vep_options` -- enforce the caller-supplied VEP flag allowlist,
  raising :class:`~vep_link.exceptions.UpstreamInputError` (-> ``invalid_input``)
  on any disallowed key.
* :func:`spliceai_dbnsfp_note` -- the instance-dependence note surfaced when a
  caller requests a plugin (SpliceAI / dbNSFP) that the public Ensembl REST does
  not run.
* ``next_command`` helpers -- ready-to-call follow-up steps embedded in
  ``_meta.next_commands`` so a client can chain the canonical workflow.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from vep_link.config import VEP_OPTION_ALLOWLIST
from vep_link.exceptions import EnsemblApiError, UpstreamInputError

if TYPE_CHECKING:
    from vep_link.api.health import UpstreamHealth

# Plugins that ARE allowlisted (so they can be sent to an instance that runs
# them) but are NOT available on the public Ensembl REST API. Requesting one
# yields an explanatory note rather than a silent drop.
_INSTANCE_DEPENDENT_PLUGINS: frozenset[str] = frozenset({"SpliceAI", "dbNSFP", "LoF"})


def new_request_id() -> str:
    """Return a short (12 hex char) request id for ``_meta.request_id``."""
    return uuid.uuid4().hex[:12]


def validate_vep_options(vep_options: dict[str, str] | None) -> None:
    """Reject any caller-supplied VEP flag outside :data:`VEP_OPTION_ALLOWLIST`.

    Raises :class:`~vep_link.exceptions.UpstreamInputError` (which the error
    module maps to ``invalid_input``) listing the disallowed keys so the client
    can correct the request.
    """
    if not vep_options:
        return
    bad = set(vep_options) - VEP_OPTION_ALLOWLIST
    if bad:
        raise UpstreamInputError(f"unsupported vep_options: {sorted(bad)}")


def spliceai_dbnsfp_note(vep_options: dict[str, str] | None) -> str | None:
    """Return an instance-dependence note when a plugin flag is requested, else None.

    SpliceAI / dbNSFP / LoF are valid (allowlisted) but are not run by the public
    Ensembl REST API, so a caller who requests them is told the result depends on
    the configured VEP instance rather than being silently misled.
    """
    if not vep_options:
        return None
    requested = sorted(set(vep_options) & _INSTANCE_DEPENDENT_PLUGINS)
    if not requested:
        return None
    return (
        f"VEP plugin(s) {requested} are instance-dependent and are not run by the "
        "public Ensembl REST API; results for these fields are only populated "
        "against a VEP instance configured with the plugin."
    )


def next_command(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Build one ready-to-call follow-up step for ``_meta.next_commands``."""
    return {"tool": tool, "arguments": arguments}


def ensure_upstream_available(health: UpstreamHealth | None, assembly: str) -> None:
    """Fail fast when the circuit breaker for ``assembly`` is open.

    Raises :class:`~vep_link.exceptions.EnsemblApiError` (-> ``upstream_unavailable``)
    *before* any upstream call when the host is known-degraded, so the consumer
    gets an instant, clearly-advised error instead of waiting out a timeout.
    """
    if health is not None and not health.allow(assembly):
        raise EnsemblApiError(
            f"Ensembl {assembly} REST is currently degraded (circuit open); failing "
            "fast without calling upstream. Retry after the cooldown."
        )
