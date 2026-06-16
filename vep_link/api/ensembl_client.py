"""Build-aware async client for the Ensembl REST API.

Wraps the three Ensembl endpoints vep-link depends on -- Variant Recoder
(GET single / POST batch), VEP (region POST, hgvs/id GET), and the assembly
``map`` liftover -- on top of :class:`~vep_link.api.base_client.BaseHTTPClient`
for resilient, bounded-concurrency transport.

Every call routes to the host for its assembly via the build-aware
``Settings.{vep,recoder,map}_url`` helpers: GRCh38 -> ``rest.ensembl.org``,
GRCh37 -> ``grch37.rest.ensembl.org``.

Batch POSTs (recoder + VEP region) are split into ``settings.CHUNK_SIZE``
chunks with a polite ``settings.INTER_CHUNK_DELAY_MS`` pause between them
(mirroring variant-linker's 200-element / 100 ms convention) and the per-chunk
list results are concatenated. The inter-chunk sleep is delegated to the HTTP
client's monkeypatchable ``_sleep`` so tests stay deterministic.

Path segments that may contain ``:``, ``>`` or other reserved characters
(HGVS, regions) are percent-encoded as a single segment with
``urllib.parse.quote(value, safe="")``.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from vep_link.api.base_client import BaseHTTPClient
from vep_link.config import (
    ASSEMBLY_MAP_PATH,
    DEFAULT_VEP_OPTIONS,
    RECODER_GET_PATH,
    RECODER_POST_PATH,
    VEP_HGVS_PATH,
    VEP_ID_PATH,
    VEP_REGION_PATH,
    Settings,
)
from vep_link.models.enums import GenomeBuild


def _fields_to_params(fields: str) -> dict[str, str]:
    """Turn a comma-separated ``fields`` string into Ensembl ``{name: "1"}`` flags."""
    return {name: "1" for f in fields.split(",") if (name := f.strip())}


def _vep_params(options: dict[str, str] | None) -> dict[str, str]:
    """Merge the default VEP option profile with caller overrides (caller wins)."""
    return {**DEFAULT_VEP_OPTIONS, **(options or {})}


class EnsemblClient:
    """Async wrapper over Ensembl VEP / Variant Recoder / assembly-map endpoints."""

    def __init__(self, settings: Settings, http: BaseHTTPClient | None = None) -> None:
        self._settings = settings
        # Own the transport only when we build it; an injected client is the
        # caller's to manage, but ``aclose`` still closes it for convenience.
        self._http = http if http is not None else BaseHTTPClient(settings)

    async def aclose(self) -> None:
        """Close the underlying HTTP client. Idempotent."""
        await self._http.aclose()

    async def __aenter__(self) -> EnsemblClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # -- internal helpers --------------------------------------------------

    async def _post_chunked(
        self,
        url: str,
        items: list[str],
        body_key: str,
        params: dict[str, str],
    ) -> list[dict[str, Any]]:
        """POST ``items`` under ``body_key`` in CHUNK_SIZE batches; concat results.

        A single request is issued when ``items`` fits in one chunk; otherwise
        a polite ``INTER_CHUNK_DELAY_MS`` pause separates consecutive chunks.
        """
        chunk_size = max(1, self._settings.CHUNK_SIZE)
        delay = self._settings.INTER_CHUNK_DELAY_MS / 1000
        aggregated: list[dict[str, Any]] = []
        for index in range(0, len(items), chunk_size):
            chunk = items[index : index + chunk_size]
            payload = await self._http.post_json(url, {body_key: chunk}, params)
            if isinstance(payload, list):
                aggregated.extend(payload)
            if delay > 0 and index + chunk_size < len(items):
                await self._http._sleep(delay)
        return aggregated

    # -- Variant Recoder ---------------------------------------------------

    async def recoder_get(
        self, variant: str, build: GenomeBuild, *, fields: str = "vcf_string"
    ) -> Any:
        """GET recoded info for a single variant (rsID / HGVS / VCF string).

        Ensembl returns a JSON list; it is passed through unchanged.
        """
        url = f"{self._settings.recoder_url(build)}{RECODER_GET_PATH}/{quote(variant, safe='')}"
        return await self._http.get_json(url, _fields_to_params(fields))

    async def recoder_post(
        self, ids: list[str], build: GenomeBuild, *, fields: str = "vcf_string"
    ) -> list[dict]:
        """POST a batch of variant ids to the Variant Recoder; aggregate results."""
        url = f"{self._settings.recoder_url(build)}{RECODER_POST_PATH}"
        return await self._post_chunked(url, ids, "ids", _fields_to_params(fields))

    # -- VEP ---------------------------------------------------------------

    async def vep_region_post(
        self,
        lines: list[str],
        build: GenomeBuild,
        *,
        options: dict[str, str] | None = None,
    ) -> list[dict]:
        """POST region-format variant lines to VEP; aggregate chunked results."""
        url = f"{self._settings.vep_url(build)}{VEP_REGION_PATH}"
        return await self._post_chunked(url, lines, "variants", _vep_params(options))

    async def vep_hgvs_get(
        self, hgvs: str, build: GenomeBuild, *, options: dict[str, str] | None = None
    ) -> list[dict]:
        """GET VEP annotation for an HGVS notation."""
        url = f"{self._settings.vep_url(build)}{VEP_HGVS_PATH}/{quote(hgvs, safe='')}"
        result: list[dict] = await self._http.get_json(url, _vep_params(options))
        return result

    async def vep_id_get(
        self, rsid: str, build: GenomeBuild, *, options: dict[str, str] | None = None
    ) -> list[dict]:
        """GET VEP annotation for a known-variant identifier (e.g. an rsID)."""
        url = f"{self._settings.vep_url(build)}{VEP_ID_PATH}/{quote(rsid, safe='')}"
        result: list[dict] = await self._http.get_json(url, _vep_params(options))
        return result

    # -- assembly map (liftover) -------------------------------------------

    async def assembly_map(
        self, region: str, from_build: GenomeBuild, to_build: GenomeBuild
    ) -> dict:
        """GET a coordinate liftover for ``region`` from ``from_build`` to ``to_build``.

        Routes to the FROM build's host, since that is the assembly the input
        region is expressed in.
        """
        url = (
            f"{self._settings.map_url(from_build)}{ASSEMBLY_MAP_PATH}"
            f"/{from_build.value}/{quote(region, safe='')}/{to_build.value}"
        )
        result: dict = await self._http.get_json(url)
        return result
