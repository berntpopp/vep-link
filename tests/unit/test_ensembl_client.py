"""Unit tests for the build-aware Ensembl REST client.

All upstream HTTP is mocked with ``respx``. The ``settings`` conftest fixture
pins ``INTER_CHUNK_DELAY_MS=0`` so the inter-chunk sleep is a no-op and chunking
loops run instantly and deterministically. Canned payloads come from
``tests.fixtures``; these mirror the real Ensembl Variant Recoder / VEP / map
response shapes so the client's URL, params, and body construction are exercised
against realistic data without any network access.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from tests.fixtures import (
    ASSEMBLY_MAP_ONE,
    RECODER_GET_RS123,
    RECODER_POST_BATCH,
    VEP_REGION_MISSENSE,
)
from vep_link.api.ensembl_client import EnsemblClient
from vep_link.config import Settings
from vep_link.models.enums import GenomeBuild

GRCH38_BASE = "https://rest.ensembl.org"
GRCH37_BASE = "https://grch37.rest.ensembl.org"


@pytest.fixture
def client(settings: Settings) -> EnsemblClient:
    """An Ensembl client over a real BaseHTTPClient (its sockets are blocked)."""
    return EnsemblClient(settings)


# --- recoder_get ----------------------------------------------------------


@respx.mock
async def test_recoder_get_grch38_hits_default_host_with_vcf_string(
    client: EnsemblClient,
) -> None:
    route = respx.get(f"{GRCH38_BASE}/variant_recoder/human/rs123").mock(
        return_value=httpx.Response(200, json=RECODER_GET_RS123)
    )
    try:
        result = await client.recoder_get("rs123", GenomeBuild.GRCH38)
    finally:
        await client.aclose()

    assert result == RECODER_GET_RS123
    assert route.called
    request = route.calls.last.request
    assert request.url.params["vcf_string"] == "1"


@respx.mock
async def test_recoder_get_grch37_hits_legacy_host(client: EnsemblClient) -> None:
    route = respx.get(f"{GRCH37_BASE}/variant_recoder/human/rs123").mock(
        return_value=httpx.Response(200, json=RECODER_GET_RS123)
    )
    try:
        result = await client.recoder_get("rs123", GenomeBuild.GRCH37)
    finally:
        await client.aclose()

    assert result == RECODER_GET_RS123
    assert route.called
    assert route.calls.last.request.url.host == "grch37.rest.ensembl.org"


@respx.mock
async def test_recoder_get_quotes_the_variant_path_segment(client: EnsemblClient) -> None:
    # An HGVS with ":" and ">" must be percent-encoded into a single path segment.
    route = respx.get(url__regex=rf"{GRCH38_BASE}/variant_recoder/human/.+").mock(
        return_value=httpx.Response(200, json=RECODER_GET_RS123)
    )
    try:
        await client.recoder_get("NM_004006.2:c.4375C>T", GenomeBuild.GRCH38)
    finally:
        await client.aclose()

    raw_path = route.calls.last.request.url.raw_path.decode()
    assert "NM_004006.2%3Ac.4375C%3ET" in raw_path
    assert "/variant_recoder/human/" in raw_path


@respx.mock
async def test_recoder_get_builds_multiple_field_params(client: EnsemblClient) -> None:
    route = respx.get(f"{GRCH38_BASE}/variant_recoder/human/rs123").mock(
        return_value=httpx.Response(200, json=RECODER_GET_RS123)
    )
    try:
        await client.recoder_get("rs123", GenomeBuild.GRCH38, fields="vcf_string,hgvsg")
    finally:
        await client.aclose()

    params = route.calls.last.request.url.params
    assert params["vcf_string"] == "1"
    assert params["hgvsg"] == "1"


# --- recoder_post ---------------------------------------------------------


@respx.mock
async def test_recoder_post_sends_ids_body_and_returns_batch(client: EnsemblClient) -> None:
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=RECODER_POST_BATCH)

    route = respx.post(f"{GRCH38_BASE}/variant_recoder/homo_sapiens").mock(side_effect=_handler)
    ids = ["rs123", "NM_004006.2:c.4375C>T"]
    try:
        result = await client.recoder_post(ids, GenomeBuild.GRCH38)
    finally:
        await client.aclose()

    assert result == RECODER_POST_BATCH
    assert route.called
    assert captured["body"] == {"ids": ids}
    assert route.calls.last.request.url.params["vcf_string"] == "1"


@respx.mock
async def test_recoder_post_chunks_and_aggregates(settings: Settings) -> None:
    chunked = settings.model_copy(update={"CHUNK_SIZE": 2})
    client = EnsemblClient(chunked)

    def _handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(200, json=[{"id": i} for i in body["ids"]])

    route = respx.post(f"{GRCH38_BASE}/variant_recoder/homo_sapiens").mock(side_effect=_handler)
    ids = ["rs1", "rs2", "rs3", "rs4", "rs5"]
    try:
        result = await client.recoder_post(ids, GenomeBuild.GRCH38)
    finally:
        await client.aclose()

    assert route.call_count == 3  # 2 + 2 + 1
    assert [r["id"] for r in result] == ids


# --- vep_region_post ------------------------------------------------------


@respx.mock
async def test_vep_region_post_sends_variants_body_with_default_options(
    client: EnsemblClient,
) -> None:
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=VEP_REGION_MISSENSE)

    route = respx.post(f"{GRCH38_BASE}/vep/homo_sapiens/region").mock(side_effect=_handler)
    lines = ["1 1000 . A T . . ."]
    try:
        result = await client.vep_region_post(lines, GenomeBuild.GRCH38)
    finally:
        await client.aclose()

    assert result == VEP_REGION_MISSENSE
    assert route.called
    assert captured["body"] == {"variants": lines}
    # Default VEP profile options are present in the query string.
    assert captured["params"]["CADD"] == "1"
    assert captured["params"]["hgvs"] == "1"
    assert captured["params"]["mane"] == "1"


@respx.mock
async def test_vep_region_post_caller_options_override_defaults(client: EnsemblClient) -> None:
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=VEP_REGION_MISSENSE)

    respx.post(f"{GRCH38_BASE}/vep/homo_sapiens/region").mock(side_effect=_handler)
    try:
        await client.vep_region_post(
            ["1 1000 . A T . . ."],
            GenomeBuild.GRCH38,
            options={"CADD": "0", "SpliceAI": "1"},
        )
    finally:
        await client.aclose()

    assert captured["params"]["CADD"] == "0"  # caller wins
    assert captured["params"]["SpliceAI"] == "1"  # caller-only flag added
    assert captured["params"]["hgvs"] == "1"  # untouched default preserved


@respx.mock
async def test_vep_region_post_chunks_and_aggregates(settings: Settings) -> None:
    chunked = settings.model_copy(update={"CHUNK_SIZE": 2})
    client = EnsemblClient(chunked)

    def _handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        # One result element per posted line.
        return httpx.Response(200, json=[{"input": line} for line in body["variants"]])

    route = respx.post(f"{GRCH38_BASE}/vep/homo_sapiens/region").mock(side_effect=_handler)
    lines = [f"1 {i} . A T . . ." for i in range(5)]
    try:
        result = await client.vep_region_post(lines, GenomeBuild.GRCH38)
    finally:
        await client.aclose()

    assert route.call_count == 3  # 5 lines / chunk size 2 -> 3 chunks
    assert len(result) == 5
    assert [r["input"] for r in result] == lines


# --- vep_hgvs_get / vep_id_get --------------------------------------------


@respx.mock
async def test_vep_hgvs_get_quotes_path_and_sends_default_options(client: EnsemblClient) -> None:
    route = respx.get(url__regex=rf"{GRCH38_BASE}/vep/human/hgvs/.+").mock(
        return_value=httpx.Response(200, json=VEP_REGION_MISSENSE)
    )
    try:
        result = await client.vep_hgvs_get("NM_004006.2:c.4375C>T", GenomeBuild.GRCH38)
    finally:
        await client.aclose()

    assert result == VEP_REGION_MISSENSE
    request = route.calls.last.request
    assert "NM_004006.2%3Ac.4375C%3ET" in request.url.raw_path.decode()
    assert request.url.params["CADD"] == "1"


@respx.mock
async def test_vep_id_get_hits_id_path_on_legacy_host(client: EnsemblClient) -> None:
    route = respx.get(f"{GRCH37_BASE}/vep/human/id/rs123").mock(
        return_value=httpx.Response(200, json=VEP_REGION_MISSENSE)
    )
    try:
        result = await client.vep_id_get("rs123", GenomeBuild.GRCH37)
    finally:
        await client.aclose()

    assert result == VEP_REGION_MISSENSE
    request = route.calls.last.request
    assert request.url.host == "grch37.rest.ensembl.org"
    assert request.url.params["hgvs"] == "1"


# --- assembly_map ---------------------------------------------------------


@respx.mock
async def test_assembly_map_uses_from_build_host_and_path(client: EnsemblClient) -> None:
    route = respx.get(f"{GRCH37_BASE}/map/human/GRCh37/1:1000..1000/GRCh38").mock(
        return_value=httpx.Response(200, json=ASSEMBLY_MAP_ONE)
    )
    try:
        result = await client.assembly_map("1:1000..1000", GenomeBuild.GRCH37, GenomeBuild.GRCH38)
    finally:
        await client.aclose()

    assert result == ASSEMBLY_MAP_ONE
    assert route.called
    assert route.calls.last.request.url.host == "grch37.rest.ensembl.org"


@respx.mock
async def test_assembly_map_quotes_region_segment(client: EnsemblClient) -> None:
    route = respx.get(url__regex=rf"{GRCH37_BASE}/map/human/GRCh37/.+/GRCh38").mock(
        return_value=httpx.Response(200, json=ASSEMBLY_MAP_ONE)
    )
    try:
        await client.assembly_map("1:1000..1000", GenomeBuild.GRCH37, GenomeBuild.GRCH38)
    finally:
        await client.aclose()

    raw_path = route.calls.last.request.url.raw_path.decode()
    assert "1%3A1000..1000" in raw_path


# --- injected http / lifecycle --------------------------------------------


@respx.mock
async def test_injected_http_client_is_used_and_owned_by_caller(settings: Settings) -> None:
    from vep_link.api.base_client import BaseHTTPClient

    http = BaseHTTPClient(settings)
    client = EnsemblClient(settings, http=http)
    route = respx.get(f"{GRCH38_BASE}/variant_recoder/human/rs123").mock(
        return_value=httpx.Response(200, json=RECODER_GET_RS123)
    )
    try:
        result = await client.recoder_get("rs123", GenomeBuild.GRCH38)
    finally:
        await client.aclose()

    assert result == RECODER_GET_RS123
    assert route.called


# --- sequence_region_ref (liftover REF validation) ------------------------


@respx.mock
async def test_sequence_region_ref_returns_uppercased_base(client: EnsemblClient) -> None:
    # The /sequence/region read returns the reference base; the client uppercases it.
    route = respx.get(url__regex=rf"{GRCH37_BASE}/sequence/region/human/.*169519049.*").mock(
        return_value=httpx.Response(200, json={"seq": "t"})
    )
    try:
        ref = await client.sequence_region_ref("1", 169519049, GenomeBuild.GRCH37)
    finally:
        await client.aclose()
    assert ref == "T"
    assert route.called
    # JSON is forced via the content-type query param (else Ensembl returns FASTA).
    assert route.calls.last.request.url.params["content-type"] == "application/json"


@respx.mock
async def test_sequence_region_ref_none_when_no_seq(client: EnsemblClient) -> None:
    respx.get(url__regex=rf"{GRCH38_BASE}/sequence/region/human/.*").mock(
        return_value=httpx.Response(200, json={})
    )
    try:
        ref = await client.sequence_region_ref("1", 1000, GenomeBuild.GRCH38)
    finally:
        await client.aclose()
    assert ref is None
