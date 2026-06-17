"""Tests for the server/transport layer (``vep_link.server_manager`` + entrypoints).

No real network: ``build_app`` constructs an ``EnsemblClient`` whose transport is
lazy (no socket until a request is issued), so running the FastAPI + MCP lifespan
in-process is safe under the conftest no-network guard. The /health route is
exercised via an in-process ASGI transport with the composed lifespan active.
"""

from __future__ import annotations

import importlib
import sys

import httpx
import pytest
from fastapi import FastAPI

from vep_link import __version__
from vep_link.server_manager import build_app, run_server
from vep_link.services.vep_service import VepService


def test_build_app_returns_fastapi() -> None:
    app = build_app()
    assert isinstance(app, FastAPI)
    assert app.title == "vep-link MCP Host"
    # Docs disabled.
    assert app.docs_url is None
    assert app.redoc_url is None
    assert app.openapi_url is None


async def test_lifespan_sets_vep_service() -> None:
    app = build_app()
    async with app.router.lifespan_context(app):
        service = app.state.vep_service
        assert isinstance(service, VepService)


async def test_health_endpoint() -> None:
    app = build_app()
    # Enter the composed (FastAPI + MCP) lifespan, issue the request, and exit it
    # all within this single task so the MCP session manager's task group is
    # entered and exited in the same task.
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "service": "vep-link",
        "version": __version__,
    }


def test_mcp_app_mounted_at_mcp_path() -> None:
    app = build_app()
    paths = [getattr(route, "path", "") for route in app.routes]
    assert any(path.startswith("/mcp") for path in paths), paths


async def test_metrics_endpoint_exposes_prometheus_text() -> None:
    app = build_app()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    # The tool-call metric family header is always present...
    assert "# TYPE vep_link_tool_calls_total counter" in body
    # ...and the live per-assembly circuit-state gauge is rendered from health.
    assert "# TYPE vep_link_circuit_state gauge" in body
    assert 'vep_link_circuit_state{assembly="GRCh38",state="closed"}' in body


def test_run_server_invokes_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    import vep_link.server_manager as sm

    recorded: dict[str, object] = {}

    def fake_run(app: object, **kwargs: object) -> None:
        recorded["app"] = app
        recorded.update(kwargs)

    monkeypatch.setattr(sm.uvicorn, "run", fake_run)
    run_server(host="10.0.0.5", port=9999)

    assert isinstance(recorded["app"], FastAPI)
    assert recorded["host"] == "10.0.0.5"
    assert recorded["port"] == 9999


def test_run_server_defaults_to_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    import vep_link.server_manager as sm
    from vep_link.config import settings

    recorded: dict[str, object] = {}

    def fake_run(app: object, **kwargs: object) -> None:
        recorded.update(kwargs)

    monkeypatch.setattr(sm.uvicorn, "run", fake_run)
    run_server()

    assert recorded["host"] == settings.MCP_HOST
    assert recorded["port"] == settings.MCP_PORT


def test_server_module_exposes_app() -> None:
    sys.modules.pop("server", None)
    module = importlib.import_module("server")
    assert isinstance(module.app, FastAPI)


def test_mcp_server_import_does_not_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Importing ``mcp_server`` must not start the stdio server (only __main__ does)."""
    called = {"run": False}

    import vep_link.mcp.facade as facade_mod

    real_create = facade_mod.create_vep_mcp

    def tracking_create(**kwargs: object):  # type: ignore[no-untyped-def]
        mcp = real_create(**kwargs)
        original_run = mcp.run

        def fake_run(*args: object, **kw: object) -> None:
            called["run"] = True
            return original_run(*args, **kw)  # type: ignore[no-any-return]

        monkeypatch.setattr(mcp, "run", fake_run)
        return mcp

    monkeypatch.setattr(facade_mod, "create_vep_mcp", tracking_create)

    sys.modules.pop("mcp_server", None)
    importlib.import_module("mcp_server")
    assert called["run"] is False
