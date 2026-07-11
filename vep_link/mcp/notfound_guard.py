"""FastMCP-core not-found reflection guard (Response-Envelope v1.1 fast-follow).

FastMCP core (pinned ``>=3.4.4,<4.0.0``) reflects the caller's OWN requested tool
name / resource URI / prompt name back to the caller (and to logs) BEFORE any
backend middleware runs. This module closes that residual with fixed, input-free
messages built from CONSTANTS only, mirroring the ratified fleet references
(``mondo``/``hpo`` registry preflight, ``clinvar`` protocol backstop,
``panelapp``/``autopvs1`` validation-log scrub filter).

The reflected text is *caller-supplied* (a caller self-reflection surface), so
this is materially lower-risk than the upstream-injection leak the prior sweep
closed. It is still worth closing: the reflected name/URI — with any
control/zero-width/bidi/NUL code points — lands in shared operator logs and in an
agent's tool-result context. Fixed constants remove the channel entirely.

Layers (spec §3), copied per repo (no shared runtime library exists fleet-wide):

* Layer 1 — ``on_call_tool`` registry preflight: ``get_tool(name)`` returns
  ``None`` for an unknown/disabled tool, so we return a fixed, name-free
  ``not_found`` envelope BEFORE core dispatch. Closes the unknown-TOOL surface;
  never echoes the requested name into ``_meta.tool``.
* Layer 2 — ``on_read_resource`` boundary: an unknown (URL-valid) resource makes
  core raise ``NotFoundError("Unknown resource: '<uri>'")``; we re-raise a fixed
  URI-free ``ResourceError``. vep-link registers no author-classified
  ``ResourceError`` message, so every read failure collapses to the fixed string.
* Layer 3 — protocol-handler backstop: wraps the raw ``CallTool`` / ``ReadResource``
  / ``GetPrompt`` request handlers as the OUTERMOST layer. Replaces any non-envelope
  ``isError`` tool result (the unknown-tool *return* path FastMCP uses on this
  stack) and re-raises fixed input-free messages for resource/prompt dispatch
  failures — the ONLY layer that covers the unknown-PROMPT surface (vep-link
  registers no prompts, so every ``prompts/get`` is unknown).
* Layer 5 — validation-log scrub filter: FastMCP's pre-middleware and the MCP SDK
  session's request-validation logs echo the raw name/URI (with code points) on
  their own loggers/handlers at DEBUG and above. The filter neutralizes those
  records at the source logger (and its handlers) so caller input never reaches a
  log or telemetry sink.

Layer 4 (arg-validation) is the existing ``install_validation_error_handler``
(``errors.py``). Layer 6 (OTel span redaction) is a no-op here: FastMCP pulls in
``opentelemetry-api`` transitively, but ``opentelemetry-sdk`` is absent, so the
tracer provider is non-recording — no span exception attributes are ever
captured, so there is nothing to redact (fleet policy: do NOT add the SDK dep).
"""

from __future__ import annotations

import json
import logging
from typing import Any, cast

import mcp.types
import structlog
from fastmcp.exceptions import NotFoundError as FastMCPNotFoundError
from fastmcp.exceptions import ResourceError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import ToolResult

from vep_link.mcp.errors import McpErrorContext, mcp_tool_error

logger = structlog.get_logger("vep_link.mcp.notfound_guard")

# Fixed, input-free public messages. They NEVER contain the requested name/URI
# (nor a ``_meta.tool`` echo of it): sanitation strips code points but not
# injection prose, so a fixed constant is the only safe source (prior-sweep
# lesson). ``not_found`` reuses this repo's error-code vocabulary (errors.py).
_UNKNOWN_TOOL_MESSAGE = "The requested tool is not available."
_UNKNOWN_TOOL_RECOVERY = "Call get_capabilities to list the available tools."
_UNKNOWN_RESOURCE_MESSAGE = "Resource unavailable or not found."
_UNKNOWN_PROMPT_MESSAGE = "The requested prompt is not available."


def unknown_tool_result() -> ToolResult:
    """Return a fixed, name-free ``not_found`` envelope for an unknown tool.

    Built with an empty ``tool_name`` so ``_meta.tool`` never reflects the
    requested (caller-controlled) name back to the caller. Carries both
    ``structured_content`` (the FLAT v1 frame with ``error_code="not_found"``) and
    the auto-generated TextContent JSON mirror, wrapped ``is_error=True`` so the
    MCP-native ``CallToolResult.isError`` flag is set (Response-Envelope v1 §2).
    """
    envelope = mcp_tool_error(
        code="not_found",
        message=_UNKNOWN_TOOL_MESSAGE,
        recovery=_UNKNOWN_TOOL_RECOVERY,
        ctx=McpErrorContext(tool_name=""),
    )
    return ToolResult(structured_content=envelope, is_error=True)


