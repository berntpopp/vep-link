"""VepService -- the orchestration layer of vep-link.

Ties the pure pieces together into the pipeline the MCP tool layer drives:

    parse input -> recode (if HGVS/rsID) to canonical CHR-POS-REF-ALT
                -> VEP region POST -> extract -> normalized dict

Every public method returns plain dicts (or lists of dicts); response shaping
for MCP lives in a later layer. Build is always passed explicitly as a
:class:`~vep_link.models.enums.GenomeBuild`.

Caching: ``resolve``, ``annotate``, and ``recode`` are wrapped with
``observability.telemetry.telemetry_cache`` (an ``async_lru.alru_cache`` plus
miss/hit/coalesced classification) so identical calls within the TTL skip the
upstream client entirely. Because the cache keys on positional/keyword arguments
and dicts/lists are unhashable, each cached method delegates to a private
``_*_impl`` taking only hashable args (``build`` as its ``.value`` string,
options as a ``json.dumps(..., sort_keys=True)`` string, variant lists as
tuples); the impl reconstructs the rich types.

Batch annotation mirrors variant-linker's ``processBatchVariants``: inputs are
canonicalized independently, parse/resolution failures are collected (never
fail the whole batch), repeated inputs share one call, and the whole batch runs
under one deadline that ends before the hard per-call cap.
"""

from __future__ import annotations

import asyncio
import json

from vep_link.api.ensembl_client import EnsemblClient
from vep_link.config import Settings
from vep_link.exceptions import (
    AmbiguousMappingError,
    DataNotFoundError,
    EnsemblApiError,
    UnsupportedContigError,
    UpstreamInputError,
    UpstreamTimeoutError,
    VepLinkError,
)
from vep_link.mcp._sanitize import sanitize_message
from vep_link.mcp.errors import canonical_error_code
from vep_link.models.enums import GenomeBuild, InputKind
from vep_link.observability.telemetry import telemetry_cache
from vep_link.services._recoding import (
    aggregate_recode_entry,
    canonical_vcf_strings,
    project_recode_fields,
    vep_vcf_strings,
)
from vep_link.services.extraction import build_annotation
from vep_link.services.warnings import (
    multiple_alts_warning,
    ref_not_validated_warning,
    resolved_via_vep_hgvs_warning,
)
from vep_link.variant import (
    VariantInput,
    cnv_to_vep_line,
    coordinate_to_vep_line,
    needs_recoding,
    parse_variant_input,
)

# NB: batch per-input error classification is NOT duplicated here. It shares the
# SINGLE canonical classifier (vep_link.mcp.errors.canonical_error_code) with the
# MCP error boundary so batch and single-call paths map every exception type
# identically (a duplicated table is a fix-narrower-than-class trap: an earlier
# copy silently omitted DisallowedURLError / ResponseTooLargeError). See
# canonical_error_code and the shared _EXCEPTION_MAP.


# Fixed (no variant echo): both Ensembl HGVS resolvers failed; name a usable way out.
_BOTH_HGVS_PATHS_FAILED = (
    "Ensembl Variant Recoder and VEP's HGVS endpoint both failed for this HGVS. "
    "Retry shortly, or resolve it to coordinates elsewhere (in GeneFoundry: "
    "clinvar_get_variant with the HGVS as identifier) and pass CHR-POS-REF-ALT."
)

