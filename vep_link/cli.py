"""Typer command line interface for the vep-link server.

GeneFoundry Logging & CLI Standard: a single ``Typer`` app exposing
``serve`` / ``config`` / ``health`` / ``version``. The unified transport hosts
FastAPI ``/health`` with the MCP Streamable HTTP app mounted at ``/mcp``; the
``http`` transport serves the MCP app on its own.

The module is kept import-cheap: ``serve`` imports the (potentially heavy)
server runner lazily inside the command body so ``--help``, ``version``, and the
console-script entry point resolve without pulling in uvicorn/fastmcp.
"""

from __future__ import annotations

from typing import Annotated

import httpx
import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .config import ServerConfig, Settings, settings

app = typer.Typer(
    name="vep-link",
    add_completion=False,
    no_args_is_help=True,
    help="vep-link unified server (FastAPI /health + MCP Streamable HTTP) for "
    "Ensembl VEP and Variant Recoder.",
)
console = Console()

_VALID_TRANSPORTS = {"unified", "http"}
_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


@app.command()
def serve(
    transport: Annotated[
        str,
        typer.Option(help="Transport mode: 'unified' or 'http'."),
    ] = "unified",
    host: Annotated[
        str | None,
        typer.Option(help="Host to bind to (defaults to configured MCP_HOST)."),
    ] = None,
    port: Annotated[
        int | None,
        typer.Option(help="Port to bind to (defaults to configured MCP_PORT)."),
    ] = None,
    mcp_path: Annotated[
        str | None,
        typer.Option(help="MCP endpoint path (defaults to configured MCP_PATH)."),
    ] = None,
    log_level: Annotated[
        str | None,
        typer.Option(help="Log level: DEBUG, INFO, WARNING, ERROR, or CRITICAL."),
    ] = None,
    dev: Annotated[
        bool,
        typer.Option("--dev/--no-dev", help="Development mode (verbose console logging)."),
    ] = False,
) -> None:
    """Start the server, deferring to ``server_manager.run_server``.

    Validates transport/log-level, then lazily imports and calls
    ``run_server`` so this module stays cheap to import.
    """
    if transport not in _VALID_TRANSPORTS:
        console.print(
            f"[red]Invalid transport '{transport}'. Choose 'unified' or 'http' "
            "(stdio is not supported).[/red]"
        )
        raise typer.Exit(code=2)
    if log_level is not None and log_level.upper() not in _VALID_LOG_LEVELS:
        console.print(f"[red]Invalid log level '{log_level}'.[/red]")
        raise typer.Exit(code=2)

    # Imported lazily so `--help`, `version`, etc. don't pull in uvicorn/fastmcp.
    from .server_manager import run_server

    bind_host = host if host is not None else settings.MCP_HOST
    bind_port = port if port is not None else settings.MCP_PORT
    console.print(
        f"[green]Starting vep-link on {bind_host}:{bind_port}[/green] (transport={transport})"
    )

    run_server(
        transport=transport,
        host=host,
        port=port,
        mcp_path=mcp_path,
        log_level=log_level.upper() if log_level is not None else None,
        dev=dev,
    )


@app.command()
def config(
    validate: Annotated[
        bool,
        typer.Option("--validate/--no-validate", help="Re-validate the resolved configuration."),
    ] = False,
) -> None:
    """Show the resolved server and upstream configuration as a table."""
    cfg = ServerConfig.from_env()

    table = Table(title="vep-link configuration", show_header=True, header_style="bold")
    table.add_column("Setting", style="cyan", no_wrap=True)
    table.add_column("Value", style="white")

    table.add_row("Default assembly", settings.DEFAULT_ASSEMBLY)
    table.add_row("Ensembl URL (GRCh38)", settings.VEP_GRCH38_URL)
    table.add_row("Ensembl URL (GRCh37)", settings.VEP_GRCH37_URL)
    table.add_row("Request timeout", f"{settings.REQUEST_TIMEOUT}s")
    table.add_row("Max concurrency", str(settings.MAX_CONCURRENCY))
    table.add_row("Queue wait timeout", f"{settings.QUEUE_WAIT_TIMEOUT}s")
    table.add_row("Max retries", str(settings.MAX_RETRIES))
    table.add_row("Chunk size", str(settings.CHUNK_SIZE))
    table.add_row("Batch max", str(settings.BATCH_MAX))
    table.add_row("Cache size / TTL", f"{settings.CACHE_SIZE} / {settings.CACHE_TTL_SECONDS}s")
    table.add_row("Transport", cfg.transport)
    table.add_row("Host", cfg.host)
    table.add_row("Port", str(cfg.port))
    table.add_row("MCP path", cfg.mcp_path)
    table.add_row("Log level", cfg.log_level)
    table.add_row("Log format", settings.LOG_FORMAT)

    console.print(table)

    if validate:
        try:
            Settings()
        except Exception as exc:  # pragma: no cover - defensive; pydantic raises ValidationError
            console.print(f"[red]Configuration is invalid: {exc}[/red]")
            raise typer.Exit(code=1) from exc
        console.print("[green]Configuration is valid.[/green]")


@app.command()
def health(
    url: Annotated[
        str,
        typer.Option(help="Base server URL to probe."),
    ] = "http://127.0.0.1:8000",
) -> None:
    """Check a running server's ``/health`` endpoint."""
    endpoint = f"{url.rstrip('/')}/health"
    try:
        with httpx.Client(timeout=5) as client:
            response = client.get(endpoint)
    except httpx.HTTPError as exc:
        console.print(f"[red]Failed to connect to server: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    if response.status_code != 200:
        console.print(f"[red]Server returned status {response.status_code}.[/red]")
        raise typer.Exit(code=1)

    data = response.json()
    console.print("[green]Server is healthy.[/green]")
    console.print(f"Status: {data.get('status', 'unknown')}")
    transport = data.get("transport")
    if transport is not None:
        console.print(f"Transport: {transport}")


@app.command()
def version() -> None:
    """Print the installed vep-link version."""
    console.print(__version__)


if __name__ == "__main__":
    app()
