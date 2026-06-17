"""Unit tests for :class:`vep_link.services.vep_service.VepService`.

The orchestration service is exercised against a hand-written fake
``EnsemblClient`` that records calls and returns canned fixtures (or raises).
Upstream HTTP is *not* mocked with respx here -- that is covered at the client
layer; these tests pin the service's parse -> recode -> VEP -> extract wiring,
its batch skip-and-collect-errors behavior, liftover guards, and that caching
prevents a second identical call from hitting the client again.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from tests.fixtures import (
    ASSEMBLY_MAP_AMBIGUOUS,
    ASSEMBLY_MAP_NONE,
    ASSEMBLY_MAP_ONE,
    RECODER_GET_RS123,
    RECODER_POST_BATCH,
    VEP_REGION_MISSENSE,
)
from vep_link.config import Settings
from vep_link.exceptions import (
    AmbiguousMappingError,
    DataNotFoundError,
    EnsemblApiError,
    RateLimitedError,
    UnsupportedContigError,
    UpstreamInputError,
)
from vep_link.models.enums import GenomeBuild
from vep_link.services import VepService

GRCH38 = GenomeBuild.GRCH38
GRCH37 = GenomeBuild.GRCH37


# --- Fake client ----------------------------------------------------------


class FakeEnsemblClient:
    """In-memory stand-in for ``EnsemblClient``.

    Each ``*_return`` attribute is the canned payload a method returns; setting
    a corresponding ``*_error`` makes that method raise instead. Call counts and
    argument captures let tests assert which upstream calls were (not) made.
    """

    def __init__(self) -> None:
        self.recoder_get_return: Any = deepcopy(RECODER_GET_RS123)
        self.recoder_post_return: list[dict[str, Any]] = deepcopy(RECODER_POST_BATCH)
        self.vep_region_post_return: list[dict[str, Any]] = deepcopy(VEP_REGION_MISSENSE)
        self.assembly_map_return: dict[str, Any] = deepcopy(ASSEMBLY_MAP_ONE)
        # Default None: "ref unknown" -> liftover keeps alleles (no downgrade).
        self.sequence_region_ref_return: str | None = None

        self.recoder_get_error: Exception | None = None
        self.recoder_post_error: Exception | None = None
        self.vep_region_post_error: Exception | None = None
        self.assembly_map_error: Exception | None = None
        self.sequence_region_ref_error: Exception | None = None

        self.recoder_get_calls: list[dict[str, Any]] = []
        self.recoder_post_calls: list[dict[str, Any]] = []
        self.vep_region_post_calls: list[dict[str, Any]] = []
        self.assembly_map_calls: list[dict[str, Any]] = []
        self.sequence_region_ref_calls: list[dict[str, Any]] = []

        self.closed = False

    async def recoder_get(
        self, variant: str, build: GenomeBuild, *, fields: str = "vcf_string"
    ) -> Any:
        self.recoder_get_calls.append({"variant": variant, "build": build, "fields": fields})
        if self.recoder_get_error:
            raise self.recoder_get_error
        return self.recoder_get_return

    async def recoder_post(
        self, ids: list[str], build: GenomeBuild, *, fields: str = "vcf_string"
    ) -> list[dict[str, Any]]:
        self.recoder_post_calls.append({"ids": ids, "build": build, "fields": fields})
        if self.recoder_post_error:
            raise self.recoder_post_error
        return self.recoder_post_return

    async def vep_region_post(
        self,
        lines: list[str],
        build: GenomeBuild,
        *,
        options: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        self.vep_region_post_calls.append(
            {"lines": list(lines), "build": build, "options": options}
        )
        if self.vep_region_post_error:
            raise self.vep_region_post_error
        return self.vep_region_post_return

    async def assembly_map(
        self, region: str, from_build: GenomeBuild, to_build: GenomeBuild
    ) -> dict[str, Any]:
        self.assembly_map_calls.append(
            {"region": region, "from_build": from_build, "to_build": to_build}
        )
        if self.assembly_map_error:
            raise self.assembly_map_error
        return self.assembly_map_return

    async def sequence_region_ref(self, chrom: str, pos: int, build: GenomeBuild) -> str | None:
        self.sequence_region_ref_calls.append({"chrom": chrom, "pos": pos, "build": build})
        if self.sequence_region_ref_error:
            raise self.sequence_region_ref_error
        return self.sequence_region_ref_return

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def client() -> FakeEnsemblClient:
    return FakeEnsemblClient()


@pytest.fixture
def service(client: FakeEnsemblClient, settings: Settings) -> VepService:
    return VepService(client, settings)  # type: ignore[arg-type]


# --- resolve --------------------------------------------------------------


def _vep_record(line: str, gene: str = "GENE1") -> dict[str, Any]:
    """A minimal VEP region record echoing ``line`` as its ``input``."""
    return {
        "input": line,
        "seq_region_name": "1",
        "most_severe_consequence": "missense_variant",
        "transcript_consequences": [
            {"gene_symbol": gene, "consequence_terms": ["missense_variant"], "canonical": 1}
        ],
    }


async def test_resolve_rsid_recodes_then_veps_and_returns_minimal_dict(
    service: VepService, client: FakeEnsemblClient
) -> None:
    # A single-alt rsID -> a variants[] of length 1, no warning.
    client.recoder_get_return = [
        {"id": "rs123", "input": "rs123", "A": {"vcf_string": ["1-1000-A-T"]}}
    ]
    result = await service.resolve("rs123", GRCH38)

    # rsID requires recoding before VEP.
    assert len(client.recoder_get_calls) == 1
    assert client.recoder_get_calls[0]["variant"] == "rs123"
    assert client.vep_region_post_calls[0]["lines"] == ["1 1000 . A T . . ."]
    assert result == {
        "query": "rs123",
        "assembly": "GRCh38",
        "variants": [
            {
                "variant_id": "1-1000-A-T",
                "assembly": "GRCh38",
                "gene_symbol": "GENE1",
                "most_severe_consequence": "missense_variant",
            }
        ],
        "warnings": [],
    }


async def test_resolve_returns_all_alts_with_warning(
    service: VepService, client: FakeEnsemblClient
) -> None:
    # The default rs123 fixture is bi-allelic (alts G and T); both are returned,
    # deterministically sorted, with a multiple_alts warning.
    client.vep_region_post_return = [
        _vep_record("1 1000 . A G . . ."),
        _vep_record("1 1000 . A T . . ."),
    ]
    out = await service.resolve("rs123", GRCH38)
    assert out["query"] == "rs123"
    assert [v["variant_id"] for v in out["variants"]] == ["1-1000-A-G", "1-1000-A-T"]
    assert out["warnings"][0]["code"] == "multiple_alts"
    assert out["warnings"][0]["context"]["count"] == 2


async def test_resolve_allele_filter_selects_one_alt(
    service: VepService, client: FakeEnsemblClient
) -> None:
    client.vep_region_post_return = [_vep_record("1 1000 . A T . . .")]
    out = await service.resolve("rs123", GRCH38, allele="T")
    assert [v["variant_id"] for v in out["variants"]] == ["1-1000-A-T"]
    assert out["warnings"] == []  # filtered to a single alt -> no ambiguity
    assert client.vep_region_post_calls[-1]["lines"] == ["1 1000 . A T . . ."]


async def test_resolve_allele_filter_no_match_raises(
    service: VepService, client: FakeEnsemblClient
) -> None:
    with pytest.raises(DataNotFoundError):
        await service.resolve("rs123", GRCH38, allele="C")


async def test_resolve_coordinate_skips_recoder(
    service: VepService, client: FakeEnsemblClient
) -> None:
    result = await service.resolve("1-1000-A-T", GRCH38)

    assert client.recoder_get_calls == []  # coordinate is already canonical
    assert len(client.vep_region_post_calls) == 1
    assert client.vep_region_post_calls[0]["lines"] == ["1 1000 . A T . . ."]
    assert result["query"] == "1-1000-A-T"
    assert result["variants"][0]["variant_id"] == "1-1000-A-T"
    assert result["variants"][0]["gene_symbol"] == "GENE1"
    assert result["warnings"] == []


async def test_resolve_empty_vep_raises_not_found(
    service: VepService, client: FakeEnsemblClient
) -> None:
    client.vep_region_post_return = []
    with pytest.raises(DataNotFoundError):
        await service.resolve("1-1000-A-T", GRCH38)


async def test_resolve_unresolvable_recoder_raises_not_found(
    service: VepService, client: FakeEnsemblClient
) -> None:
    # An HGVS whose recoder reply carries no vcf_string anywhere.
    client.recoder_get_return = [{"id": None, "input": "NM_x:c.1A>T", "A": {"hgvsg": []}}]
    with pytest.raises(DataNotFoundError):
        await service.resolve("NM_x:c.1A>T", GRCH38)


# --- annotate -------------------------------------------------------------


async def test_annotate_returns_full_normalized_dict(
    service: VepService, client: FakeEnsemblClient
) -> None:
    result = await service.annotate("1-1000-A-T", GRCH38)

    assert result["query"] == "1-1000-A-T"
    assert result["assembly"] == "GRCh38"
    assert result["warnings"] == []
    v = result["variants"][0]
    assert v["variant_id"] == "1-1000-A-T"
    assert v["assembly"] == "GRCh38"
    assert v["most_severe_consequence"] == "missense_variant"
    assert v["gene_symbol"] == "GENE1"
    # Full shape carries both transcript rows.
    assert len(v["transcript_consequences"]) == 2
    assert v["transcript_consequences"][0]["transcript_id"] == "ENST00000123456"
    assert v["frequencies"]  # gnomAD frequencies flattened


async def test_annotate_forwards_vep_options(
    service: VepService, client: FakeEnsemblClient
) -> None:
    await service.annotate("1-1000-A-T", GRCH38, vep_options={"SpliceAI": "1"})
    assert client.vep_region_post_calls[0]["options"] == {"SpliceAI": "1"}


async def test_annotate_empty_vep_raises_not_found(
    service: VepService, client: FakeEnsemblClient
) -> None:
    client.vep_region_post_return = []
    with pytest.raises(DataNotFoundError):
        await service.annotate("1-1000-A-T", GRCH38)


# --- annotate_batch -------------------------------------------------------


async def test_annotate_batch_skips_and_collects_errors(
    service: VepService, client: FakeEnsemblClient
) -> None:
    # One good coordinate + one HGVS the recoder cannot resolve (no vcf_string).
    client.recoder_get_return = [{"id": None, "input": "NM_x:c.1A>T", "A": {"hgvsg": []}}]
    result = await service.annotate_batch(["1-1000-A-T", "NM_x:c.1A>T"], GRCH38)

    assert len(result["results"]) == 1
    assert len(result["errors"]) == 1
    assert result["results"][0]["input"] == "1-1000-A-T"
    assert result["results"][0]["gene_symbol"] == "GENE1"
    assert result["errors"][0]["input"] == "NM_x:c.1A>T"
    assert result["errors"][0]["error_code"] == "not_found"
    assert result["summary"] == {"requested": 2, "annotated": 1, "failed": 1}


async def test_annotate_batch_parse_error_collected_as_invalid_input(
    service: VepService, client: FakeEnsemblClient
) -> None:
    result = await service.annotate_batch(["1-1000-A-T", "   "], GRCH38)
    assert len(result["results"]) == 1
    assert len(result["errors"]) == 1
    assert result["errors"][0]["error_code"] == "invalid_input"
    assert result["summary"]["failed"] == 1


async def test_annotate_batch_enforces_batch_max(
    settings: Settings, client: FakeEnsemblClient
) -> None:
    small = settings.model_copy(update={"BATCH_MAX": 2})
    svc = VepService(client, small)  # type: ignore[arg-type]
    with pytest.raises(UpstreamInputError):
        await svc.annotate_batch(["1-1-A-T", "2-2-A-T", "3-3-A-T"], GRCH38)


async def test_annotate_batch_dedups_identical_inputs(
    service: VepService, client: FakeEnsemblClient
) -> None:
    result = await service.annotate_batch(["1-1000-A-T", "1-1000-A-T"], GRCH38)
    # Both inputs map to one unique vep_line; one record annotates both.
    assert client.vep_region_post_calls[0]["lines"] == ["1 1000 . A T . . ."]
    assert len(result["results"]) == 2
    assert result["summary"] == {"requested": 2, "annotated": 2, "failed": 0}


async def test_annotate_batch_never_raises_on_bad_input(
    service: VepService, client: FakeEnsemblClient
) -> None:
    client.recoder_get_return = [{"id": None, "input": "bad", "A": {"hgvsg": []}}]
    # All inputs fail; batch returns empty results, no exception.
    result = await service.annotate_batch(["junkhgvs:c.1A>T"], GRCH38)
    assert result["results"] == []
    assert len(result["errors"]) == 1


async def test_annotate_batch_collects_upstream_fault_without_aborting(
    service: VepService, client: FakeEnsemblClient
) -> None:
    # An rsID whose recoder call faults with an UPSTREAM error (not a parse/
    # not-found) must be collected per-input, not abort the whole batch -- and a
    # good coordinate alongside it must still reach the VEP stage.
    client.recoder_get_error = EnsemblApiError("recoder 502")
    result = await service.annotate_batch(["1-1000-A-T", "rs6025"], GRCH38)
    codes = {e["error_code"] for e in result["errors"]}
    assert codes == {"upstream_unavailable"}
    assert result["summary"]["failed"] == 1
    assert result["summary"]["requested"] == 2
    # The good coordinate still flowed to the VEP region POST (batch not aborted).
    assert client.vep_region_post_calls


async def test_annotate_batch_maps_rate_limit_per_input(
    service: VepService, client: FakeEnsemblClient
) -> None:
    client.recoder_get_error = RateLimitedError("429")
    result = await service.annotate_batch(["rs6025"], GRCH38)
    assert result["errors"][0]["error_code"] == "rate_limited"
    assert result["summary"]["failed"] == 1


# --- recode ---------------------------------------------------------------


async def test_recode_aggregates_vcf_strings(
    service: VepService, client: FakeEnsemblClient
) -> None:
    result = await service.recode(["rs123", "NM_004006.2:c.4375C>T"], GRCH38)

    assert len(result) == 2
    assert result[0]["input"] == "rs123"
    assert result[0]["id"] == "rs123"
    assert "1-1000-A-T" in result[0]["vcf_string"]
    assert "1-1000-A-G" in result[0]["vcf_string"]
    assert result[1]["input"] == "NM_004006.2:c.4375C>T"
    assert result[1]["vcf_string"] == ["X-32389644-G-A"]
    # hgvsg aggregated across alleles too.
    assert "NC_000001.11:g.1000A>T" in result[0]["hgvsg"]


async def test_recode_default_fields(service: VepService, client: FakeEnsemblClient) -> None:
    # Omitting `fields` must request the FULL representation set, not vcf_string
    # only: pass "" so the recoder returns its default (all fields).
    await service.recode(["rs123"], GRCH38)
    assert client.recoder_post_calls[0]["fields"] == ""


async def test_recode_explicit_fields_passthrough(
    service: VepService, client: FakeEnsemblClient
) -> None:
    await service.recode(["rs123"], GRCH38, fields="hgvsg,spdi")
    assert client.recoder_post_calls[0]["fields"] == "hgvsg,spdi"


# --- liftover -------------------------------------------------------------


async def test_liftover_single_mapping(service: VepService, client: FakeEnsemblClient) -> None:
    result = await service.liftover("1-1000-A-T", GRCH37, GRCH38)

    assert client.assembly_map_calls[0]["region"] == "1:1000..1000"
    assert client.assembly_map_calls[0]["from_build"] == GRCH37
    assert result["lifted"] == "1-1064-A-T"
    assert result["from_assembly"] == "GRCh37"
    assert result["to_assembly"] == "GRCh38"
    assert result["input"] == "1-1000-A-T"
    assert result["mapped_region"] == "1:1064"
    assert result["warnings"] == []


async def test_liftover_keeps_alleles_when_ref_matches(
    service: VepService, client: FakeEnsemblClient
) -> None:
    # Target-assembly REF equals the carried REF (A) -> full CHR-POS-REF-ALT, no warning.
    client.sequence_region_ref_return = "A"
    result = await service.liftover("1-1000-A-T", GRCH37, GRCH38)
    assert result["lifted"] == "1-1064-A-T"
    assert result["warnings"] == []
    # The lifted locus (target build) was the one validated.
    assert client.sequence_region_ref_calls[-1] == {"chrom": "1", "pos": 1064, "build": GRCH38}


async def test_liftover_drops_alleles_and_warns_on_ref_mismatch(
    service: VepService, client: FakeEnsemblClient
) -> None:
    # Target-assembly REF (C) disagrees with the carried REF (A): the alleles are
    # unverifiable, so return coordinate-only + a ref_not_validated warning.
    client.sequence_region_ref_return = "C"
    result = await service.liftover("1-1000-A-T", GRCH37, GRCH38)
    assert result["lifted"] == "1-1064"  # coordinate-only
    assert result["warnings"][0]["code"] == "ref_not_validated"
    assert result["warnings"][0]["context"] == {"expected_ref": "C", "carried_ref": "A"}


async def test_liftover_skips_validation_when_disabled(
    client: FakeEnsemblClient, settings: Settings
) -> None:
    no_validate = settings.model_copy(update={"LIFTOVER_VALIDATE_REF": False})
    svc = VepService(client, no_validate)  # type: ignore[arg-type]
    client.sequence_region_ref_return = "C"  # would mismatch, but validation is off
    result = await svc.liftover("1-1000-A-T", GRCH37, GRCH38)
    assert result["lifted"] == "1-1064-A-T"
    assert result["warnings"] == []
    assert client.sequence_region_ref_calls == []  # not even queried


async def test_liftover_no_mapping_raises_not_found(
    service: VepService, client: FakeEnsemblClient
) -> None:
    client.assembly_map_return = deepcopy(ASSEMBLY_MAP_NONE)
    with pytest.raises(DataNotFoundError):
        await service.liftover("1-1000-A-T", GRCH37, GRCH38)


async def test_liftover_ambiguous_mapping_raises(
    service: VepService, client: FakeEnsemblClient
) -> None:
    client.assembly_map_return = deepcopy(ASSEMBLY_MAP_AMBIGUOUS)
    with pytest.raises(AmbiguousMappingError):
        await service.liftover("1-1000-A-T", GRCH37, GRCH38)


async def test_liftover_rsid_raises_unsupported(
    service: VepService, client: FakeEnsemblClient
) -> None:
    with pytest.raises(UnsupportedContigError):
        await service.liftover("rs123", GRCH37, GRCH38)
    assert client.assembly_map_calls == []


async def test_liftover_hgvs_raises_unsupported(
    service: VepService, client: FakeEnsemblClient
) -> None:
    with pytest.raises(UnsupportedContigError):
        await service.liftover("NM_004006.2:c.4375C>T", GRCH37, GRCH38)


# --- caching --------------------------------------------------------------


async def test_resolve_is_cached(service: VepService, client: FakeEnsemblClient) -> None:
    first = await service.resolve("1-1000-A-T", GRCH38)
    second = await service.resolve("1-1000-A-T", GRCH38)
    assert first == second
    # Second identical call must be served from cache (client untouched).
    assert len(client.vep_region_post_calls) == 1


async def test_annotate_is_cached(service: VepService, client: FakeEnsemblClient) -> None:
    await service.annotate("1-1000-A-T", GRCH38, vep_options={"CADD": "1"})
    await service.annotate("1-1000-A-T", GRCH38, vep_options={"CADD": "1"})
    assert len(client.vep_region_post_calls) == 1


async def test_recode_is_cached(service: VepService, client: FakeEnsemblClient) -> None:
    await service.recode(["rs123"], GRCH38)
    await service.recode(["rs123"], GRCH38)
    assert len(client.recoder_post_calls) == 1


async def test_cache_distinguishes_options(service: VepService, client: FakeEnsemblClient) -> None:
    await service.annotate("1-1000-A-T", GRCH38, vep_options={"CADD": "1"})
    await service.annotate("1-1000-A-T", GRCH38, vep_options={"SpliceAI": "1"})
    # Different options -> different cache keys -> two upstream calls.
    assert len(client.vep_region_post_calls) == 2


# --- aclose ---------------------------------------------------------------


async def test_aclose_closes_client(service: VepService, client: FakeEnsemblClient) -> None:
    await service.aclose()
    assert client.closed is True
