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
    canonical_vcf_strings,
    first_canonical_vcf_string,
    project_recode_fields,
)
from vep_link.services.extraction import build_annotation
from vep_link.services.warnings import multiple_alts_warning, ref_not_validated_warning
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


def _allele_matches(canonical: str, allele: str) -> bool:
    """Whether ``canonical`` (CHR-POS-REF-ALT) matches an ALT filter.

    ``allele`` may be a full ``CHR-POS-REF-ALT`` string or just the ALT base
    (e.g. ``"A"``); the latter matches on the trailing allele field.
    """
    return allele == canonical or canonical.rsplit("-", 1)[-1] == allele


def _alt_warnings(pairs: list[tuple[str, str]]) -> list[dict]:
    """Build the shared ``warnings`` list for a resolved pair set.

    A ``multiple_alts`` warning when an input expanded to >1 ALT allele, else [].
    """
    if len(pairs) <= 1:
        return []
    return [multiple_alts_warning([canonical for canonical, _ in pairs])]


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

    async def _canonical_lines(
        self, variant: str, build: GenomeBuild, allele: str | None
    ) -> list[tuple[str, str]]:
        """All ``(canonical_id, vep_line)`` pairs for an input.

        Coordinates/CNVs yield exactly one pair. A recoded rsID/HGVS yields one
        pair *per distinct ALT allele* (deterministically sorted), so a
        multi-allelic input is never silently collapsed to a single alt. When
        ``allele`` is given (an ALT base or full ``CHR-POS-REF-ALT``), the pairs
        are filtered to it. Raises :class:`DataNotFoundError` when nothing
        resolves (or nothing matches ``allele``).
        """
        vi = parse_variant_input(variant)
        if vi.kind is InputKind.COORDINATE:
            pairs = [(vi.value, coordinate_to_vep_line(vi.value))]
        elif vi.kind is InputKind.CNV:
            pairs = [(vi.value, cnv_to_vep_line(vi.value))]
        elif needs_recoding(vi):
            payload = await self._client.recoder_get(vi.value, build)
            canonicals = canonical_vcf_strings(payload)
            if not canonicals:
                raise DataNotFoundError(f"Could not resolve {variant!r} to a genomic coordinate")
            pairs = [(c, coordinate_to_vep_line(c)) for c in canonicals]
        else:  # defensive: parse_variant_input only emits the four kinds above.
            raise DataNotFoundError(f"Could not resolve {variant!r} to a genomic coordinate")

        if allele is not None:
            pairs = [pair for pair in pairs if _allele_matches(pair[0], allele)]
            if not pairs:
                raise DataNotFoundError(f"No ALT allele matching {allele!r} for {variant!r}")
        return pairs

    # -- resolve -----------------------------------------------------------

    async def resolve(self, variant: str, build: GenomeBuild, *, allele: str | None = None) -> dict:
        """Resolve a variant to one minimal summary per ALT allele (cached)."""
        return await self._resolve_cached(variant, build.value, allele)

    async def _resolve_impl(self, variant: str, build_value: str, allele: str | None) -> dict:
        build = GenomeBuild(build_value)
        pairs = await self._canonical_lines(variant, build, allele)
        records = await self._client.vep_region_post([line for _, line in pairs], build)
        by_line: dict[str, dict] = {
            line: rec for rec in records if isinstance((line := rec.get("input")), str)
        }
        variants: list[dict] = []
        for canonical, line in pairs:
            record = by_line.get(line)
            if record is None:
                continue
            ann = build_annotation(record, variant_id=canonical, assembly=build.value)
            variants.append(
                {
                    "variant_id": ann["variant_id"],
                    "assembly": ann["assembly"],
                    "gene_symbol": ann["gene_symbol"],
                    "most_severe_consequence": ann["most_severe_consequence"],
                }
            )
        if not variants:
            raise DataNotFoundError(f"No VEP annotation found for {variant!r}")
        return {
            "query": variant,
            "assembly": build.value,
            "variants": variants,
            "warnings": _alt_warnings(pairs),
        }

    # -- annotate ----------------------------------------------------------

    async def annotate(
        self,
        variant: str,
        build: GenomeBuild,
        *,
        vep_options: dict[str, str] | None = None,
        allele: str | None = None,
    ) -> dict:
        """Return the full normalized annotation, one entry per ALT allele (cached)."""
        options_key = json.dumps(vep_options or {}, sort_keys=True)
        return await self._annotate_cached(variant, build.value, options_key, allele)

    async def _annotate_impl(
        self, variant: str, build_value: str, options_key: str, allele: str | None
    ) -> dict:
        build = GenomeBuild(build_value)
        parsed: dict[str, str] = json.loads(options_key)
        options = parsed or None
        pairs = await self._canonical_lines(variant, build, allele)
        records = await self._client.vep_region_post(
            [line for _, line in pairs], build, options=options
        )
        by_line: dict[str, dict] = {
            line: rec for rec in records if isinstance((line := rec.get("input")), str)
        }
        variants = [
            build_annotation(by_line[line], variant_id=canonical, assembly=build.value)
            for canonical, line in pairs
            if line in by_line
        ]
        if not variants:
            raise DataNotFoundError(f"No VEP annotation found for {variant!r}")
        return {
            "query": variant,
            "assembly": build.value,
            "variants": variants,
            "warnings": _alt_warnings(pairs),
        }

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
        # Omitting `fields` requests the recoder's FULL default set (pass "" so no
        # field flags are sent); an explicit filter is forwarded verbatim.
        entries = await self._client.recoder_post(
            list(variants), build, fields=fields if fields is not None else ""
        )
        # The recoder preserves request order and POST entries do not echo the
        # caller's query, so map our inputs back positionally. If the upstream
        # count diverges, fall back to the entry's own input rather than misalign.
        aligned = len(entries) == len(variants)
        # Enforce the `fields` filter client-side: Ensembl may ignore or only
        # partially honor the upstream param, so project after aggregation.
        return [
            project_recode_fields(
                aggregate_recode_entry(
                    entry, input_override=variants[i] if aligned else None
                ),
                fields,
            )
            for i, entry in enumerate(entries)
        ]

    # -- liftover ----------------------------------------------------------

    async def liftover(self, variant: str, from_build: GenomeBuild, to_build: GenomeBuild) -> dict:
        """Lift a genomic coordinate from one assembly to another.

        Only ``CHR-POS-REF-ALT`` coordinates are liftable; HGVS/rsID inputs
        raise :class:`UnsupportedContigError`. A unique mapping yields the lifted
        coordinate; zero mappings raise :class:`DataNotFoundError` and multiple
        raise :class:`AmbiguousMappingError`.

        The assembly-map endpoint is coordinate-only, so the input REF/ALT are
        carried through unchanged -- which can be *wrong* if the reference base
        differs between builds. When ``LIFTOVER_VALIDATE_REF`` is set, the carried
        REF is checked against the target-assembly reference base: a match returns
        the full ``CHR-POS-REF-ALT``; a mismatch returns coordinate-only plus a
        ``ref_not_validated`` warning rather than a confidently-wrong allele.
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
        lifted = f"{seq_region}-{start}-{ref}-{alt}"
        warnings: list[dict] = []
        if self._settings.LIFTOVER_VALIDATE_REF:
            target_ref = await self._client.sequence_region_ref(seq_region, start, to_build)
            if target_ref is not None and target_ref != ref.upper():
                # The carried REF is wrong for the target build: drop the alleles.
                lifted = f"{seq_region}-{start}"
                warnings.append(ref_not_validated_warning(expected=target_ref, carried=ref.upper()))
        return {
            "input": vi.value,
            "from_assembly": from_build.value,
            "to_assembly": to_build.value,
            "lifted": lifted,
            "mapped_region": f"{seq_region}:{start}",
            "warnings": warnings,
        }