_BATCH_DEADLINE_MESSAGE = (
    "Batch deadline reached before this input was resolved; retry it alone or in a smaller batch."
)


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
        self._resolve_cached = telemetry_cache(self._resolve_impl, maxsize=maxsize, ttl=ttl)
        self._annotate_cached = telemetry_cache(self._annotate_impl, maxsize=maxsize, ttl=ttl)
        self._recode_cached = telemetry_cache(self._recode_impl, maxsize=maxsize, ttl=ttl)

    async def aclose(self) -> None:
        """Close the underlying Ensembl client."""
        await self._client.aclose()

    # -- canonicalization --------------------------------------------------

    async def _recode(self, vi: VariantInput, build: GenomeBuild) -> tuple[list[str], list[dict]]:
        """Recode an rsID/HGVS to its canonical alts, plus any resolution-path warning.

        When the Variant Recoder fails upstream for an HGVS input, VEP's own HGVS
        endpoint is tried before giving up (#111); the path is named in a warning.
        """
        if vi.kind is not InputKind.HGVS:
            return canonical_vcf_strings(await self._client.recoder_get(vi.value, build)), []
        # Recoder + fallback share ONE overall budget, so a call never outlasts a
        # plain recoder call (45 s); the fallback keeps at least its reserved slice.
        loop = asyncio.get_running_loop()
        budget_end = loop.time() + self._settings.OVERALL_DEADLINE_SECONDS
        reserve = self._settings.HGVS_FALLBACK_DEADLINE_SECONDS
        try:
            payload = await self._client.recoder_get(
                vi.value, build, deadline=self._settings.OVERALL_DEADLINE_SECONDS - reserve
            )
        except (EnsemblApiError, UpstreamTimeoutError) as exc:
            remaining = max(reserve, budget_end - loop.time())
            try:
                payload = await self._client.vep_hgvs_vcf(vi.value, build, deadline=remaining)
                alts = vep_vcf_strings(payload)
            except EnsemblApiError, UpstreamTimeoutError:
                raise type(exc)(_BOTH_HGVS_PATHS_FAILED) from exc
            if not alts:
                raise
            return alts, [resolved_via_vep_hgvs_warning()]
        return canonical_vcf_strings(payload), []

    async def _canonical_lines(
        self, variant: str, build: GenomeBuild, allele: str | None
    ) -> tuple[list[tuple[str, str]], list[dict]]:
        """All ``(canonical_id, vep_line)`` pairs for an input, plus path warnings.

        Coordinates/CNVs yield exactly one pair. A recoded rsID/HGVS yields one
        pair *per distinct ALT allele* (deterministically sorted), so a
        multi-allelic input is never silently collapsed to a single alt. When
        ``allele`` is given (an ALT base or full ``CHR-POS-REF-ALT``), the pairs
        are filtered to it. Raises :class:`DataNotFoundError` when nothing
        resolves (or nothing matches ``allele``).
        """
        vi = parse_variant_input(variant)
        path_warnings: list[dict] = []
        if vi.kind is InputKind.COORDINATE:
            pairs = [(vi.value, coordinate_to_vep_line(vi.value))]
        elif vi.kind is InputKind.CNV:
            pairs = [(vi.value, cnv_to_vep_line(vi.value))]
        elif needs_recoding(vi):
            canonicals, path_warnings = await self._recode(vi, build)
            if not canonicals:
                raise DataNotFoundError(f"Could not resolve {variant!r} to a genomic coordinate")
            pairs = [(c, coordinate_to_vep_line(c)) for c in canonicals]
        else:  # defensive: parse_variant_input only emits the four kinds above.
            raise DataNotFoundError(f"Could not resolve {variant!r} to a genomic coordinate")

        if allele is not None:
            pairs = [pair for pair in pairs if _allele_matches(pair[0], allele)]
            if not pairs:
                raise DataNotFoundError(f"No ALT allele matching {allele!r} for {variant!r}")
        return pairs, path_warnings

    # -- resolve -----------------------------------------------------------

    async def resolve(self, variant: str, build: GenomeBuild, *, allele: str | None = None) -> dict:
        """Resolve a variant to one minimal summary per ALT allele (cached)."""
        return await self._resolve_cached(variant, build.value, allele)

    async def _resolve_impl(self, variant: str, build_value: str, allele: str | None) -> dict:
        build = GenomeBuild(build_value)
        pairs, path_warnings = await self._canonical_lines(variant, build, allele)
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
            "warnings": _alt_warnings(pairs) + path_warnings,
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
        pairs, path_warnings = await self._canonical_lines(variant, build, allele)
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
            "warnings": _alt_warnings(pairs) + path_warnings,
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

        Each unique input is annotated exactly as ``annotate`` would (shared
        cache); repeated inputs reuse one call. A multi-allelic input yields one
        result row per ALT allele: ``summary.annotated`` counts inputs,
        ``summary.result_rows`` counts rows. Inputs unfinished at
        ``BATCH_DEADLINE_SECONDS`` come back as per-input ``upstream_timeout`` errors.
        Returns ``{"results", "errors", "summary"}``.
        """
        requested = len(variants)
        if requested > self._settings.BATCH_MAX:
            raise UpstreamInputError(
                f"too many variants: {requested} (max {self._settings.BATCH_MAX})"
            )

        # Each unique input runs its whole pipeline (recode -> VEP POST) as one
        # task under ONE batch deadline that ends before the hard per-call cap
        # (D3): an input still running then -- in any phase -- becomes a per-input
        # upstream_timeout and every finished input is still returned. Bounded to
        # the client's slot count so no task trips its queue-wait.
        slots = asyncio.Semaphore(max(1, self._settings.MAX_CONCURRENCY))

        async def annotate_one(original: str) -> dict:
            async with slots:
                return await self.annotate(original, build, vep_options=vep_options)

        tasks = {original: asyncio.create_task(annotate_one(original)) for original in variants}
        try:
            _done, pending = (
                await asyncio.wait(tasks.values(), timeout=self._settings.BATCH_DEADLINE_SECONDS)
                if tasks
                else (set(), set())
            )
        finally:  # deadline hit or caller cancelled: never leave work running
            for task in tasks.values():
                task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

        results: list[dict] = []
        errors: list[dict] = []
        annotated = 0
        for original in variants:  # duplicates fan out from their one shared task
            task = tasks[original]
            exc = (
                UpstreamTimeoutError(_BATCH_DEADLINE_MESSAGE)
                if task in pending
                else task.exception()
            )
            if isinstance(exc, VepLinkError):
                # Any known fault (parse, not-found, rate-limit, upstream, URL-guard
                # / response-cap refusal) is collected per-input with the SAME
                # canonical code + subcode the single-call boundary uses; never
                # abort the batch.
                code, subcode = canonical_error_code(exc)
                errors.append(self._batch_error(original, code, exc, subcode=subcode))
            elif exc is not None:  # last-resort: one bad input cannot crash the batch
                errors.append(self._batch_error(original, "internal", exc))
            else:
                annotated += 1
                # A multi-allelic input yields one result per ALT, never a silent pick.
                # Original input wins over the VEP-echoed region line.
                results.extend({**row, "input": original} for row in task.result()["variants"])

        return {
            "results": results,
            "errors": errors,
            "summary": {
                "requested": requested,
                # Inputs (not ALT rows) that got at least one annotation.
                "annotated": annotated,
                "failed": len(errors),
                # Rows in ``results``: a multi-allelic input contributes one per ALT.
                "result_rows": len(results),
            },
        }

    @staticmethod
    def _batch_error(
        original: str, code: str, exc: BaseException, *, subcode: str | None = None
    ) -> dict:
        """Shape a per-input batch failure record.

        This row rides inside an otherwise-successful batch response, so it
        bypasses the MCP error boundary (:func:`run_mcp_tool`) and its sanitation
        -- the per-item ``message`` is therefore made safe here directly. An
        ``internal`` error NEVER echoes ``str(exc)`` (it can carry internal detail
        / filesystem paths), matching the boundary's opaque-internal contract; a
        classified fault's server-authored message is stripped of the fence's
        forbidden control / zero-width / bidi / NUL code points. ``error_code`` is
        one of the closed six; a finer ``error_subcode`` rides additively.
        """
        message = (
            "Internal error while processing this input."
            if code == "internal"
            else sanitize_message(str(exc) or type(exc).__name__)
        )
        row: dict = {"input": original, "error_code": code, "message": message}
        if subcode:
            row["error_subcode"] = subcode
        return row

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
                aggregate_recode_entry(entry, input_override=variants[i] if aligned else None),
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
