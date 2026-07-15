"""FastMCP argument-validation handler for vep-link (Response-Envelope v1).

``install_validation_error_handler`` wraps each registered tool's ``run`` so a
pydantic / FastMCP argument-validation failure (a client passing arguments that
violate the tool schema) is converted into the same flat ``invalid_input``
envelope :func:`vep_link.mcp.errors.run_mcp_tool` builds -- wrapped in an
``is_error=True`` :class:`~fastmcp.tools.ToolResult` -- rather than surfacing as
an opaque framework error. The envelope NAMES the offending parameter(s) (in the
message and as a structured ``field``) and lists the tool's ``allowed_values`` so
a model can self-correct (the behaviour gate's "names the offending or the valid
parameters" check).

Lives in its own module purely for the 600-LOC budget; it is a cohesive unit
(``errors.py`` sits near the ceiling). The FastMCP internals are probed
defensively and ``pydantic`` is imported lazily, so this is import-safe and a
best-effort no-op when the API differs.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from fastmcp.exceptions import ValidationError as FastMCPValidationError
from fastmcp.tools import ToolResult

from vep_link.mcp.errors import McpErrorContext, mcp_tool_error

logger = structlog.get_logger("vep_link.mcp.validation_errors")


def _offending_fields(exc: BaseException) -> list[str]:
    """Names of the offending argument(s), walking to the pydantic error.

    For a missing required argument the loc is the required field name; for an
    unknown/extra argument (``extra='forbid'``) it is the rejected key. Either
    way the model gets a concrete parameter to act on. FastMCP re-raises the
    pydantic error as its own ``ValidationError`` with the original in
    ``__cause__``, so the chain is walked.
    """
    seen: set[int] = set()
    err: BaseException | None = exc
    while err is not None and id(err) not in seen:
        seen.add(id(err))
        errors_fn = getattr(err, "errors", None)
        if callable(errors_fn):
            try:
                raw = errors_fn()
            except Exception:
                raw = None
            names: list[str] = []
            for item in raw or []:
                loc = item.get("loc") if isinstance(item, dict) else None
                if loc:
                    names.append(str(loc[-1]))
            if names:
                return list(dict.fromkeys(names))  # dedupe, preserve order
        err = getattr(err, "__cause__", None)
    return []


def _declared_params(tool: Any) -> list[str]:
    """Declared input-parameter names for ``tool`` (the valid arguments)."""
    schema = getattr(tool, "parameters", None)
    if isinstance(schema, dict):
        props = schema.get("properties")
        if isinstance(props, dict):
            return sorted(props.keys())
    return []


def install_validation_error_handler(mcp: Any) -> None:
    """Make FastMCP argument-validation failures return a flat ``invalid_input`` frame.

    Each registered tool's ``run`` is wrapped so a pydantic / FastMCP
    ``ValidationError`` becomes the flat Response-Envelope Standard v1
    ``invalid_input`` frame (wrapped ``is_error=True``) that names the offending
    parameter(s). Idempotent: already-wrapped tools are skipped.
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
        tool_params = _declared_params(tool)

        async def wrapped_run(
            arguments: dict[str, Any],
            *,
            _original_run: Callable[[dict[str, Any]], Awaitable[Any]] = original_run,
            _tool_name: str = tool_label,
            _allowed: list[str] = tool_params,
        ) -> Any:
            try:
                return await _original_run(arguments)
            except (PydanticValidationError, FastMCPValidationError) as exc:
                ctx = McpErrorContext(tool_name=_tool_name)
                fields = _offending_fields(exc)
                # Name the offending parameter(s) IN the message AND carry them as a
                # structured `field` (plus the valid `allowed_values`) so a model can
                # self-correct instead of guessing (Response-Envelope §2; the
                # behaviour gate's "names the offending or the valid parameters").
                if fields:
                    named = ", ".join(fields)
                    message = f"Invalid arguments for {_tool_name}: check parameter(s): {named}."
                else:
                    error_count = (
                        exc.error_count() if isinstance(exc, PydanticValidationError) else 1
                    )
                    message = f"Invalid arguments for {_tool_name}: {error_count} error(s)."
                recovery = "Fix the tool arguments to match the schema"
                if _allowed:
                    recovery += f"; accepted parameters: {', '.join(_allowed)}"
                recovery += "; call get_capabilities for the full contract."
                envelope = mcp_tool_error(
                    code="invalid_input",
                    message=message,
                    recovery=recovery,
                    ctx=ctx,
                )
                if fields:
                    envelope["field"] = fields
                if _allowed:
                    envelope["allowed_values"] = _allowed
                return ToolResult(structured_content=envelope, is_error=True)

        try:
            object.__setattr__(tool, "run", wrapped_run)
            object.__setattr__(tool, "_vep_validation_wrapped", True)
        except Exception as exc:  # frozen/immutable tool: skip it, don't fail install.
            logger.debug("validation_handler_skip", tool=tool_label, exc_type=type(exc).__name__)
            continue
