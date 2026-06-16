"""Server/transport layer for vep-link.

A thin FastAPI host that exposes ``GET /health`` and mounts the FastMCP HTTP app
at ``settings.MCP_PATH``. The host owns the long-lived upstream wiring: its
lifespan builds an :class:`~vep_link.api.ensembl_client.EnsemblClient`, wraps it
in a :class:`~vep_link.services.vep_service.VepService` stored on
``app.state.vep_service``, and closes it on shutdown.

The MCP facade is wired to that single shared service via a lazy
``service_factory`` so the tool layer always sees the host-managed instance
rather than constructing its own. The FastMCP HTTP app carries its own lifespan
(it starts/stops the streamable-HTTP session manager); :func:`_compose_lifespan`
nests that lifespan inside the FastAPI lifespan so both start and stop cleanly --
mirroring the gnomad-link ``UnifiedServerManager`` pattern.

:func:`build_app` is the importable ASGI factory (``server:app`` /
``uvicorn``-friendly); :func:`run_server` is the CLI entry point that configures
logging and hands the app to ``uvicorn.run`` (tests patch ``uvicorn.run``).
"""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import structlog
import uvicorn
from asgi_correlation_id import CorrelationIdMiddleware
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from vep_link import __version__
from vep_link.api.ensembl_client import EnsemblClient
from vep_link.api.health import UpstreamHealth
from vep_link.config import ServerConfig, settings
from vep_link.logging_config import configure_logging
from vep_link.mcp.facade import create_vep_mcp
from vep_link.services.vep_service import VepService

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = structlog.get_logger("vep_link.server_manager")


async def _poll_health(monitor: UpstreamHealth, interval: float) -> None:
    """Background loop: refresh the upstream-health probe every ``interval`` seconds.

    Exceptions are swallowed (and logged) so a transient probe fault never kills
    the poller; the loop ends only on cancellation at shutdown.
    """
    while True:
        try:
            await monitor.refresh()
        except Exception:
            logger.debug("health_poll_failed", exc_info=True)
        await asyncio.sleep(interval)


def _compose_lifespan(app: FastAPI, mcp_app: Any) -> None:
    """Nest the MCP HTTP app's lifespan inside the FastAPI lifespan.

    The mounted FastMCP app has its own lifespan that starts the streamable-HTTP
    session manager; running it inside the FastAPI lifespan guarantees both the
    host service and the MCP session manager start and stop together.
    """
    fastapi_lifespan = app.router.lifespan_context
    mcp_lifespan = mcp_app.lifespan

    @asynccontextmanager
    async def combined(parent_app: FastAPI) -> AsyncIterator[None]:
        async with fastapi_lifespan(parent_app), mcp_lifespan(mcp_app):
            yield

    app.router.lifespan_context = combined


def build_app(config: ServerConfig | None = None) -> FastAPI:
    """Build the FastAPI host with /health and the MCP HTTP app mounted at MCP_PATH.

    ``config`` selects the mount path (defaults to ``settings.MCP_PATH``); the
    upstream client/service are constructed in the lifespan, not at build time,
    so import-time and test-time construction stay side-effect free.
    """
    cfg = config or ServerConfig.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client = EnsemblClient(settings)
        service = VepService(client, settings)
        app.state.vep_service = service
        monitor = UpstreamHealth(settings)
        app.state.upstream_health = monitor
        poller: asyncio.Task[None] | None = None
        if settings.HEALTH_PROBE_ENABLED:
            poller = asyncio.create_task(
                _poll_health(monitor, settings.HEALTH_PROBE_INTERVAL_SECONDS)
            )
        try:
            yield
        finally:
            if poller is not None:
                poller.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await poller
            await monitor.aclose()
            await app.state.vep_service.aclose()

    app = FastAPI(
        title="vep-link MCP Host",
        description="Thin FastAPI host that exposes /health and mounts the MCP HTTP app.",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(CorrelationIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "healthy", "service": "vep-link", "version": __version__}

    mcp = create_vep_mcp(
        service_factory=lambda: app.state.vep_service,
        health_factory=lambda: app.state.upstream_health,
    )
    mcp_app = mcp.http_app(path="/", stateless_http=True, json_response=True)
    _compose_lifespan(app, mcp_app)
    app.mount(cfg.mcp_path, mcp_app)

    return app


def run_server(
    *,
    transport: str = "unified",
    host: str | None = None,
    port: int | None = None,
    mcp_path: str | None = None,
    log_level: str | None = None,
    dev: bool = False,
) -> None:
    """Configure logging and run the unified host under uvicorn.

    Resolves host/port/mcp_path/log_level from the explicit arguments first, then
    ``settings``. In ``dev`` mode logging uses the console renderer; otherwise the
    configured ``LOG_FORMAT`` is used. Tests patch ``uvicorn.run``.
    """
    resolved_level = log_level or settings.LOG_LEVEL
    log_format = "console" if dev else settings.LOG_FORMAT
    configure_logging(resolved_level, log_format)

    config = ServerConfig(
        transport="unified" if transport == "unified" else "http",
        host=host or settings.MCP_HOST,
        port=port or settings.MCP_PORT,
        mcp_path=mcp_path or settings.MCP_PATH,
        log_level=resolved_level,
    )
    app = build_app(config)
    uvicorn.run(app, host=config.host, port=config.port)
