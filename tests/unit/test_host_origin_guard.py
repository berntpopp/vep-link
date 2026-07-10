"""Security contract for strict Host and Origin validation."""

from __future__ import annotations

import inspect
from importlib.metadata import version

import pytest
from fastapi.testclient import TestClient
from packaging.version import Version
from pydantic import ValidationError

from vep_link import server_manager
from vep_link.config import Settings, settings


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(
        settings,
        "MCP_ALLOWED_HOSTS",
        ["localhost", "127.0.0.1", "::1", "vep-link.example.org"],
    )
    monkeypatch.setattr(
        settings,
        "MCP_ALLOWED_ORIGINS",
        ["https://vep-link.example.org"],
    )
    return TestClient(server_manager.build_app(), raise_server_exceptions=False)


def test_fastmcp_supports_native_strict_guard_configuration() -> None:
    assert Version(version("fastmcp")) >= Version("3.4.4")
    source = inspect.getsource(server_manager)
    assert "host_origin_protection=True" in source
    assert "allowed_hosts=settings.MCP_ALLOWED_HOSTS" in source
    assert "allowed_origins=settings.MCP_ALLOWED_ORIGINS" in source


@pytest.mark.parametrize(
    "host",
    ["localhost", "localhost:8000", "127.0.0.1:8000", "[::1]", "[::1]:8000"],
)
def test_loopback_hosts_are_allowed(client: TestClient, host: str) -> None:
    assert client.get("/health", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize("host", ["vep-link.example.org", "vep-link.example.org:8443"])
def test_configured_public_host_is_allowed(client: TestClient, host: str) -> None:
    assert client.get("/health", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize("path", ["/health", "/metrics", "/mcp"])
def test_unlisted_host_is_rejected_on_every_route(client: TestClient, path: str) -> None:
    assert client.get(path, headers={"Host": "attacker.example"}).status_code == 421


@pytest.mark.parametrize("path", ["/health", "/metrics", "/mcp"])
def test_unlisted_origin_is_rejected_on_every_route(client: TestClient, path: str) -> None:
    response = client.get(
        path,
        headers={"Host": "localhost", "Origin": "https://attacker.example"},
    )
    assert response.status_code == 403


@pytest.mark.parametrize("origin", [None, "https://vep-link.example.org"])
def test_absent_or_configured_origin_is_allowed(client: TestClient, origin: str | None) -> None:
    headers = {"Host": "localhost"}
    if origin is not None:
        headers["Origin"] = origin
    assert client.get("/health", headers=headers).status_code == 200


def test_default_empty_origin_allowlist_rejects_present_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "MCP_ALLOWED_HOSTS", ["localhost"])
    monkeypatch.setattr(settings, "MCP_ALLOWED_ORIGINS", [])
    default_client = TestClient(server_manager.build_app(), raise_server_exceptions=False)
    response = default_client.get(
        "/health",
        headers={"Host": "localhost", "Origin": "https://browser.example"},
    )
    assert response.status_code == 403


def test_untrusted_preflight_is_rejected_by_outer_guard(client: TestClient) -> None:
    response = client.options(
        "/health",
        headers={
            "Host": "attacker.example",
            "Origin": "https://attacker.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 421


@pytest.mark.parametrize(
    ("field", "entry"),
    [
        ("MCP_ALLOWED_HOSTS", "*"),
        ("MCP_ALLOWED_HOSTS", "*.example.org"),
        ("MCP_ALLOWED_HOSTS", "host?.example.org"),
        ("MCP_ALLOWED_HOSTS", "host[0].example.org"),
        ("MCP_ALLOWED_ORIGINS", "https://*.example.org"),
    ],
)
def test_wildcard_allowlist_entries_are_rejected(field: str, entry: str) -> None:
    with pytest.raises(ValidationError, match="wildcard"):
        Settings(_env_file=None, **{field: [entry]})


def test_allowlists_load_from_prefixed_json_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VEP_LINK_MCP_ALLOWED_HOSTS", '["api.example.org"]')
    monkeypatch.setenv("VEP_LINK_MCP_ALLOWED_ORIGINS", '["https://app.example.org"]')
    configured = Settings(_env_file=None)
    assert configured.MCP_ALLOWED_HOSTS == ["api.example.org"]
    assert configured.MCP_ALLOWED_ORIGINS == ["https://app.example.org"]
