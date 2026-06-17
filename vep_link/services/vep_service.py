"""VepService -- the orchestration layer of vep-link.

Ties the pure pieces together into the pipeline the MCP tool layer drives:

    parse input -> recode (if HGVS/rsID) to canonical CHR-POS-REF-ALT
                -> VEP region POST -> extract -> normalized dict

Every public method returns plain dicts (or lists of dicts); response shaping
for MCP lives in a later layer. Build is always passed explicitly as a
:class:`~vep_link.models.enums.GenomeBuild`.

Caching: ``resolve``, ``annotate``, and ``recode`` are wrapped with
``async_lru.alru_cache`` so identical calls within the TTL skip the upstream
client entirely. Because ``alru_cache`` keys on positional/keyword arguments
and dicts/lists are unhashable, each cached method delegates to a private
``_*_impl`` taking only hashable args (``build`` as its ``.value`` string,
options as a ``json.dumps(..., sort_keys=True)`` string, variant lists as
tuples); the impl reconstructs the rich types.

Batch annotation mirrors variant-linker's ``processBatchVariants``: inputs are
canonicalized independently, parse/resolution failures are collected (never
fail the whole batch), identical canonical variants are de-duplicated into a
single VEP request, and results are fanned back out to every original input.
"""

from __future__ import annotations

import json

from async_lru import alru_cache

from vep_link.api.ensembl_client import EnsemblClient
from vep_link.config import Settings
from vep_link.exceptions import (
    AmbiguousMappingError,
    DataNotFoundError,
    EnsemblApiError,
    RateLimitedError,
    UnsupportedContigError,
    UpstreamInputError,
    UpstreamTimeoutError,
    VariantParseError,
    VepLinkError,
)
from vep_link.models.enums import GenomeBuild, InputKind
from vep_link.services._recoding import (
    aggregate_recode_entry,
    first_canonical_vcf_string,
)
from vep_link.services.extraction import build_annotation
from vep_link.variant import (
    cnv_to_vep_line,
    coordinate_to_vep_line,
    needs_recoding,
    parse_variant_input,
)

# Per-input batch error classification, most-specific subclass first
# (UnsupportedContigError is a VariantParseError; both must precede it).
_BATCH_ERROR_CODES: tuple[tuple[type[VepLinkError], str], ...] = (
    (UnsupportedContigError, "unsupported_input"),
    (VariantParseError, "invalid_input"),
    (DataNotFoundError, "not_found"),
    (RateLimitedError, "rate_limited"),
    (UpstreamTimeoutError, "upstream_timeout"),
    (EnsemblApiError, "upstream_unavailable"),
    (UpstreamInputError, "not_found"),
    (AmbiguousMappingError, "ambiguous"),
)


def _batch_error_code(exc: VepLinkError) -> str:
    """Map a known vep-link exception to its batch per-input error code."""
    for exc_type, code in _BATCH_ERROR_CODES:
        if isinstance(exc, exc_type):
            return code
    return "internal_error"


