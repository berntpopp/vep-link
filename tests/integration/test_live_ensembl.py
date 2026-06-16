"""Live integration tests against the real Ensembl REST API.

These are EXCLUDED from default CI: they are marked ``integration`` +
``allow_network`` (so the conftest no-network guard lets them through) and are
skipped entirely unless ``VEP_LINK_RUN_INTEGRATION`` is set in the environment.
They may rate-limit; run them deliberately with ``make test-integration``.
"""

from __future__ import annotations

import os

import pytest

from vep_link.api.ensembl_client import EnsemblClient
from vep_link.config import Settings
from vep_link.models.enums import GenomeBuild
from vep_link.services.vep_service import VepService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.allow_network,
    pytest.mark.skipif(
        not os.getenv("VEP_LINK_RUN_INTEGRATION"),
        reason="set VEP_LINK_RUN_INTEGRATION=1 to run live Ensembl tests",
    ),
]


@pytest.fixture
async def service() -> VepService:
    settings = Settings()
    svc = VepService(EnsemblClient(settings), settings)
    yield svc
    await svc.aclose()


async def test_resolve_rsid_grch38(service: VepService) -> None:
    # rs6025 = Factor V Leiden (F5 c.1601G>A), a stable, well-known variant.
    result = await service.resolve("rs6025", GenomeBuild.GRCH38)
    assert result["variant_id"].startswith("1-")
    assert result["most_severe_consequence"]


async def test_annotate_coordinate_grch38(service: VepService) -> None:
    result = await service.annotate("1-169549811-C-A", GenomeBuild.GRCH38, vep_options=None)
    assert result["variant_id"] == "1-169549811-C-A"
    assert result["most_severe_consequence"]
    assert result["transcript_consequences"]


async def test_recode_rsid(service: VepService) -> None:
    results = await service.recode(["rs6025"], GenomeBuild.GRCH38, fields=None)
    assert results
    assert results[0]["vcf_string"]


async def test_liftover_grch37_to_grch38(service: VepService) -> None:
    # A coordinate on GRCh37 lifted to GRCh38.
    result = await service.liftover("1-169519049-C-A", GenomeBuild.GRCH37, GenomeBuild.GRCH38)
    assert result["lifted"] is not None
    assert result["to_assembly"] == "GRCh38"
