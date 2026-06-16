"""Tests for vep_link.config."""

from __future__ import annotations

import pytest

from vep_link.config import (
    DEFAULT_VEP_OPTIONS,
    VEP_OPTION_ALLOWLIST,
    Settings,
)
from vep_link.models.enums import GenomeBuild


def test_defaults() -> None:
    s = Settings()
    assert s.VEP_GRCH38_URL == "https://rest.ensembl.org"
    assert s.VEP_GRCH37_URL == "https://grch37.rest.ensembl.org"
    assert s.DEFAULT_ASSEMBLY == "GRCh38"
    assert s.CHUNK_SIZE == 200
    assert s.BATCH_MAX == 200
    assert s.MAX_RETRIES == 4
    assert s.MCP_PATH == "/mcp"


def test_build_aware_urls_enum() -> None:
    s = Settings()
    assert s.vep_url(GenomeBuild.GRCH37) == "https://grch37.rest.ensembl.org"
    assert s.vep_url(GenomeBuild.GRCH38) == "https://rest.ensembl.org"
    assert s.recoder_url(GenomeBuild.GRCH37) == "https://grch37.rest.ensembl.org"
    assert s.map_url(GenomeBuild.GRCH38) == "https://rest.ensembl.org"


def test_build_aware_urls_string() -> None:
    s = Settings()
    assert s.base_url("GRCh37") == "https://grch37.rest.ensembl.org"
    assert s.base_url("GRCh38") == "https://rest.ensembl.org"


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VEP_LINK_MAX_CONCURRENCY", "9")
    monkeypatch.setenv("VEP_LINK_CHUNK_SIZE", "50")
    s = Settings()
    assert s.MAX_CONCURRENCY == 9
    assert s.CHUNK_SIZE == 50


def test_mcp_path_normalized() -> None:
    assert Settings(MCP_PATH="mcp").MCP_PATH == "/mcp"


def test_cors_origins_list() -> None:
    assert Settings(CORS_ORIGINS="*").cors_origins_list == ["*"]
    assert Settings(CORS_ORIGINS="a.com, b.com").cors_origins_list == ["a.com", "b.com"]


def test_default_vep_options_and_allowlist() -> None:
    assert DEFAULT_VEP_OPTIONS["CADD"] == "1"
    assert "hgvs" in DEFAULT_VEP_OPTIONS
    # Every default option must be allowlisted.
    assert set(DEFAULT_VEP_OPTIONS).issubset(VEP_OPTION_ALLOWLIST)