class VepService:
    """Orchestrates parse -> recode -> VEP -> extract for vep-link.

    The constructor owns the wiring; the cached impl methods are bound here so
    each ``VepService`` instance gets its own bounded cache.
    """

    def __init__(self, client: EnsemblClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings

        maxsize = settings.CACHE_SIZE
        ttl = float(settings.CACHE_TTL_SECONDS)
        self._resolve_cached = alru_cache(maxsize=maxsize, ttl=ttl)(self._resolve_impl)
        self._annotate_cached = alru_cache(maxsize=maxsize, ttl=ttl)(self._annotate_impl)
        self._recode_cached = alru_cache(maxsize=maxsize, ttl=ttl)(self._recode_impl)

    async def aclose(self) -> None:
        """Close the underlying Ensembl client."""
        await self._client.aclose()

    # -- canonicalization --------------------------------------------------

    async def _to_canonical(self, variant: str, build: GenomeBuild) -> tuple[str, str]:
        """Resolve a raw input to ``(canonical_id, vep_line)``.

        Coordinates and CNVs are already VEP-ready and skip the recoder. HGVS
        and rsID inputs are sent to the Variant Recoder; the first canonical
        ``CHR-POS-REF-ALT`` ``vcf_string`` becomes the canonical id. Raises
        :class:`VariantParseError` for unparseable input and
        :class:`DataNotFoundError` when the recoder yields no genomic
        coordinate.
        """
        vi = parse_variant_input(variant)

        if vi.kind is InputKind.COORDINATE:
            return vi.value, coordinate_to_vep_line(vi.value)
        if vi.kind is InputKind.CNV:
            return vi.value, cnv_to_vep_line(vi.value)

        if needs_recoding(vi):
            payload = await self._client.recoder_get(vi.value, build)
            canonical = first_canonical_vcf_string(payload)
            if canonical is None:
                raise DataNotFoundError(f"Could not resolve {variant!r} to a genomic coordinate")
            return canonical, coordinate_to_vep_line(canonical)

        # Defensive: parse_variant_input only emits the four kinds above.
        raise DataNotFoundError(f"Could not resolve {variant!r} to a genomic coordinate")

    # -- resolve -----------------------------------------------------------

    async def resolve(self, variant: str, build: GenomeBuild) -> dict:
        """Resolve a variant to a minimal annotation summary (cached)."""
        return await self._resolve_cached(variant, build.value)

    async def _resolve_impl(self, variant: str, build_value: str) -> dict:
        build = GenomeBuild(build_value)
        canonical, vep_line = await self._to_canonical(variant, build)
        records = await self._client.vep_region_post([vep_line], build)
        if not records:
            raise DataNotFoundError(f"No VEP annotation found for {canonical!r}")
        annotation = build_annotation(records[0], variant_id=canonical, assembly=build.value)
        return {
            "variant_id": annotation["variant_id"],
            "assembly": annotation["assembly"],
            "gene_symbol": annotation["gene_symbol"],
            "most_severe_consequence": annotation["most_severe_consequence"],
        }

    # -- annotate ----------------------------------------------------------

    async def annotate(
        self,
        variant: str,
        build: GenomeBuild,
        *,
        vep_options: dict[str, str] | None = None,
    ) -> dict:
        """Return the full normalized annotation for a single variant (cached)."""
        options_key = json.dumps(vep_options or {}, sort_keys=True)
        return await self._annotate_cached(variant, build.value, options_key)

    async def _annotate_impl(self, variant: str, build_value: str, options_key: str) -> dict:
        build = GenomeBuild(build_value)
        parsed: dict[str, str] = json.loads(options_key)
        options = parsed or None
        canonical, vep_line = await self._to_canonical(variant, build)
        records = await self._client.vep_region_post([vep_line], build, options=options)
        if not records:
            raise DataNotFoundError(f"No VEP annotation found for {canonical!r}")
        return build_annotation(records[0], variant_id=canonical, assembly=build.value)

    # -- annotate_batch ----------------------------------------------------

    async def annotate_batch(
        self,
        variants: list[str],
        build: GenomeBuild,
        *,
        vep_options: dict[str, str] | None = None,
    ) -> dict:
        """Annotate many variants; collect per-input errors, never fail wholesale.

        Identical canonical variants are de-duplicated into a single VEP region
        POST and the resulting record is fanned out to every original input.
        Returns ``{"results", "errors", "summary"}``.
        """
        requested = len(variants)
        if requested > self._settings.BATCH_MAX:
            raise UpstreamInputError(
                f"too many variants: {requested} (max {self._settings.BATCH_MAX})"
            )

        errors: list[dict] = []
        # canonical id -> the originals that map to it (preserves order).
        canonical_to_inputs: dict[str, list[str]] = {}
        # canonical id -> its VEP region line.
        canonical_to_line: dict[str, str] = {}

        for original in variants:
            try:
                canonical, vep_line = await self._to_canonical(original, build)
            except VepLinkError as exc:
                # Any known fault (parse, not-found, rate-limit, upstream) is
                # collected per-input with its mapped code; never abort the batch.
                errors.append(self._batch_error(original, _batch_error_code(exc), exc))
                continue
            except Exception as exc:  # last-resort: one bad input cannot crash the batch
                errors.append(self._batch_error(original, "internal_error", exc))
                continue
            canonical_to_inputs.setdefault(canonical, []).append(original)
            canonical_to_line[canonical] = vep_line

        results = await self._annotate_unique(
            canonical_to_inputs, canonical_to_line, build, vep_options
        )

        return {
            "results": results,
            "errors": errors,
            "summary": {
                "requested": requested,
                "annotated": len(results),
                "failed": len(errors),
            },
        }

    async def _annotate_unique(
        self,
        canonical_to_inputs: dict[str, list[str]],
        canonical_to_line: dict[str, str],
        build: GenomeBuild,
        vep_options: dict[str, str] | None,
    ) -> list[dict]:
        """POST the unique lines once; fan each record back to its inputs."""
        if not canonical_to_line:
            return []

        unique_lines = list(canonical_to_line.values())
        records = await self._client.vep_region_post(unique_lines, build, options=vep_options)
        # Index records by the line that produced them (record['input'] equals
        # the posted region line). Reverse the line->canonical map to recover
        # the canonical id for each returned record.
        line_to_canonical = {line: cid for cid, line in canonical_to_line.items()}
        record_by_canonical: dict[str, dict] = {}
        for record in records:
            posted_line = record.get("input")
            if not isinstance(posted_line, str):
                continue
            canonical = line_to_canonical.get(posted_line)
            if canonical is not None:
                record_by_canonical[canonical] = record

        results: list[dict] = []
        for canonical, originals in canonical_to_inputs.items():
            matched = record_by_canonical.get(canonical)
            if matched is None:
                continue
            annotation = build_annotation(matched, variant_id=canonical, assembly=build.value)
            for original in originals:
                # Original input wins over the VEP-echoed region line.
                results.append({**annotation, "input": original})
        return results

    @staticmethod
    def _batch_error(original: str, code: str, exc: Exception) -> dict:
        """Shape a per-input batch failure record."""
        return {"input": original, "error_code": code, "message": str(exc)}

    # -- recode ------------------------------------------------------------

    async def recode(
        self,
        variants: list[str],
        build: GenomeBuild,
        *,
        fields: str | None = None,
    ) -> list[dict]:
        """Batch-recode variants to aggregated HGVS/SPDI/vcf_string views (cached)."""
        return await self._recode_cached(tuple(variants), build.value, fields)

    async def _recode_impl(
        self, variants: tuple[str, ...], build_value: str, fields: str | None
    ) -> list[dict]:
        build = GenomeBuild(build_value)
        entries = await self._client.recoder_post(
            list(variants), build, fields=fields or "vcf_string"
        )
        return [aggregate_recode_entry(entry) for entry in entries]

    # -- liftover ----------------------------------------------------------

    async def liftover(self, variant: str, from_build: GenomeBuild, to_build: GenomeBuild) -> dict:
        """Lift a genomic coordinate from one assembly to another.

        Only ``CHR-POS-REF-ALT`` coordinates are liftable; HGVS/rsID inputs
        raise :class:`UnsupportedContigError`. A unique mapping yields the lifted
        coordinate; zero mappings raise :class:`DataNotFoundError` and multiple
        raise :class:`AmbiguousMappingError`.
        """
        vi = parse_variant_input(variant)
        if vi.kind in (InputKind.HGVS, InputKind.RSID):
            raise UnsupportedContigError(
                "liftover requires a genomic coordinate (CHR-POS-REF-ALT), not HGVS/rsID"
            )
        if vi.kind is not InputKind.COORDINATE:
            raise UnsupportedContigError("liftover requires a genomic coordinate (CHR-POS-REF-ALT)")

        chrom, pos, ref, alt = vi.value.split("-")
        region = f"{chrom}:{pos}..{pos}"
        result = await self._client.assembly_map(region, from_build, to_build)
        mappings = result.get("mappings", [])

        if len(mappings) == 0:
            raise DataNotFoundError(
                f"No liftover mapping found for {vi.value!r} "
                f"({from_build.value} -> {to_build.value})"
            )
        if len(mappings) > 1:
            raise AmbiguousMappingError(
                f"Liftover of {vi.value!r} produced {len(mappings)} mappings "
                f"({from_build.value} -> {to_build.value})"
            )

        mapped = mappings[0]["mapped"]
        seq_region = mapped["seq_region_name"]
        start = mapped["start"]
        return {
            "input": vi.value,
            "from_assembly": from_build.value,
            "to_assembly": to_build.value,
            "lifted": f"{seq_region}-{start}-{ref}-{alt}",
            "mapped_region": f"{seq_region}:{start}",
        }
