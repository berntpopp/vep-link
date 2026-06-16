"""Tests for the vep-link typer CLI.

These follow the GeneFoundry Logging & CLI Standard: a single ``Typer`` app
exposing ``serve`` / ``config`` / ``health`` / ``version``. Network is blocked
by the conftest no-network guard, so the ``health`` tests mock the httpx client
and the ``serve`` tests stub the server runner; nothing here touches a socket.
"""

from __future__ import annotations

import importlib.metadata
import sys
import types
from typing import Any

import httpx
import pytest
import typer
from typer.testing import CliRunner

from vep_link import __version__
from vep_link.cli import app

runner = CliRunner()


def test_app_is_typer() -> None:
    assert isinstance(app, typer.Typer)
    assert app.info.name == "vep-link"


def test_help_lists_required_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("serve", "config", "health", "version"):
        assert command in result.output


def test_no_args_shows_help() -> None:
    result = runner.invoke(app, [])
    # no_args_is_help=True -> help text, non-zero exit per typer convention.
    assert result.exit_code != 0
    assert "serve" in result.output


def test_version_command_prints_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output
    assert "0.1.0" in result.output


def test_config_command_runs() -> None:
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0
    assert "GRCh38" in result.output
    assert "rest.ensembl.org" in result.output


def test_config_shows_key_settings() -> None:
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0
    # A handful of the resolved settings surfaced in the table.
    assert "GRCh37" in result.output
    assert "unified" in result.output


def test_config_validate_ok() -> None:
    result = runner.invoke(app, ["config", "--validate"])
    assert result.exit_code == 0


def _install_fake_server_manager(
    monkeypatch: pytest.MonkeyPatch, recorder: list[dict[str, Any]]
) -> None:
    """Install a stub ``vep_link.server_manager`` exposing ``run_server``.

    The CLI's ``serve`` command does ``from vep_link.server_manager import
    run_server`` lazily, so providing a stub module makes the import resolve and
    captures the call instead of starting a real server. This keeps the test
    independent of whether the real module exists yet.
    """

    def _fake_run_server(
        *,
        transport: str = "unified",
        host: str | None = None,
        port: int | None = None,
        mcp_path: str | None = None,
        log_level: str | None = None,
        dev: bool = False,
    ) -> None:
        recorder.append(
            {
                "transport": transport,
                "host": host,
                "port": port,
                "mcp_path": mcp_path,
                "log_level": log_level,
                "dev": dev,
            }
        )

    module = types.ModuleType("vep_link.server_manager")
    module.run_server = _fake_run_server  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "vep_link.server_manager", module)


def test_serve_defers_to_run_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """serve passes options through to run_server without starting a server."""
    calls: list[dict[str, Any]] = []
    _install_fake_server_manager(monkeypatch, calls)

    result = runner.invoke(app, ["serve", "--host", "0.0.0.0", "--port", "9000"])  # noqa: S104

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    call = calls[0]
    assert call["transport"] == "unified"
    assert call["host"] == "0.0.0.0"  # noqa: S104
    assert call["port"] == 9000


def test_serve_passes_transport_and_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    _install_fake_server_manager(monkeypatch, calls)

    result = runner.invoke(
        app,
        [
            "serve",
            "--transport",
            "http",
            "--mcp-path",
            "/api",
            "--log-level",
            "DEBUG",
            "--dev",
        ],
    )

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    call = calls[0]
    assert call["transport"] == "http"
    assert call["mcp_path"] == "/api"
    assert call["log_level"] == "DEBUG"
    assert call["dev"] is True


def test_serve_rejects_invalid_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    _install_fake_server_manager(monkeypatch, calls)

    result = runner.invoke(app, ["serve", "--transport", "stdio"])
    assert result.exit_code != 0
    assert "stdio" in result.output.lower()
    # The runner must never be reached on a validation failure.
    assert calls == []


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


def test_health_reports_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_get(self: httpx.Client, url: str, *args: Any, **kwargs: Any) -> _FakeResponse:
        assert url.endswith("/health")
        return _FakeResponse(200, {"status": "healthy", "transport": "unified"})

    monkeypatch.setattr(httpx.Client, "get", _fake_get)

    result = runner.invoke(app, ["health", "--url", "http://x"])
    assert result.exit_code == 0, result.output
    assert "healthy" in result.output.lower()


def test_health_non_200_exits_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_get(self: httpx.Client, url: str, *args: Any, **kwargs: Any) -> _FakeResponse:
        return _FakeResponse(503, {"status": "unhealthy"})

    monkeypatch.setattr(httpx.Client, "get", _fake_get)

    result = runner.invoke(app, ["health", "--url", "http://x"])
    assert result.exit_code != 0


def test_health_connection_error_is_graceful(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_get(self: httpx.Client, url: str, *args: Any, **kwargs: Any) -> _FakeResponse:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx.Client, "get", _fake_get)

    result = runner.invoke(app, ["health", "--url", "http://127.0.0.1:8000"])
    assert result.exit_code != 0
    # Graceful: a message, not a traceback.
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "connect" in result.output.lower() or "error" in result.output.lower()


def test_console_script_entry_resolves() -> None:
    """The console-script entry point resolves to the typer app."""
    (entry,) = [
        ep
        for ep in importlib.metadata.entry_points(group="console_scripts")
        if ep.name == "vep-link"
    ]
    assert entry.value == "vep_link.cli:app"
    assert entry.load() is app
