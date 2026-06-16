"""Shared pytest fixtures for vep-link.

Key invariant: **zero real network access**. An autouse fixture blocks outbound
socket connections so any un-mocked HTTP call fails loudly instead of hitting the
live Ensembl REST API. Tests that genuinely talk to Ensembl must be marked
``integration`` (excluded from the default run) and opt out via the
``allow_network`` marker.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from typing import Any

import pytest

from vep_link.config import Settings
from vep_link.logging_config import configure_logging

# Configure structlog once for the session. Use the JSON renderer (as in
# production) so exception logging via ``format_exc_info`` does not emit the
# ConsoleRenderer "pretty exceptions" warning during error-path tests.
configure_logging("WARNING", "json")

_REAL_SOCKET = socket.socket


class _BlockedSocket(socket.socket):
    """A socket whose connect attempts raise, blocking real network egress."""

    def connect(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError(
            "Real network access is blocked in tests. Mock Ensembl with respx, "
            "or mark the test `integration` + `allow_network`."
        )

    def connect_ex(self, *args: Any, **kwargs: Any) -> int:
        raise RuntimeError("Real network access is blocked in tests (connect_ex).")


@pytest.fixture(autouse=True)
def _no_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Block real outbound sockets unless the test opts in with `allow_network`."""
    if request.node.get_closest_marker("allow_network"):
        return
    monkeypatch.setattr(socket, "socket", _BlockedSocket)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "allow_network: permit real outbound sockets (live integration tests)"
    )


@pytest.fixture
def settings() -> Settings:
    """A fresh Settings instance with deterministic, test-friendly values."""
    return Settings(
        MAX_RETRIES=2,
        BACKOFF_BASE_SECONDS=0.0,
        BACKOFF_MAX_SECONDS=0.0,
        INTER_CHUNK_DELAY_MS=0,
        REQUEST_TIMEOUT=5,
        CACHE_SIZE=64,
    )


class StubService:
    """In-memory stand-in for ``VepService`` used by MCP tool-layer tests.

    Records calls and returns canned payloads. Set ``*_return`` to control the
    return value and ``*_error`` to make a method raise.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.resolve_return: dict[str, Any] = {
            "variant_id": "1-1000-A-T",
            "assembly": "GRCh38",
            "gene_symbol": "GENE1",
            "most_severe_consequence": "missense_variant",
        }
        self.annotate_return: dict[str, Any] = {
            "variant_id": "1-1000-A-T",
            "assembly": "GRCh38",
            "most_severe_consequence": "missense_variant",
            "transcript_consequences": [],
        }
        self.batch_return: dict[str, Any] = {
            "results": [],
            "errors": [],
            "summary": {"requested": 0, "annotated": 0, "failed": 0},
        }
        self.recode_return: list[dict[str, Any]] = [
            {"input": "rs123", "vcf_string": ["1-1000-A-T"]}
        ]
        self.liftover_return: dict[str, Any] = {
            "input": "1-1000-A-T",
            "from_assembly": "GRCh37",
            "to_assembly": "GRCh38",
            "lifted": "1-1064-A-T",
        }
        self.resolve_error: Exception | None = None
        self.annotate_error: Exception | None = None
        self.batch_error: Exception | None = None
        self.recode_error: Exception | None = None
        self.liftover_error: Exception | None = None

    async def resolve(self, variant: str, build: Any) -> dict[str, Any]:
        self.calls.append(("resolve", {"variant": variant, "build": build}))
        if self.resolve_error:
            raise self.resolve_error
        return self.resolve_return

    async def annotate(
        self, variant: str, build: Any, *, vep_options: dict[str, str] | None = None
    ) -> dict[str, Any]:
        self.calls.append(
            ("annotate", {"variant": variant, "build": build, "vep_options": vep_options})
        )
        if self.annotate_error:
            raise self.annotate_error
        return self.annotate_return

    async def annotate_batch(
        self, variants: list[str], build: Any, *, vep_options: dict[str, str] | None = None
    ) -> dict[str, Any]:
        self.calls.append(
            ("annotate_batch", {"variants": variants, "build": build, "vep_options": vep_options})
        )
        if self.batch_error:
            raise self.batch_error
        return self.batch_return

    async def recode(
        self, variants: list[str], build: Any, *, fields: str | None = None
    ) -> list[dict[str, Any]]:
        self.calls.append(("recode", {"variants": variants, "build": build, "fields": fields}))
        if self.recode_error:
            raise self.recode_error
        return self.recode_return

    async def liftover(self, variant: str, from_build: Any, to_build: Any) -> dict[str, Any]:
        self.calls.append(
            ("liftover", {"variant": variant, "from_build": from_build, "to_build": to_build})
        )
        if self.liftover_error:
            raise self.liftover_error
        return self.liftover_return


@pytest.fixture
def stub_service() -> StubService:
    return StubService()


@pytest.fixture
def facade(stub_service: StubService) -> Iterator[Any]:
    """A FastMCP facade wired to the StubService (created lazily to avoid import cost)."""
    from vep_link.mcp.facade import create_vep_mcp

    yield create_vep_mcp(service_factory=lambda: stub_service)
