"""Structured MCP error envelopes for vep-link.

The envelope shape is what an LLM client branches on, so codes are deterministic
per exception class: a prompt can recover (retry, switch tool, reformulate input)
without scraping free text. Every top-level tool body runs inside
:func:`run_mcp_tool`, which converts any raised exception into the canonical
envelope built by :func:`mcp_tool_error`.

Mapping is centralized here (mirroring the ``vep_link.exceptions`` docstrings):

* Known :class:`~vep_link.exceptions.VepLinkError` subclasses map to a stable
  ``error.code`` + a recovery hint. Subclass specificity matters --
  :class:`~vep_link.exceptions.UnsupportedContigError` subclasses
  :class:`~vep_link.exceptions.VariantParseError`, so it is checked FIRST and
  classifies as ``unsupported_input`` rather than ``invalid_input``.
* Anything else (an unmapped ``VepLinkError`` or a stray ``RuntimeError``) becomes
  a sanitized ``internal_error``: the original text is never surfaced to the
  client. Instead a fresh ``correlation_id`` is generated, embedded in the
  client-facing message, and logged alongside the real exception so an operator
  can join the two from logs.

``install_validation_error_handler`` adapts the spliceailookup pattern to the
FastMCP build in use: it wraps each registered tool's ``run`` so a FastMCP /
pydantic argument-validation failure returns a structured ``invalid_input``
envelope instead of an opaque framework error. The FastMCP internals are probed
defensively (and fastmcp is imported lazily) so the function is import-safe and a
best-effort no-op if the API differs.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from vep_link.api.health import UpstreamHealth

from vep_link.exceptions import (
    AmbiguousMappingError,
    DataNotFoundError,
    EnsemblApiError,
    RateLimitedError,
    UnsupportedContigError,
    UpstreamInputError,
    UpstreamTimeoutError,
    VariantParseError,
    VepLinkError,
)
from vep_link.mcp.resources import build_meta
from vep_link.observability.metrics import METRICS

logger = structlog.get_logger("vep_link.mcp.errors")

# The ten deterministic error codes, in spec (§7) order. Surfaced verbatim in the
# capabilities document so a client can branch on them ahead of time.
ERROR_CODES: tuple[str, ...] = (
    "invalid_input",
    "unsupported_input",
    "not_found",
    "build_mismatch",
    "ambiguous",
    "rate_limited",
    "upstream_unavailable",
    "upstream_timeout",
    "output_validation_failed",
    "internal_error",
)

# Always-on safe entry point a confused client can fall back to.
_FALLBACK_TOOL = "get_capabilities"

# Error codes that represent a transient upstream fault: the client should back
# off and retry (or switch assembly), not reformulate its input. These also feed
# the circuit breaker's failure count.
_RETRYABLE_CODES: frozenset[str] = frozenset(
    {"rate_limited", "upstream_unavailable", "upstream_timeout"}
)
# Default backoff hint surfaced as ``error.retry_after_s`` for retryable faults.
_DEFAULT_RETRY_AFTER_S = 10

# Recovery text for the internal_error fallthrough; every mapped exception resolves
# its own recovery from ``_EXCEPTION_MAP`` below.
_INTERNAL_ERROR_RECOVERY = (
    "Unexpected server error; retry later. Reference the correlation id if reporting."
)

# Ordered MOST-SPECIFIC FIRST. UnsupportedContigError subclasses VariantParseError,
# so it must precede VariantParseError or it would be mis-classified as invalid_input.
_EXCEPTION_MAP: tuple[tuple[type[VepLinkError], str, str], ...] = (
    (
        UnsupportedContigError,
        "unsupported_input",
        "This input/contig is not supported for the requested operation.",
    ),
    (
        VariantParseError,
        "invalid_input",
        "Check the variant format: coordinate (CHR-POS-REF-ALT), rsID, HGVS, or SPDI.",
    ),
    (
        UpstreamInputError,
        "invalid_input",
        # UpstreamInputError covers both Ensembl 4xx rejections AND local
        # validation (same-assembly liftover, bad vep_options, oversized batch),
        # so the recovery must stay source-neutral -- never claim Ensembl rejected
        # a call that local validation refused before any request was made.
        "Request rejected: verify the input format and the assembly argument.",
    ),
    (
        DataNotFoundError,
        "not_found",
        "No data found; try resolve_variant first to validate the input.",
    ),
    (
        AmbiguousMappingError,
        "ambiguous",
        "The liftover is ambiguous (multiple mappings); inspect the region manually.",
    ),
    (
        RateLimitedError,
        "rate_limited",
        "Upstream rate limit hit; retry with exponential backoff or fewer parallel calls.",
    ),
    (
        UpstreamTimeoutError,
        "upstream_timeout",
        "Upstream timed out; retry shortly.",
    ),
    (
        EnsemblApiError,
        "upstream_unavailable",
        "Ensembl REST is temporarily unavailable; retry shortly.",
    ),
)


@dataclass
class McpErrorContext:
    """Per-call context so error envelopes can be stamped and made actionable.

    ``next_commands`` are ready-to-call follow-ups (e.g. a ``resolve_variant``
    suggestion); ``assembly`` is echoed into ``_meta`` when present.
    """

    tool_name: str
    variant: str | None = None
    assembly: str | None = None
    next_commands: list | None = None
    # The shared upstream-health monitor. When set, ``run_mcp_tool`` records the
    # call outcome against ``assembly`` and injects ``_meta.upstream`` into every
    # success and error envelope.
    health: UpstreamHealth | None = None


def _classify(exc: BaseException) -> tuple[str, str] | None:
    """Return ``(code, recovery)`` for a known exception, else ``None``.

    Walks ``_EXCEPTION_MAP`` most-specific-first; the first ``isinstance`` match
    wins, so subclass relationships (UnsupportedContigError < VariantParseError)
    are honored.
    """
    for exc_type, code, recovery in _EXCEPTION_MAP:
        if isinstance(exc, exc_type):
            return code, recovery
    return None


def mcp_tool_error(
    *,
    code: str,
    message: str,
    recovery: str,
    ctx: McpErrorContext,
    request_id: str | None = None,
    retry_after_s: int | None = None,
) -> dict[str, Any]:
    """Build the canonical structured error envelope.

    Shape (spec §7): an ``error`` block the client branches on plus a ``_meta``
    block built by :func:`~vep_link.mcp.resources.build_meta` (which already
    stamps ``capabilities_version`` and ``unsafe_for_clinical_use``). ``request_id``
    defaults to a fresh 12-hex-char id when not supplied by the caller.

    Retryable upstream faults additionally carry ``retryable: true`` and a
    ``retry_after_s`` backoff hint so an LLM can pace a retry instead of giving up.
    """
    error_block: dict[str, Any] = {
        "code": code,
        "message": message,
        "recovery": recovery,
        "fallback_tool": _FALLBACK_TOOL,
        "next_commands": ctx.next_commands or [],
        "retryable": code in _RETRYABLE_CODES,
    }
    if code in _RETRYABLE_CODES:
        error_block["retry_after_s"] = (
            retry_after_s if retry_after_s is not None else _DEFAULT_RETRY_AFTER_S
        )
    envelope = {
        "error": error_block,
        "_meta": build_meta(
            tool=ctx.tool_name,
            request_id=request_id or uuid.uuid4().hex[:12],
            assembly=ctx.assembly,
            extra={"unsafe_for_clinical_use": True},
        ),
    }
    _inject_upstream(envelope, ctx)
    return envelope


def _inject_upstream(envelope: dict[str, Any], ctx: McpErrorContext) -> None:
    """Attach the compact ``_meta.upstream`` health hint, if a monitor is present."""
    if ctx.health is None:
        return
    meta = envelope.get("_meta")
    if isinstance(meta, dict):
        meta.setdefault("upstream", ctx.health.meta_hint())


def _stamp_elapsed(envelope: dict[str, Any], start: float) -> int:
    """Overwrite ``_meta.timing.elapsed_ms`` with the measured wall-clock cost.

    ``build_meta`` seeds ``timing.elapsed_ms`` to ``0`` at construction time
    (before the body has run); this stamps the real elapsed milliseconds once the
    body has completed, on both success and error envelopes. Returns the value.
    """
    elapsed_ms = max(0, int((time.perf_counter() - start) * 1000))
    if isinstance(envelope, dict):
        meta = envelope.get("_meta")
        if isinstance(meta, dict):
            timing = meta.get("timing")
            if isinstance(timing, dict):
                timing["elapsed_ms"] = elapsed_ms
            else:
                meta["timing"] = {"elapsed_ms": elapsed_ms}
    return elapsed_ms


def _internal_error_envelope(exc: BaseException, ctx: McpErrorContext) -> dict[str, Any]:
    """Build a sanitized ``internal_error`` envelope and log the real exception.

    The original exception text is NEVER surfaced to the client. A fresh
    ``correlation_id`` is generated, embedded in the client-facing message, and
    logged with the real exception at error level so an operator can correlate
    the redacted client message with the unredacted server log.
    """
    correlation_id = uuid.uuid4().hex[:12]
    # Log the real exception (type + repr + traceback) under the correlation id so
    # an operator can join the redacted client message to the full server-side
    # detail. ``exc_info`` is rendered by the configured ``format_exc_info``
    # processor (see ``vep_link.logging_config``).
    logger.error(
        "mcp_internal_error",
        tool=ctx.tool_name,
        correlation_id=correlation_id,
        exc_type=type(exc).__name__,
        exc_repr=repr(exc),
        exc_info=exc,
    )
    message = f"Internal error in {ctx.tool_name} (correlation_id={correlation_id}). Retry later."
    return mcp_tool_error(
        code="internal_error",
        message=message,
        recovery=_INTERNAL_ERROR_RECOVERY,
        ctx=ctx,
        request_id=correlation_id,
    )


async def run_mcp_tool(
    tool_name: str,
    body: Callable[[], Awaitable[dict[str, Any]]],
    ctx: McpErrorContext,
) -> dict[str, Any]:
    """Execute a tool ``body``, converting any exception into an error envelope.

    * On success: the body's dict is returned UNCHANGED (it already carries its
      own ``_meta``).
    * On a known :class:`~vep_link.exceptions.VepLinkError` subclass: mapped to
      the matching code + recovery via :func:`_classify` and returned as an
      envelope.
    * On anything else: a sanitized ``internal_error`` envelope with a correlation
      id; the original exception text is not leaked.
    """
    start = time.perf_counter()
    try:
        result = await body()
    except VepLinkError as exc:
        classified = _classify(exc)
        if classified is None:
            return _finalize_error(_internal_error_envelope(exc, ctx), ctx, start)
        code, recovery = classified
        # A real upstream fault: feed the breaker and append the healthy-host
        # advice (e.g. "GRCh37 is healthy -- retry there") to the recovery.
        if code in _RETRYABLE_CODES and ctx.health is not None:
            if ctx.assembly:
                ctx.health.record_failure(ctx.assembly, exc)
            advice = ctx.health.meta_hint().get("advice")
            if advice:
                recovery = f"{recovery} {advice}"
        message = str(exc) or type(exc).__name__
        envelope = mcp_tool_error(code=code, message=message, recovery=recovery, ctx=ctx)
        return _finalize_error(envelope, ctx, start)
    except Exception as exc:  # error-boundary contract: every other fault -> internal_error
        return _finalize_error(_internal_error_envelope(exc, ctx), ctx, start)

    # Success: record a healthy outcome, stamp the live upstream hint + timing,
    # and emit the call metric.
    if ctx.health is not None:
        if ctx.assembly:
            ctx.health.record_success(ctx.assembly)
        if isinstance(result, dict):
            _inject_upstream(result, ctx)
    elapsed_ms = _stamp_elapsed(result, start) if isinstance(result, dict) else 0
    METRICS.record_tool_call(ctx.tool_name, outcome="success", code=None, elapsed_ms=elapsed_ms)
    return result


def _finalize_error(envelope: dict[str, Any], ctx: McpErrorContext, start: float) -> dict[str, Any]:
    """Stamp elapsed timing on an error envelope and record the error metric."""
    elapsed_ms = _stamp_elapsed(envelope, start)
    code = "internal_error"
    error_block = envelope.get("error")
    if isinstance(error_block, dict):
        code = str(error_block.get("code", code))
    METRICS.record_tool_call(ctx.tool_name, outcome="error", code=code, elapsed_ms=elapsed_ms)
    return envelope


def install_validation_error_handler(mcp: Any) -> None:
    """Make FastMCP argument-validation failures return an ``invalid_input`` envelope.

    Adapts the spliceailookup pattern to the local FastMCP build: each registered
    tool's ``run`` is wrapped so a pydantic ``ValidationError`` (raised when a
    client passes arguments that violate the tool schema) is converted into the
    structured ``invalid_input`` envelope rather than surfacing as an opaque
    framework error.

    The FastMCP internals are probed defensively and ``fastmcp`` / ``pydantic`` are
    imported lazily, so this is import-safe and a best-effort no-op when the API
    differs (e.g. passed a non-FastMCP stub). It is idempotent: already-wrapped
    tools are skipped.
    """
    try:  # lazy + defensive: never fail at import or on a foreign object.
        from pydantic import ValidationError as PydanticValidationError
    except Exception:  # best-effort; pydantic is always present in prod.
        return

    components: dict[Any, Any] = {}
    local_provider = getattr(mcp, "_local_provider", None)
    provider_components = getattr(local_provider, "_components", None)
    if isinstance(provider_components, dict):
        components.update(provider_components)
    # Older FastMCP builds expose tools under a _tool_manager._tools dict.
    tool_manager = getattr(mcp, "_tool_manager", None)
    legacy_tools = getattr(tool_manager, "_tools", None)
    if isinstance(legacy_tools, dict):
        components.update(legacy_tools)

    for tool in components.values():
        if not hasattr(tool, "run") or getattr(tool, "_vep_validation_wrapped", False):
            continue
        original_run = tool.run
        tool_label = str(getattr(tool, "name", "unknown"))

        async def wrapped_run(
            arguments: dict[str, Any],
            *,
            _original_run: Callable[[dict[str, Any]], Awaitable[Any]] = original_run,
            _tool_name: str = tool_label,
        ) -> Any:
            try:
                return await _original_run(arguments)
            except PydanticValidationError as exc:
                ctx = McpErrorContext(tool_name=_tool_name)
                return mcp_tool_error(
                    code="invalid_input",
                    message=f"Invalid arguments for {_tool_name}: {exc.error_count()} error(s).",
                    recovery=(
                        "Fix the tool arguments to match the schema; "
                        "call get_capabilities for accepted parameters."
                    ),
                    ctx=ctx,
                )

        try:
            object.__setattr__(tool, "run", wrapped_run)
            object.__setattr__(tool, "_vep_validation_wrapped", True)
        except Exception as exc:  # frozen/immutable tool: skip it, don't fail install.
            logger.debug("validation_handler_skip", tool=tool_label, exc_type=type(exc).__name__)
            continue