class NotFoundGuard(Middleware):
    """Layer 1 (tool preflight) + Layer 2 (resource boundary)."""

    async def on_call_tool(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, ToolResult],
    ) -> ToolResult:
        """Preflight the tool NAME; an unknown name never reaches core dispatch.

        ``get_tool`` returns ``None`` (it does not raise) for an unknown or
        disabled tool, so an unknown name is caught here and answered with a
        fixed, name-free envelope. Otherwise defer to the chain (arg-validation
        wrapper + the tool body).
        """
        fctx = getattr(context, "fastmcp_context", None)
        name = getattr(getattr(context, "message", None), "name", None)
        if fctx is not None and isinstance(name, str):
            try:
                tool = await fctx.fastmcp.get_tool(name)
            except Exception:
                tool = object()  # resolution failure: defer to core, do not mask
            if tool is None:
                logger.warning("mcp_unknown_tool")
                return unknown_tool_result()
        return await call_next(context)

    async def on_read_resource(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        """Emit a FIXED, URI-free error for a resource not-found / read failure.

        The requested URI is caller-controlled; FastMCP core echoes it
        (``Unknown resource: '<uri>'`` / ``Error reading resource '<uri>'``) in
        both the direct exception and the protocol error. Re-raise a fixed
        message so the URI never reaches the caller/protocol. vep-link raises no
        author-classified ResourceError, so every failure collapses to the fixed
        string — NEVER ``str(exc)`` (sanitation strips code points but PRESERVES
        injection prose, so a fixed constant is the only safe caller-visible
        source; error-sanitation-sweep rule).
        """
        try:
            return await call_next(context)
        except Exception as exc:
            logger.warning("mcp_resource_error", error_type=type(exc).__name__)
            raise ResourceError(_UNKNOWN_RESOURCE_MESSAGE) from None


# ---------------------------------------------------------------------------
# Layer 3 — protocol-handler backstop (clinvar pattern)
# ---------------------------------------------------------------------------


class ProtocolError(Exception):
    """A dispatch-level failure re-raised with a FIXED, input-free message."""


def _is_structured_envelope(call_result: mcp.types.CallToolResult) -> bool:
    """True if an ``isError`` result carries one of OUR JSON envelopes.

    Distinguishes a structured vep-link error (already input-free — it has an
    ``error_code``) from a RAW FastMCP dispatch error whose plain-text message
    echoes the caller-supplied tool name (``Unknown tool: '<name>'``).
    """
    if not call_result.content:
        return False
    text = getattr(call_result.content[0], "text", None)
    if not isinstance(text, str):
        return False
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        return False
    return isinstance(obj, dict) and "error_code" in obj


def _fixed_tool_not_found_result() -> mcp.types.ServerResult:
    """A fixed, input-free ServerResult for an unknown/failed tool dispatch."""
    result = unknown_tool_result().to_mcp_result()
    return mcp.types.ServerResult(cast(mcp.types.CallToolResult, result))


def install_protocol_error_handler(mcp_server: Any) -> None:
    """Wrap the tool/resource/prompt request handlers as the OUTERMOST layer.

    A FastMCP core not-found (or read) error can no longer reflect the
    caller-supplied name/URI. Install AFTER all tools/resources/prompts are
    registered so the handlers exist.
    """
    handlers = mcp_server._mcp_server.request_handlers

    call_tool = handlers.get(mcp.types.CallToolRequest)
    if call_tool is not None:

        async def wrapped_call_tool(
            request: mcp.types.CallToolRequest,
            *,
            _orig: Any = call_tool,
        ) -> mcp.types.ServerResult:
            try:
                result = cast(mcp.types.ServerResult, await _orig(request))
            except FastMCPNotFoundError:
                # Unknown-tool *raise* drift (should not reach here once Layer 1
                # is active) — answer with the fixed name-free envelope.
                return _fixed_tool_not_found_result()
            # FastMCP *returns* an isError CallToolResult with a raw plain-text
            # message ("Unknown tool: '<name>'") for an unknown tool; replace any
            # isError result that is NOT one of our structured envelopes. A masked
            # runtime ToolError is a FastMCPError (raised, not returned) and does
            # not pass through here, so this only catches the name-echoing return.
            root = getattr(result, "root", None)
            if (
                isinstance(root, mcp.types.CallToolResult)
                and root.isError
                and not _is_structured_envelope(root)
            ):
                return _fixed_tool_not_found_result()
            return result

        handlers[mcp.types.CallToolRequest] = wrapped_call_tool

    for request_type, message in (
        (mcp.types.ReadResourceRequest, _UNKNOWN_RESOURCE_MESSAGE),
        (mcp.types.GetPromptRequest, _UNKNOWN_PROMPT_MESSAGE),
    ):
        orig = handlers.get(request_type)
        if orig is None:
            continue

        async def wrapped(
            request: Any,
            *,
            _orig: Any = orig,
            _message: str = message,
        ) -> Any:
            try:
                return await _orig(request)
            except Exception:
                # Re-raise with a FIXED, input-free message so no requested
                # name/URI (or its code points) reaches the JSON-RPC error frame.
                raise ProtocolError(_message) from None

        handlers[request_type] = wrapped


# ---------------------------------------------------------------------------
# Layer 5 — validation-log scrub filter (panelapp/autopvs1 pattern)
# ---------------------------------------------------------------------------
#
# Each entry is a substring that appears in the ``record.msg`` of a FastMCP-core
# or MCP-SDK log line that reflects the caller-supplied name/URI (either
# interpolated into an f-string ``msg`` or carried in ``record.args``). Matching
# on ``msg`` (the format string) covers both forms, because the scrub clears the
# args as well.
_SCRUB_MARKERS: tuple[str, ...] = (
    "Handler called: call_tool",
    "Handler called: read_resource",
    "Handler called: get_prompt",
    "Invalid arguments for tool",
    "Error calling tool",
    "Error reading resource",
    "Failed to validate request",
    "Message that failed validation",
    "Tool cache miss for",
)

# The source loggers on which those records are CREATED. A logging filter must be
# attached to the originating logger (or its handlers) — logger-level filters are
# skipped during propagation, but HANDLER-level filters DO run during propagation.
# The MCP SDK session logs the request-validation failure via the module-level
# ``logging.warning`` (root). ``fastmcp`` is FastMCP's non-propagating parent
# logger (propagate=False, its own Rich handlers): attaching there — and to its
# handlers (see install_validation_log_filter) — scrubs at the handler level any
# record that propagates up from a child logger to the Rich handlers.
_SCRUB_LOGGERS: tuple[str, ...] = (
    "",  # root — mcp.shared.session request-validation failures
    "fastmcp",  # non-propagating parent + its Rich handlers (handler-level scrub)
    "fastmcp.server.server",
    "fastmcp.server.mixins.mcp_operations",
    "mcp.server.lowlevel.server",
    "mcp.shared.session",
)

_SCRUBBED_MESSAGE = "MCP request rejected (details omitted)."


class _ValidationLogScrubFilter(logging.Filter):
    """Scrub log records that would echo a caller-supplied tool name / URI.

    Replaces the record payload with fixed metadata (clearing ``args`` /
    ``exc_info`` / ``exc_text`` / ``stack_info``) so the caller-chosen name/URI —
    and any control/zero-width/bidi/NUL code points it carries — can never reach
    a log or telemetry sink. Always returns ``True``: the (now input-free) record
    is still emitted for operational visibility.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.msg if isinstance(record.msg, str) else ""
        if any(marker in msg for marker in _SCRUB_MARKERS):
            record.msg = _SCRUBBED_MESSAGE
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True


def install_validation_log_filter() -> None:
    """Idempotently attach the scrub filter to each source logger (and handlers)."""
    for name in _SCRUB_LOGGERS:
        target = logging.getLogger(name)
        if not any(isinstance(f, _ValidationLogScrubFilter) for f in target.filters):
            target.addFilter(_ValidationLogScrubFilter())
        # Also attach to any non-propagating handlers on this logger, so a record
        # that reaches a handler directly is scrubbed even if the logger filter
        # were bypassed (belt and braces; matches the panelapp/autopvs1 reference).
        for handler in target.handlers:
            if not any(isinstance(f, _ValidationLogScrubFilter) for f in handler.filters):
                handler.addFilter(_ValidationLogScrubFilter())
